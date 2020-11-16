import contextlib
import fnmatch
import itertools
import logging
import re
from pathlib import Path
from typing import Generator
from typing import List
from typing import Tuple

import pytest

from cardano_node_tests.utils import devops_cluster
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

ERRORS_RE = re.compile(":error:|failed|failure", re.IGNORECASE)
ERRORS_IGNORED = ["failedScripts", "EKGServerStartupError", "WithIPList SubscriptionTrace"]
ERRORS_RULES_FILE_NAME = ".errors_rules"


def add_ignore_rule(files_glob: str, regex: str) -> None:
    with helpers.FileLockIfXdist(f"{helpers.TEST_TEMP_DIR}/ignore_rules.lock"):
        cluster_env = devops_cluster.get_cluster_env()
        state_dir = cluster_env["state_dir"]
        rules_file = state_dir / ERRORS_RULES_FILE_NAME
        with open(rules_file, "a") as infile:
            infile.write(f"{files_glob};;{regex}\n")


@contextlib.contextmanager
def expect_errors(regex_pair: List[Tuple[str, str]]) -> Generator:
    cluster_env = devops_cluster.get_cluster_env()
    state_dir = cluster_env["state_dir"]
    glob_list = []
    for files_glob, regex in regex_pair:
        add_ignore_rule(files_glob, regex)
        glob_list.append(files_glob)
    _expanded_paths = [list(state_dir.glob(glob_item)) for glob_item in glob_list]
    expanded_paths = list(itertools.chain.from_iterable(_expanded_paths))
    positions = {str(p): helpers.get_last_line_position(p) for p in expanded_paths}

    yield

    for files_glob, regex in regex_pair:
        regex_comp = re.compile(regex)
        matching_files = fnmatch.filter(positions, f"{state_dir}/{files_glob}")
        for mfile in matching_files:
            with open(mfile) as infile:
                seek_pos = positions.get(mfile) or 0
                infile.seek(seek_pos)
                for line in infile:
                    if regex_comp.search(line):
                        break
                else:
                    raise AssertionError(
                        f"No line matching `{regex}` found in '{mfile}' after position {seek_pos}"
                    )


def _get_lastpos(fpath: Path) -> int:
    if fpath.exists():
        with open(fpath) as infile:
            return int(infile.readline().strip())
    return 0


def get_ignore_rules(rules_file: Path) -> List[Tuple[str, str]]:
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
    for fpath in state_dir.glob("*.std*"):
        if fpath.name.endswith(".lastpos"):
            continue
        lastpos_file = fpath.parent / f".{fpath.name}.lastpos"
        last_position = _get_lastpos(lastpos_file)

        errors_ignored_re = re.compile(
            get_ignore_regex(ignore_rules=ignore_rules, regexes=ERRORS_IGNORED, logfile=fpath)
        )

        cur_position = 0
        with open(fpath) as infile:
            infile.seek(last_position)
            for line in infile:
                if ERRORS_RE.search(line) and not errors_ignored_re.search(line):
                    errors.append((fpath, line))
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
