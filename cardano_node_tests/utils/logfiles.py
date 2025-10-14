import contextlib
import dataclasses
import fnmatch
import io
import itertools
import logging
import os
import pathlib as pl
import re
import time
import typing as tp
from collections import deque

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import framework_log
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)

BUFFER_SIZE = 512 * 1024  # 512 KB buffer
ROTATED_RE = re.compile(r".+\.[0-9]+")  # Detect rotated log file
# NOTE: The regex needs to be unanchored.
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

# Relevant errors from supervisord.log
# NOTE: The regex needs to be unanchored.
SUPERVISORD_ERRORS_RE = re.compile("not expected|FATAL", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class RotableLog:
    logfile: pl.Path
    seek: int
    timestamp: float


def _get_rotated_logs(
    logfile: pl.Path, *, seek: int = 0, timestamp: float = 0.0
) -> list[RotableLog]:
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
    try:
        with open(offset_file, encoding="utf-8") as infile:
            return int(infile.readline().strip())
    except Exception:
        return 0


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


def _resume_at_line_boundary(fb: tp.BinaryIO, pos: int, size: int) -> None:
    if pos <= 0:
        fb.seek(0, io.SEEK_SET)
        return
    if pos >= size:
        fb.seek(size, io.SEEK_SET)
        return

    fb.seek(pos - 1, io.SEEK_SET)
    prev = fb.read(1)
    if prev in (b"\n", b"\r"):
        # If we're exactly between CRLF, consume the '\n' so we start at the next line.
        fb.seek(pos, io.SEEK_SET)
        nxt = fb.read(1)
        if prev == b"\r" and nxt == b"\n":
            # We were at CR|LF — already consumed the '\n', good.
            return
        # Otherwise we moved forward; rewind one byte to pos
        fb.seek(pos, io.SEEK_SET)
        return

    # In the middle of a line: skip to next newline
    fb.readline()


def _split_complete_lines(buf: bytes) -> tuple[list[bytes], bytes]:
    """Return (complete_lines_without_newline, leftover_tail). Handles LF/CRLF/CR."""
    parts = buf.splitlines(keepends=True)
    if not parts:
        return [], b""
    # Determine if the last piece ends with a newline
    last = parts[-1]
    complete = parts if last.endswith((b"\n", b"\r")) else parts[:-1]
    leftover = b"" if complete is parts else last

    # Strip line endings
    out: list[bytes] = []
    for ln in complete:
        stripped_ln = ln
        if stripped_ln.endswith(b"\n"):
            stripped_ln = stripped_ln[:-1]
        if stripped_ln.endswith(b"\r"):
            stripped_ln = stripped_ln[:-1]
        out.append(stripped_ln)
    return out, leftover


def _bytes_flags(flags: int) -> int:
    """Remove flags that are invalid for bytes patterns."""
    return flags & ~(re.UNICODE | re.LOCALE)


def _compile_bytes_from_pattern(
    pat: re.Pattern[str] | None, *, encoding: str = "utf-8"
) -> re.Pattern[bytes] | None:
    """Compile a *string* regex as a *bytes* regex, sanitizing flags.

    Note: IGNORECASE on bytes is ASCII-only.
    """
    if pat is None:
        return None
    bpat = pat.pattern.encode(encoding)
    bflags = _bytes_flags(pat.flags)
    return re.compile(bpat, bflags)


def _compile_bytes_from_str(
    pat: str, *, flags: int = 0, encoding: str = "utf-8"
) -> re.Pattern[bytes]:
    """Compile a *string* regex as a *bytes* regex, sanitizing flags.

    Note: IGNORECASE on bytes is ASCII-only.
    """
    return re.compile(pat.encode(encoding), _bytes_flags(flags))


def _compile_look_back_map_bytes(
    m: dict[str, str] | None, *, flags: int = 0, encoding: str = "utf-8"
) -> list[tuple[re.Pattern[bytes], re.Pattern[bytes]]]:
    """Compile {error_regex: preceding_regex} to bytes regex pairs (flags sanitized)."""
    if not m:
        return []
    bflags = _bytes_flags(flags)
    return [
        (re.compile(err.encode(encoding), bflags), re.compile(prev.encode(encoding), bflags))
        for err, prev in m.items()
    ]


def _should_ignore_error(
    line_b: bytes, lookback: deque[bytes], pairs: list[tuple[re.Pattern[bytes], re.Pattern[bytes]]]
) -> bool:
    """Return True if line matches an 'error' key and a preceding regex is in the look-back."""
    for err_pat_b, prev_pat_b in pairs:
        if err_pat_b.search(line_b):
            # Found a mapped error; require a preceding match in the buffer
            return any(prev_pat_b.search(prev_b) for prev_b in lookback)
    return False


def _validated_start(seek: int | None, size: int) -> int:
    """Return a safe starting byte offset.

    - If seek is None, negative, or beyond EOF -> start at 0
    - Otherwise -> start at seek
    """
    if seek is None or seek < 0 or seek > size:
        return 0
    return seek


def _search_log_lines(  # noqa: C901
    logfile: pl.Path,
    rotated_logs: list[RotableLog],
    errors_re: re.Pattern[str],  # The the error regex needs to be unanchored
    *,
    errors_ignored_re: re.Pattern[str] | None = None,
    look_back_map: dict[str, str] | None = None,
    look_back_lines: int = 10,
    encoding: str = "utf-8",
) -> list[tuple[pl.Path, str]]:
    """Search for error lines, ignoring mapped errors when a preceding message appears.

    - Uses binary I/O + byte offsets for correctness and speed.
    - Decodes only matched lines for output.
    - Persists a byte offset at a line boundary for the live logfile.
    """
    errs_b = _compile_bytes_from_pattern(pat=errors_re, encoding=encoding)
    if not errs_b:
        return []
    ign_b = _compile_bytes_from_pattern(pat=errors_ignored_re, encoding=encoding)
    lb_pairs = _compile_look_back_map_bytes(
        m=look_back_map, flags=errors_re.flags, encoding=encoding
    )

    results: list[tuple[pl.Path, str]] = []
    last_offset_bytes = -1

    for rec in rotated_logs:
        path = rec.logfile
        size = path.stat().st_size
        start = _validated_start(seek=rec.seek, size=size)

        with path.open("rb", buffering=BUFFER_SIZE) as fb:
            _resume_at_line_boundary(fb=fb, pos=start, size=size)

            look_back: deque[bytes] = deque(maxlen=look_back_lines)
            leftover = b""

            while True:
                chunk = fb.read(BUFFER_SIZE)
                if not chunk:
                    break

                buf = leftover + chunk

                # Fast prefilter: is there *any* error token in this chunk?
                # Safe only when the error regex is unanchored.
                has_error_in_chunk = bool(errs_b.search(buf))

                lines_b, leftover = _split_complete_lines(buf=buf)

                if not has_error_in_chunk:
                    # No error candidates: just update look-back (cheap) and continue.
                    # (Deque will keep only the last look_back_lines lines.)
                    for ln in lines_b:
                        look_back.append(ln)
                    continue

                for line_b in lines_b:
                    # Fast ignore
                    if ign_b and ign_b.search(line_b):
                        look_back.append(line_b)
                        continue
                    # Not an error -> just update context
                    if not errs_b.search(line_b):
                        look_back.append(line_b)
                        continue
                    # Error: maybe ignore based on mapping
                    if lb_pairs and _should_ignore_error(
                        line_b=line_b, lookback=look_back, pairs=lb_pairs
                    ):
                        look_back.append(line_b)
                        continue

                    look_back.append(line_b)

                    # Report: decode only now
                    line = line_b.decode(encoding, errors="surrogateescape")
                    results.append((path, line))

            # Persist next offset for the "live" logfile at a line boundary
            if path == logfile:
                cur = fb.tell()
                last_offset_bytes = cur - len(leftover)

    if last_offset_bytes >= 0:
        offset_file = _get_offset_file(logfile=logfile)
        offset_file.write_text(str(last_offset_bytes), encoding="utf-8")

    return results


def add_ignore_rule(
    *, files_glob: str, regex: str, ignore_file_id: str, skip_after: float = 0.0
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
    *,
    regex: str,
    logfile: pl.Path,
    seek_offset: int,
    timestamp: float,
    only_first: bool = False,
    encoding: str = "utf-8",
) -> list[str]:
    """Find messages in log using a *byte* seek offset (binary mode).

    Reads in binary for speed, resumes at a line boundary near `seek_offset`,
    matches with a bytes regex, and decodes only matching lines.

    Args:
        regex: Regular expression (interpreted as UTF-8 when compiled to bytes).
        logfile: Path to the primary log file.
        seek_offset: Byte offset to resume from (start of a line or 0).
        timestamp: Timestamp used by `_get_rotated_logs(...)`.
        only_first: If True, return immediately after the first match.
        encoding: Text encoding used to decode matched lines (default: "utf-8").

    Returns:
        Matching log lines (decoded to str, without trailing newlines).
    """
    # Compile as BYTES regex for speed; note: \w/\b are ASCII-only in bytes mode.
    regex_b = _compile_bytes_from_str(pat=regex)

    lines_found: list[str] = []

    for logfile_rec in _get_rotated_logs(
        logfile=logfile,
        seek=seek_offset,
        timestamp=timestamp,
    ):
        path = logfile_rec.logfile
        size = path.stat().st_size
        start = _validated_start(seek=logfile_rec.seek, size=size)

        with path.open("rb", buffering=BUFFER_SIZE) as fb:
            _resume_at_line_boundary(fb=fb, pos=start, size=size)

            leftover = b""
            while True:
                chunk = fb.read(BUFFER_SIZE)
                if not chunk:
                    break

                buf = leftover + chunk
                lines_b, leftover = _split_complete_lines(buf=buf)

                for line_b in lines_b:
                    if regex_b.search(line_b):
                        line = line_b.decode(encoding, errors="surrogateescape")
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
    """Check if the expected messages are present in logs (byte offsets, binary I/O).

    Args:
        regex_pairs: (glob, regex) pairs.
        seek_offsets: Mapping of absolute log path (str) -> byte offset.
        state_dir: Root directory for globs.
        timestamp: Passed to `_get_rotated_logs`.

    Returns:
        Error messages for missing entries.
    """
    errors: list[str] = []

    for files_glob, regex in regex_pairs:
        # Compile to BYTES regex for speed; note: \w/\b are ASCII-only in bytes mode.
        regex_b = _compile_bytes_from_str(pat=regex)

        # Get list of candidate files by globbing keys of seek_offsets
        pattern = f"{state_dir}/{files_glob}"
        matching_files = fnmatch.filter(seek_offsets, pattern)

        for logfile in matching_files:
            # Skip rotated file names here; `_get_rotated_logs` will include them appropriately.
            if ROTATED_RE.match(logfile):
                continue

            start_seek = seek_offsets.get(logfile) or 0
            line_found = False

            for rec in _get_rotated_logs(
                logfile=pl.Path(logfile),
                seek=start_seek,
                timestamp=timestamp,
            ):
                path = rec.logfile
                size = path.stat().st_size
                pos = _validated_start(seek=rec.seek, size=size)

                with path.open("rb", buffering=BUFFER_SIZE) as fb:
                    _resume_at_line_boundary(fb=fb, pos=pos, size=size)

                    leftover = b""
                    while True:
                        chunk = fb.read(BUFFER_SIZE)
                        if not chunk:
                            break

                        buf = leftover + chunk
                        lines_b, leftover = _split_complete_lines(buf=buf)

                        for line_b in lines_b:
                            if regex_b.search(line_b):
                                line_found = True
                                break
                        if line_found:
                            break

                if line_found:
                    break

            if not line_found:
                errors.append(f"No line matching `{regex}` found in '{logfile}'.")

    return errors


@contextlib.contextmanager
def expect_errors(regex_pairs: list[tuple[str, str]], *, worker_id: str) -> tp.Iterator[None]:
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
                    look_back_map=ERRORS_LOOK_BACK_MAP,
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


def clean_ignore_rules(*, ignore_file_id: str) -> None:
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
