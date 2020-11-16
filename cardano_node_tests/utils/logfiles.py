import contextlib
import fnmatch
import itertools
import logging
import os
import re
import time
from pathlib import Path
from typing import Generator
from typing import List
from typing import NamedTuple
from typing import Tuple

import pytest

from cardano_node_tests.utils import devops_cluster
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

ROTATED_RE = re.compile(r".+\.[0-9]+")  # detect rotated log file
ERRORS_RE = re.compile(":error:|failed|failure", re.IGNORECASE)
ERRORS_IGNORED = ["failedScripts", "EKGServerStartupError", "WithIPList SubscriptionTrace"]
ERRORS_RULES_FILE_NAME = ".errors_rules"


class RotableLog(NamedTuple):
    logfile: Path
    seek: int
    timestamp: float


def get_rotated_logs(logfile: Path, seek: int = 0, timestamp: float = 0.0) -> List[RotableLog]:
    """Return list of versions of the log file (list of `RotableLog`).

    When the seek position was recorded for a log file and the log file was rotated,
    the seek position now belongs to the rotated file and the "live" log file has seek position 0.
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


def add_ignore_rule(files_glob: str, regex: str) -> None:
    """Add ignore rule for expected errors."""
    with helpers.FileLockIfXdist(f"{helpers.TEST_TEMP_DIR}/ignore_rules.lock"):
        cluster_env = devops_cluster.get_cluster_env()
        state_dir = cluster_env["state_dir"]
        rules_file = state_dir / ERRORS_RULES_FILE_NAME
        with open(rules_file, "a") as infile:
            infile.write(f"{files_glob};;{regex}\n")


@contextlib.contextmanager
def expect_errors(regex_pair: List[Tuple[str, str]]) -> Generator:
    """Make sure expected errors are present in logs.

    Args:
        regex_pair: (glob, regex) - regex that needs to be present in files described by the glob
    """
    cluster_env = devops_cluster.get_cluster_env()
    state_dir = cluster_env["state_dir"]

    glob_list = []
    for files_glob, regex in regex_pair:
        add_ignore_rule(files_glob, regex)  # don't report errors that are expected
        glob_list.append(files_glob)
    # resolve the globs
    _expanded_paths = [list(state_dir.glob(glob_item)) for glob_item in glob_list]
    # flatten the list
    expanded_paths = list(itertools.chain.from_iterable(_expanded_paths))
    # record each end-of-file as a starting position for searching the log file
    seek_positions = {str(p): helpers.get_last_line_position(p) for p in expanded_paths}

    timestamp = time.time()

    yield

    for files_glob, regex in regex_pair:
        regex_comp = re.compile(regex)
        # get list of records (file names and positions) for given glob
        matching_files = fnmatch.filter(seek_positions, f"{state_dir}/{files_glob}")
        for logfile in matching_files:
            # skip if the log file is rotated log, it will be handled by `get_rotated_logs`
            if ROTATED_RE.match(logfile):
                continue

            # search for the expected error
            seek = seek_positions.get(logfile) or 0
            line_found = False
            for logfile_rec in get_rotated_logs(
                logfile=Path(logfile), seek=seek, timestamp=timestamp
            ):
                with open(logfile_rec.logfile) as infile:
                    infile.seek(seek)
                    for line in infile:
                        if regex_comp.search(line):
                            line_found = True
                            break
                if line_found:
                    break
            else:
                raise AssertionError(f"No line matching `{regex}` found in '{logfile}'.")


def _get_seek(fpath: Path) -> int:
    with open(fpath) as infile:
        return int(infile.readline().strip())


def get_ignore_rules(rules_file: Path) -> List[Tuple[str, str]]:
    """Get rules (file glob and regex) for ignored errors."""
    rules: List[Tuple[str, str]] = []

    if not rules_file.exists():
        return rules

    with open(rules_file) as infile:
        for line in infile:
            if ";;" not in line:
                continue
            files_glob, regex = line.split(";;")
            rules.append((files_glob, regex.rstrip("\n")))

    return rules


def get_ignore_regex(ignore_rules: List[Tuple[str, str]], regexes: List[str], logfile: Path) -> str:
    """Combine together regex for the given log file using file specific and global ignore rules."""
    regex_list = regexes[:]
    for record in ignore_rules:
        files_glob, regex = record
        if fnmatch.filter([logfile.name], files_glob):
            regex_list.append(regex)
    return "|".join(regex_list)


def search_cluster_artifacts() -> List[Tuple[Path, str]]:
    """Search cluster artifacts for errors."""
    cluster_env = devops_cluster.get_cluster_env()
    state_dir = cluster_env["state_dir"]
    rules_file = state_dir / ERRORS_RULES_FILE_NAME

    with helpers.FileLockIfXdist(f"{helpers.TEST_TEMP_DIR}/ignore_rules.lock"):
        ignore_rules = get_ignore_rules(rules_file)

    errors = []
    for logfile in state_dir.glob("*.std*"):
        # skip if the log file is status file or rotated log
        if logfile.name.endswith(".lastpos") or ROTATED_RE.match(logfile.name):
            continue

        # read seek position (from where to start searching) and timestamp of last search
        lastpos_file = logfile.parent / f".{logfile.name}.lastpos"
        if lastpos_file.exists():
            seek = _get_seek(lastpos_file)
            timestamp = os.path.getmtime(lastpos_file)
        else:
            seek = 0
            timestamp = 0.0

        errors_ignored_re = re.compile(
            get_ignore_regex(ignore_rules=ignore_rules, regexes=ERRORS_IGNORED, logfile=logfile)
        )

        cur_position = 0
        for logfile_rec in get_rotated_logs(logfile=logfile, seek=seek, timestamp=timestamp):
            with open(logfile_rec.logfile) as infile:
                infile.seek(seek)
                for line in infile:
                    if ERRORS_RE.search(line) and not errors_ignored_re.search(line):
                        errors.append((logfile, line))
                # record position only for the "live" log file, rotated versions are just archives
                if logfile_rec.logfile == logfile:
                    cur_position = infile.tell()

        if cur_position:
            with open(lastpos_file, "w") as outfile:
                outfile.write(str(cur_position))

    return errors


def report_artifacts_errors(errors: List[Tuple[Path, str]]) -> None:
    """Report errors found in artifacts."""
    err = [f"{e[0]}: {e[1]}" for e in errors]
    err_joined = "\n".join(err)
    pytest.fail(f"Errors found in cluster log files:\n{err_joined}")
