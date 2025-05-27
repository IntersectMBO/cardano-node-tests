import contextlib
import dataclasses
import fnmatch
import itertools
import logging
import os
import pathlib as pl
import re
import time
import typing as tp

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import framework_log
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)

BUFFER_SIZE = 512 * 1024  # 512 KB buffer
ROTATED_RE = re.compile(r".+\.[0-9]+")  # detect rotated log file
ERRORS_RE = re.compile("error|fail", re.IGNORECASE)
ERRORS_IGNORE_FILE_NAME = ".errors_to_ignore"

ERRORS_IGNORED = [
    r"cardano\.node\.[^:]+:Debug:",
    r"cardano\.node\.[^:]+:Info:",
    "db-sync-node:Info:",
    "cardano-tx-submit:Info:",
    "Event: LedgerUpdate",
    "trace.*ErrorPolicy",
    "ErrorPolicySuspendConsumer",
    "Connection Attempt Exception",
    "EKGServerStartupError",
    "ExceededTimeLimit",
    "Failed to start all required subscriptions",
    "TraceDidntAdoptBlock",
    "failedScripts",
    "closed when reading data, waiting on next header",
    "IOException writev: resource vanished",
    r"IOException Network\.Socket\.recvBuf: resource vanished",
    # Can happen when single postgres instance is used for multiple db-sync services
    "db-sync-node.*could not serialize access",
    # Can happen on p2p when node is shutting down
    "AsyncCancelled",
    # DB-Sync table name
    "OffChainPoolFetchError",
    # DB-Sync table name
    "OffChainVoteFetchError",
    # TODO: p2p failures on testnet
    "PeerStatusChangeFailure",
    # TODO: p2p failures on testnet - PeerMonitoringError
    "DeactivationTimeout",
    # TODO: p2p failures on testnet
    "PeerMonitoringError .* MuxError",
    # Harmless when whole network is shutting down
    "SubscriberWorkerCancelled, .*SubscriptionWorker exiting",
    # TODO: see node issue https://github.com/IntersectMBO/cardano-node/issues/5312
    "DiffusionError thread killed",
    # TODO: connection to other node before the other node is started
    r"AcquireConnectionError Network\.Socket\.connect",
    "expected change in the serialization format",
]
# Already removed from the list above:
# * Workaround for node issue https://github.com/IntersectMBO/cardano-node/issues/4369
#   "MAIN THREAD FAILED"

if (os.environ.get("GITHUB_ACTIONS") or "").lower() == "true":
    # We sometimes see this error on CI. It seems time is not synced properly on GitHub runners.
    ERRORS_IGNORED.append("TraceBlockFromFuture")

if cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.TESTNET:
    ERRORS_IGNORED.extend(
        (
            # We can get this error when some clients are old, or are using wrong
            # network magic.
            "TrHandshakeClientError",
            "TracePromoteWarmBigLedgerPeerAborted",
        )
    )

# Errors that are ignored if there are expected messages in the log file before the error
ERRORS_LOOK_BACK_LINES = 10
ERRORS_LOOK_BACK_MAP = {
    "TraceNoLedgerState": "Switched to a fork",  # can happen when chain switched to a fork
}
ERRORS_LOOK_BACK_RE = (
    re.compile("|".join(ERRORS_LOOK_BACK_MAP.keys())) if ERRORS_LOOK_BACK_MAP else None
)

# Relevant errors from supervisord.log
SUPERVISORD_ERRORS_RE = re.compile("not expected|FATAL", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class RotableLog:
    logfile: pl.Path
    seek: int
    timestamp: float


def _look_back_found(buffer: list[str]) -> bool:
    """Look back to the buffer to see if there is an expected message.

    If the expected message is found, the error can be ignored.
    """
    # Find the look back regex that corresponds to the error message
    err_line = buffer[-1]
    look_back_re = ""
    for err_re, look_re in ERRORS_LOOK_BACK_MAP.items():
        if re.search(err_re, err_line):
            look_back_re = look_re
            break
    else:
        msg = f"Look back regex not found for error line: {err_line}"
        raise KeyError(msg)

    # Check if the look back regex matches any of the previous log messages
    return any(re.search(look_back_re, line) for line in buffer[:-1])


def _get_rotated_logs(logfile: pl.Path, seek: int = 0, timestamp: float = 0.0) -> list[RotableLog]:
    """Return list of versions of the log file (list of `RotableLog`).

    When the seek offset was recorded for a log file and the log file was rotated,
    the seek offset now belongs to the rotated file and the "live" log file has seek offset 0.
    """
    # Get logfile including rotated versions
    logfiles = list(logfile.parent.glob(f"{logfile.name}*"))

    # Get list of logfiles modified after `timestamp`, sorted by their last modification time
    # from oldest to newest.
    _logfile_records = [
        RotableLog(logfile=f, seek=0, timestamp=f.stat().st_mtime) for f in logfiles
    ]
    _logfile_records = [r for r in _logfile_records if r.timestamp > timestamp]
    logfile_records = sorted(_logfile_records, key=lambda r: r.timestamp)

    if not logfile_records:
        return []

    # The `seek` value belongs to the log file with modification time furthest in the past
    logfile_records[0] = dataclasses.replace(logfile_records[0], seek=seek)

    return logfile_records


def _get_ignore_rules_lock_file(instance_num: int) -> pl.Path:
    """Return path to the lock file for ignored errors rules."""
    return temptools.get_basetemp() / f"{ERRORS_IGNORE_FILE_NAME}_{instance_num}.lock"


def _get_ignore_rules(
    cluster_env: cluster_nodes.ClusterEnv, timestamp: float
) -> list[tuple[str, str]]:
    """Get rules (file glob and regex) for ignored errors."""
    rules: list[tuple[str, str]] = []
    lock_file = _get_ignore_rules_lock_file(instance_num=cluster_env.instance_num)

    with locking.FileLockIfXdist(lock_file):
        for rules_file in cluster_env.state_dir.glob(f"{ERRORS_IGNORE_FILE_NAME}_*"):
            with open(rules_file, encoding="utf-8") as infile:
                for line in infile:
                    if ";;" not in line:
                        continue
                    files_glob, skip_after_str, regex = line.split(";;")
                    skip_after = float(skip_after_str)
                    # Skip the rule if it is expired. The `timestamp` is the time of the last log
                    # search, so the expire time is compared to the time of the last log check.
                    if 0 < skip_after < timestamp:
                        continue
                    rules.append((files_glob, regex.rstrip("\n")))

    return rules


def _get_offset_file(logfile: pl.Path) -> pl.Path:
    """Return path to the file that stores the seek offset for the given log file."""
    return logfile.parent / f".{logfile.name}.offset"


def _read_seek(offset_file: pl.Path) -> int:
    """Read seek offset from the given file."""
    with open(offset_file, encoding="utf-8") as infile:
        return int(infile.readline().strip())


def _get_ignore_regex(
    ignore_rules: list[tuple[str, str]], regexes: list[str], logfile: pl.Path
) -> str:
    """Combine together regex for the given log file using file specific and global ignore rules."""
    regex_set = set(regexes)
    for record in ignore_rules:
        files_glob, regex = record
        if fnmatch.filter([logfile.name], files_glob):
            regex_set.add(regex)
    return "|".join(regex_set) or "nothing_to_ignore"


def _search_log_lines(  # noqa: C901
    logfile: pl.Path,
    rotated_logs: list[RotableLog],
    errors_re: re.Pattern,
    errors_ignored_re: re.Pattern | None = None,
    errors_look_back_re: re.Pattern | None = None,
) -> list[tuple[pl.Path, str]]:
    """Search for errors in the log file and, if needed, in the corresponding rotated logs."""
    errors = []
    last_line_pos = -1

    for logfile_rec in rotated_logs:
        with open(logfile_rec.logfile, encoding="utf-8") as infile:
            # Avoid seeking past the end of file
            if 0 < logfile_rec.seek <= logfile_rec.logfile.stat().st_size:
                # Seek to the byte that comes right before the recorded offset
                infile.seek(logfile_rec.seek - 1)
                # Check if the byte is a newline, which means that the offset starts at
                # the beginning of a line.
                if infile.read(1) not in ("\n", "\r"):
                    # Skip the first line if the line is incomplete
                    infile.readline()

            look_back_buf = [""] * ERRORS_LOOK_BACK_LINES
            incomplete_line = ""

            # Read the file in chunks
            while chunk := infile.read(BUFFER_SIZE):
                # Prepend any leftover from the last chunk
                if incomplete_line:
                    chunk = incomplete_line + chunk
                lines = chunk.splitlines(keepends=False)

                # Handle incomplete lines at the end of the chunk
                incomplete_line = lines.pop() if chunk[-1] not in "\n\r" else ""

                for line in lines:
                    look_back_buf.append(line)
                    look_back_buf.pop(0)
                    if errors_re.search(line) and not (
                        errors_ignored_re and errors_ignored_re.search(line)
                    ):
                        # Skip if expected message is in the look back buffer
                        if (
                            errors_look_back_re
                            and errors_look_back_re.search(line)
                            and _look_back_found(look_back_buf)
                        ):
                            continue
                        errors.append((logfile, line))

            # Get offset for the "live" log file
            if logfile_rec.logfile == logfile:
                if incomplete_line:
                    # Adjust the offset to point before the incomplete line
                    last_line_pos = infile.tell() - len(incomplete_line.encode("utf-8"))
                else:
                    last_line_pos = infile.tell()

    # Record last search offset for the "live" log file
    if last_line_pos >= 0:
        offset_file = _get_offset_file(logfile=logfile)
        with open(offset_file, "w", encoding="utf-8") as outfile:
            outfile.write(str(last_line_pos))

    return errors


def add_ignore_rule(
    files_glob: str, regex: str, ignore_file_id: str, skip_after: float = 0.0
) -> None:
    """Add ignore rule for expected errors.

    Args:
        files_glob: A glob matching files that the `regex` should apply to.
        regex: A regex that should be ignored.
        ignore_file_id: The id of a ignore file the ignore rule will be added to.

            NOTE: When `ignore_file_id` matches pytest-xdist worker id (the `worker_id` fixture),
            the rule will be deleted during the test teardown.

        skip_after: The time in seconds after which the rule will expire. This is to avoid
            reporting the ignored errors in subsequent tests. It can take several seconds for the
            errors to appear in log files and we don't want to wait for them after each test.

            NOTE: The rule will expire **only** when there are no yet to be searched log messages
            that were created before the `skip_after` time.
    """
    cluster_env = cluster_nodes.get_cluster_env()
    rules_file = cluster_env.state_dir / f"{ERRORS_IGNORE_FILE_NAME}_{ignore_file_id}"
    lock_file = _get_ignore_rules_lock_file(instance_num=cluster_env.instance_num)

    with locking.FileLockIfXdist(lock_file), open(rules_file, "a", encoding="utf-8") as infile:
        infile.write(f"{files_glob};;{skip_after};;{regex}\n")


def find_msgs_in_logs(
    regex: str,
    logfile: pl.Path,
    seek_offset: int,
    timestamp: float,
    only_first: bool = False,
) -> list[str]:
    """Find messages in log.

    Args:
        regex (str): The regular expression to search for.
        logfile (Path): The path to the log file.
        seek_offset (int): The seek offset in the log file.
        timestamp (float): The timestamp to filter log entries.
        only_first (bool): Whether to return only the first match.

    Returns:
        list[str]: A list of matching log lines.
    """
    regex_comp = re.compile(regex)
    lines_found = []
    for logfile_rec in _get_rotated_logs(logfile=logfile, seek=seek_offset, timestamp=timestamp):
        with open(logfile_rec.logfile, encoding="utf-8") as infile:
            infile.seek(logfile_rec.seek)

            # Read the file in chunks
            incomplete_line = ""
            while chunk := infile.read(BUFFER_SIZE):
                # Prepend any leftover from the last chunk
                if incomplete_line:
                    chunk = incomplete_line + chunk
                lines = chunk.splitlines(keepends=False)

                # Handle incomplete lines at the end of the chunk
                incomplete_line = lines.pop() if chunk[-1] not in "\n\r" else ""

                for line in lines:
                    if regex_comp.search(line):
                        lines_found.append(line)
                        if only_first:
                            return lines_found
    return lines_found


def check_msgs_presence_in_logs(  # noqa: C901
    regex_pairs: list[tuple[str, str]],
    seek_offsets: dict[str, int],
    state_dir: pl.Path,
    timestamp: float,
) -> list[str]:
    """Check if the expected messages are present in logs.

    Args:
        regex_pairs (list[tuple[str, str]]): List of tuples with file globs and regex patterns.
        seek_offsets (dict[str, int]): Dictionary of file seek offsets.
        state_dir (Path): Path to the state directory.
        timestamp (float): Timestamp to filter log entries.

    Returns:
        list[str]: List of error messages for missing log entries.
    """
    errors = []
    for files_glob, regex in regex_pairs:
        regex_comp = re.compile(regex)
        # Get list of records (file names and offsets) for given glob
        matching_files = fnmatch.filter(seek_offsets, f"{state_dir}/{files_glob}")
        for logfile in matching_files:
            # Skip if the log file is rotated log, it will be handled by `_get_rotated_logs`
            if ROTATED_RE.match(logfile):
                continue

            # Search for the expected string
            seek = seek_offsets.get(logfile) or 0
            line_found = False
            for logfile_rec in _get_rotated_logs(
                logfile=pl.Path(logfile), seek=seek, timestamp=timestamp
            ):
                with open(logfile_rec.logfile, encoding="utf-8") as infile:
                    infile.seek(logfile_rec.seek)

                    # Read the file in chunks
                    incomplete_line = ""
                    while chunk := infile.read(BUFFER_SIZE):
                        # Prepend any leftover from the last chunk
                        if incomplete_line:
                            chunk = incomplete_line + chunk
                        lines = chunk.splitlines(keepends=False)

                        # Handle incomplete lines at the end of the chunk
                        incomplete_line = lines.pop() if chunk[-1] not in "\n\r" else ""

                        for line in lines:
                            if regex_comp.search(line):
                                line_found = True
                                break
                        if line_found:
                            break
                if line_found:
                    break
            else:
                errors.append(f"No line matching `{regex}` found in '{logfile}'.")

    return errors


@contextlib.contextmanager
def expect_errors(regex_pairs: list[tuple[str, str]], worker_id: str) -> tp.Iterator[None]:
    """Make sure the expected errors are present in logs.

    Context manager.

    Args:
        regex_pairs: [(glob, regex)] - A list of regexes matching strings that need to be present
            in files described by the glob.
        worker_id: The id of the pytest-xdist worker (the `worker_id` fixture) that the test
            is running on.
    """
    state_dir = cluster_nodes.get_cluster_env().state_dir

    glob_list = []
    for files_glob, regex in regex_pairs:
        add_ignore_rule(files_glob=files_glob, regex=regex, ignore_file_id=worker_id)
        glob_list.append(files_glob)
    # Resolve the globs
    _expanded_paths = [list(state_dir.glob(glob_item)) for glob_item in glob_list]
    # Flatten the list
    expanded_paths = list(itertools.chain.from_iterable(_expanded_paths))
    # Record each end-of-file as a starting offset for searching the log file
    seek_offsets = {str(p): helpers.get_eof_offset(p) for p in expanded_paths}

    timestamp = time.time()

    yield

    errors = check_msgs_presence_in_logs(
        regex_pairs=regex_pairs, seek_offsets=seek_offsets, state_dir=state_dir, timestamp=timestamp
    )
    if errors:
        errors_joined = "\n".join(errors)
        raise AssertionError(errors_joined) from None


@contextlib.contextmanager
def expect_messages(regex_pairs: list[tuple[str, str]]) -> tp.Iterator[None]:
    """Make sure the expected messages are present in logs.

    Context manager.

    Args:
        regex_pairs: [(glob, regex)] - A list of regexes matching strings that need to be present
            in files described by the glob.
    """
    state_dir = cluster_nodes.get_cluster_env().state_dir

    glob_list = [r[0] for r in regex_pairs]
    # Resolve the globs
    _expanded_paths = [list(state_dir.glob(glob_item)) for glob_item in glob_list]
    # Flatten the list
    expanded_paths = list(itertools.chain.from_iterable(_expanded_paths))
    # Record each end-of-file as a starting offset for searching the log file
    seek_offsets = {str(p): helpers.get_eof_offset(p) for p in expanded_paths}

    timestamp = time.time()

    yield

    errors = check_msgs_presence_in_logs(
        regex_pairs=regex_pairs, seek_offsets=seek_offsets, state_dir=state_dir, timestamp=timestamp
    )
    if errors:
        errors_joined = "\n".join(errors)
        raise AssertionError(errors_joined) from None


def search_cluster_logs() -> list[tuple[pl.Path, str]]:
    """Search cluster logs for errors."""
    cluster_env = cluster_nodes.get_cluster_env()
    lock_file = temptools.get_basetemp() / f"search_cluster_{cluster_env.instance_num}.lock"

    with locking.FileLockIfXdist(lock_file):
        errors = []
        for logfile in cluster_env.state_dir.glob("*.std*"):
            # Skip if the log file is status file or rotated log
            if logfile.name.endswith(".offset") or ROTATED_RE.match(logfile.name):
                continue

            # Get seek offset (from where to start searching) and timestamp of last search
            offset_file = _get_offset_file(logfile=logfile)
            if offset_file.exists():
                seek = _read_seek(offset_file=offset_file)
                timestamp = offset_file.stat().st_mtime
            else:
                seek = 0
                timestamp = 0.0

            # Get ignore rules for the log file
            ignore_rules = _get_ignore_rules(
                cluster_env=cluster_env, timestamp=timestamp or time.time()
            )
            errors_ignored = _get_ignore_regex(
                ignore_rules=ignore_rules, regexes=ERRORS_IGNORED, logfile=logfile
            )

            # Search for errors in the log file
            errors.extend(
                _search_log_lines(
                    logfile=logfile,
                    rotated_logs=_get_rotated_logs(logfile=logfile, seek=seek, timestamp=timestamp),
                    errors_re=ERRORS_RE,
                    errors_ignored_re=re.compile(errors_ignored),
                    errors_look_back_re=ERRORS_LOOK_BACK_RE,
                )
            )

    return errors


def search_framework_log() -> list[tuple[pl.Path, str]]:
    """Search framework log for errors."""
    # It is not necessary to lock the `framework.log` file because there is one log file per worker.
    # Each worker is checking only its own log file.
    logfile = framework_log.get_framework_log_path()
    errors = []

    # Get seek offset (from where to start searching) and timestamp of last search
    offset_file = _get_offset_file(logfile=logfile)
    if offset_file.exists():
        seek = _read_seek(offset_file=offset_file)
        timestamp = offset_file.stat().st_mtime
    else:
        seek = 0
        timestamp = 0.0

    # Search for errors in the log file
    errors.extend(
        _search_log_lines(
            logfile=logfile,
            rotated_logs=_get_rotated_logs(logfile=logfile, seek=seek, timestamp=timestamp),
            errors_re=ERRORS_RE,
        )
    )

    return errors


def search_supervisord_logs() -> list[tuple[pl.Path, str]]:
    """Search cluster logs for errors."""
    cluster_env = cluster_nodes.get_cluster_env()
    lock_file = temptools.get_basetemp() / f"search_supervisord_{cluster_env.instance_num}.lock"

    with locking.FileLockIfXdist(lock_file):
        logfile = cluster_env.state_dir / "supervisord.log"

        # Get seek offset (from where to start searching) and timestamp of last search
        offset_file = _get_offset_file(logfile=logfile)
        if offset_file.exists():
            seek = _read_seek(offset_file=offset_file)
            timestamp = offset_file.stat().st_mtime
        else:
            seek = 0
            timestamp = 0.0

        # Search for errors in the log file
        errors = _search_log_lines(
            logfile=logfile,
            rotated_logs=_get_rotated_logs(logfile=logfile, seek=seek, timestamp=timestamp),
            errors_re=SUPERVISORD_ERRORS_RE,
        )

    return errors


def clean_ignore_rules(ignore_file_id: str) -> None:
    """Cleanup relevant ignore rules file.

    Delete ignore file identified by `ignore_file_id` when it is no longer valid.
    """
    cluster_env = cluster_nodes.get_cluster_env()
    rules_file = cluster_env.state_dir / f"{ERRORS_IGNORE_FILE_NAME}_{ignore_file_id}"
    lock_file = _get_ignore_rules_lock_file(instance_num=cluster_env.instance_num)

    with locking.FileLockIfXdist(lock_file):
        rules_file.unlink(missing_ok=True)


def get_logfiles_errors() -> str:
    """Get errors found in cluster artifacts."""
    errors = search_cluster_logs()
    errors.extend(search_framework_log())
    errors.extend(search_supervisord_logs())
    if not errors:
        return ""

    err = [f"{e[0]}: {e[1]}" for e in errors]
    err_joined = "\n".join(err)
    return err_joined
