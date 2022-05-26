# pylint: disable=abstract-class-instantiated
import contextlib
import fnmatch
import itertools
import logging
import os
import re
import time
from pathlib import Path
from typing import Iterator
from typing import List
from typing import NamedTuple
from typing import Tuple

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

ROTATED_RE = re.compile(r".+\.[0-9]+")  # detect rotated log file
ERRORS_RE = re.compile(":error:|failed|failure", re.IGNORECASE)
ERRORS_IGNORED = [
    "Connection Attempt Exception",
    "EKGServerStartupError",
    "ExceededTimeLimit",
    "Failed to start all required subscriptions",
    "TraceDidntAdoptBlock",
    "failedScripts",
    "closed when reading data, waiting on next header",
    "MuxIOException writev: resource vanished",
    r"MuxIOException Network\.Socket\.recvBuf: resource vanished",
    "db-sync-node:.* AsyncCancelled",
    # can happen when single postgres instance is used for multiple db-sync services
    "db-sync-node:.*could not serialize access",
    # can happen on p2p when local roots are not up yet
    r"PeerSelection:Info:.* TracePromoteColdFailed .* Network\.Socket\.connect:",
    # can happen on p2p when node is shutting down
    "TrInboundGovernorError AsyncCancelled",
    # harmless when whole network is shutting down
    "SubscriberWorkerCancelled, .*SubscriptionWorker exiting",
]
if VERSIONS.cluster_era >= VERSIONS.ALONZO:
    ERRORS_IGNORED.append(r"cardano\.node\.Mempool:Info")
ERRORS_IGNORE_FILE_NAME = ".errors_to_ignore"


class RotableLog(NamedTuple):
    logfile: Path
    seek: int
    timestamp: float


def get_rotated_logs(logfile: Path, seek: int = 0, timestamp: float = 0.0) -> List[RotableLog]:
    """Return list of versions of the log file (list of `RotableLog`).

    When the seek offset was recorded for a log file and the log file was rotated,
    the seek offset now belongs to the rotated file and the "live" log file has seek offset 0.
    """
    # get logfile including rotated versions
    logfiles = list(logfile.parent.glob(f"{logfile.name}*"))

    # get list of logfiles modified after `timestamp`, sorted by their last modification time
    # from oldest to newest
    _logfile_records = [
        RotableLog(logfile=f, seek=0, timestamp=os.path.getmtime(f)) for f in logfiles
    ]
    _logfile_records = [r for r in _logfile_records if r.timestamp > timestamp]
    logfile_records = sorted(_logfile_records, key=lambda r: r.timestamp, reverse=True)

    if not logfile_records:
        return []

    # the `seek` value belongs to the log file with modification time furthest in the past
    oldest_record = logfile_records[0]
    oldest_record = oldest_record._replace(seek=seek)
    logfile_records[0] = oldest_record

    return logfile_records


def add_ignore_rule(files_glob: str, regex: str, ignore_file_id: str) -> None:
    """Add ignore rule for expected errors."""
    cluster_env = cluster_nodes.get_cluster_env()
    rules_file = cluster_env.state_dir / f"{ERRORS_IGNORE_FILE_NAME}_{ignore_file_id}"
    basetemp = temptools.get_basetemp()

    with locking.FileLockIfXdist(f"{basetemp}/ignore_rules_{cluster_env.instance_num}.lock"), open(
        rules_file, "a", encoding="utf-8"
    ) as infile:
        infile.write(f"{files_glob};;{regex}\n")


def get_ignore_rules() -> List[Tuple[str, str]]:
    """Get rules (file glob and regex) for ignored errors."""
    rules: List[Tuple[str, str]] = []
    cluster_env = cluster_nodes.get_cluster_env()
    basetemp = temptools.get_basetemp()

    with locking.FileLockIfXdist(f"{basetemp}/ignore_rules_{cluster_env.instance_num}.lock"):
        for rules_file in cluster_env.state_dir.glob(f"{ERRORS_IGNORE_FILE_NAME}_*"):
            with open(rules_file, encoding="utf-8") as infile:
                for line in infile:
                    if ";;" not in line:
                        continue
                    files_glob, regex = line.split(";;")
                    rules.append((files_glob, regex.rstrip("\n")))

    return rules


@contextlib.contextmanager
def expect_errors(regex_pairs: List[Tuple[str, str]], ignore_file_id: str) -> Iterator[None]:
    """Make sure the expected errors are present in logs.

    Args:
        regex_pairs: [(glob, regex)] - A list of regexes that need to be present in files
            described by the glob.
        ignore_file_id: The id of a ignore file the expected error will be added to.
    """
    state_dir = cluster_nodes.get_cluster_env().state_dir

    glob_list = []
    for files_glob, regex in regex_pairs:
        add_ignore_rule(files_glob=files_glob, regex=regex, ignore_file_id=ignore_file_id)
        glob_list.append(files_glob)
    # resolve the globs
    _expanded_paths = [list(state_dir.glob(glob_item)) for glob_item in glob_list]
    # flatten the list
    expanded_paths = list(itertools.chain.from_iterable(_expanded_paths))
    # record each end-of-file as a starting offset for searching the log file
    seek_offsets = {str(p): helpers.get_eof_offset(p) for p in expanded_paths}

    timestamp = time.time()

    yield

    errors = []
    for files_glob, regex in regex_pairs:
        regex_comp = re.compile(regex)
        # get list of records (file names and offsets) for given glob
        matching_files = fnmatch.filter(seek_offsets, f"{state_dir}/{files_glob}")
        for logfile in matching_files:
            # skip if the log file is rotated log, it will be handled by `get_rotated_logs`
            if ROTATED_RE.match(logfile):
                continue

            # search for the expected error
            seek = seek_offsets.get(logfile) or 0
            line_found = False
            for logfile_rec in get_rotated_logs(
                logfile=Path(logfile), seek=seek, timestamp=timestamp
            ):
                with open(logfile_rec.logfile, encoding="utf-8") as infile:
                    infile.seek(seek)
                    for line in infile:
                        if regex_comp.search(line):
                            line_found = True
                            break
                if line_found:
                    break
            else:
                errors.append(f"No line matching `{regex}` found in '{logfile}'.")

    if errors:
        errors_joined = "\n".join(errors)
        raise AssertionError(errors_joined) from None


def _get_seek(fpath: Path) -> int:
    with open(fpath, encoding="utf-8") as infile:
        return int(infile.readline().strip())


def get_ignore_regex(ignore_rules: List[Tuple[str, str]], regexes: List[str], logfile: Path) -> str:
    """Combine together regex for the given log file using file specific and global ignore rules."""
    regex_set = set(regexes)
    for record in ignore_rules:
        files_glob, regex = record
        if fnmatch.filter([logfile.name], files_glob):
            regex_set.add(regex)
    return "|".join(regex_set)


def search_cluster_artifacts() -> List[Tuple[Path, str]]:
    """Search cluster artifacts for errors."""
    ignore_rules = get_ignore_rules()
    cluster_env = cluster_nodes.get_cluster_env()

    errors = []
    for logfile in cluster_env.state_dir.glob("*.std*"):
        # skip if the log file is status file or rotated log
        if logfile.name.endswith(".offset") or ROTATED_RE.match(logfile.name):
            continue

        # read seek offset (from where to start searching) and timestamp of last search
        offset_file = logfile.parent / f".{logfile.name}.offset"
        if offset_file.exists():
            seek = _get_seek(offset_file)
            timestamp = os.path.getmtime(offset_file)
        else:
            seek = 0
            timestamp = 0.0

        errors_ignored = get_ignore_regex(
            ignore_rules=ignore_rules, regexes=ERRORS_IGNORED, logfile=logfile
        )
        errors_ignored_re = re.compile(errors_ignored)

        # record offset for the "live" log file
        with open(offset_file, "w", encoding="utf-8") as outfile:
            outfile.write(str(helpers.get_eof_offset(logfile)))

        for logfile_rec in get_rotated_logs(logfile=logfile, seek=seek, timestamp=timestamp):
            with open(logfile_rec.logfile, encoding="utf-8") as infile:
                infile.seek(seek)
                for line in infile:
                    if ERRORS_RE.search(line) and not (
                        errors_ignored and errors_ignored_re.search(line)
                    ):
                        errors.append((logfile, line))

    return errors


def search_and_clean(ignore_file_id: str) -> List[Tuple[Path, str]]:
    """Search cluster artifacts for errors and cleanup relevant ignore file when finished.

    After performing the search, delete ignore file identified by `ignore_file_id` as it is no
    longer valid.
    Use lock, so no other `search_and_clean` can run on another worker and report the same errors.
    """
    cluster_env = cluster_nodes.get_cluster_env()
    basetemp = temptools.get_basetemp()

    errors = []
    with locking.FileLockIfXdist(f"{basetemp}/search_clean_{cluster_env.instance_num}.lock"):
        # search for errors in cluster logfiles
        errors = search_cluster_artifacts()

        # There's only one test running on a worker at a time. Deleting the coresponding rules
        # file right after a test is finished is therefore safe. The effect is that the rules
        # apply only from the time they were added (by `logfiles.add_ignore_rule`) until the end
        # of the test.
        rules_file = cluster_env.state_dir / f"{ERRORS_IGNORE_FILE_NAME}_{ignore_file_id}"
        rules_file.unlink(missing_ok=True)

    return errors


def report_artifacts_errors(errors: List[Tuple[Path, str]]) -> None:
    """Report errors found in artifacts."""
    err = [f"{e[0]}: {e[1]}" for e in errors]
    err_joined = "\n".join(err)
    raise AssertionError(f"Errors found in cluster log files:\n{err_joined}") from None
