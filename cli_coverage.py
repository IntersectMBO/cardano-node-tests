#!/usr/bin/env python3
"""Generate coverage report for `cardano-cli` sub-commands and options."""
import argparse
import copy
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import List

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import UnpackableSequence

LOGGER = logging.getLogger(__name__)


def get_args(args=None):
    """Get script command line arguments."""
    parser = argparse.ArgumentParser(description="cli-coverage")
    parser.add_argument(
        "-i",
        "--input-files",
        required=True,
        nargs="+",
        type=helpers.check_file_arg,
        help="Path to coverage files",
    )
    parser.add_argument(
        "-o", "--output-file", required=True, help="File where to save coverage results",
    )
    parser.add_argument(
        "-u", "--uncovered-only", action="store_true", help="Report only uncovered arguments",
    )
    return parser.parse_args(args)


def merge_coverage(dict_a: dict, dict_b: dict) -> dict:
    """Merge dict_b into dict_a."""
    if not (isinstance(dict_a, dict) and isinstance(dict_b, dict)):
        return dict_a

    mergeable = (list, set, tuple)
    addable = (int, float)
    for key, value in dict_b.items():
        if key in dict_a and isinstance(value, mergeable) and isinstance(dict_a[key], mergeable):
            new_list = set(dict_a[key]).union(value)
            dict_a[key] = sorted(new_list)
        elif key in dict_a and isinstance(value, addable) and isinstance(dict_a[key], addable):
            dict_a[key] += value
        # there shouldn't be any argument that is not in the available commands dict
        elif key not in dict_a:
            continue
        elif not isinstance(value, dict):
            dict_a[key] = value
        else:
            merge_coverage(dict_a[key], value)

    return dict_a


def cli(cli_args: UnpackableSequence) -> str:
    """Run the `cardano-cli` command."""
    p = subprocess.Popen(cli_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    __, stderr = p.communicate()
    return stderr.decode()


def parse_cmd_output(output: str) -> List[str]:
    """Parse `cardano-cli` command output, return sub-commands and options names."""
    section_start = False
    cli_args = []
    for line in output.splitlines():
        if "Available " in line:
            section_start = True
            continue
        if section_start:
            # skip line with wrapped description from previous command
            if line.startswith("    "):
                continue
            line = line.strip()
            if not line:
                continue
            item = line.split(" ")[0]
            cli_args.append(item)

    return cli_args


def get_available_commands(cli_args: UnpackableSequence) -> dict:
    """Get all available cardano-cli sub-commands and options."""
    cli_out = cli(cli_args)
    new_cli_args = parse_cmd_output(cli_out)
    command_dict: dict = {"_count": 0}
    for arg in new_cli_args:
        if arg.startswith("-"):
            command_dict[arg] = {"_count": 0}
            continue
        command_dict[arg] = get_available_commands([*cli_args, arg])

    return command_dict


def get_coverage(input_jsons: List[Path], available_commands: dict) -> dict:
    """Get coverage info by merging available data."""
    coverage_dict = copy.deepcopy(available_commands)
    for in_json in input_jsons:
        with open(in_json) as infile:
            coverage = json.load(infile)
        if coverage.get("cardano-cli", {}).get("_count") is None:
            raise AttributeError(
                f"Data in '{in_json}' doesn't seem to be in proper coverage format."
            )
        coverage_dict = merge_coverage(coverage_dict, coverage)

    return coverage_dict


def get_report(arg_name: str, coverage: dict, uncovered_only: bool = False) -> dict:
    """Generate coverage report."""
    uncovered_db: dict = {}
    for key, value in coverage.items():
        if key == "_count":
            continue
        if len(value) != 1:
            ret_db = get_report(key, value, uncovered_only=uncovered_only)
            if ret_db:
                uncovered_db[key] = ret_db
                continue
        count = value["_count"]
        if count == 0:
            uncovered_db[key] = 0
        elif not uncovered_only:
            uncovered_db[key] = count

    if uncovered_db and "_count" in coverage:
        uncovered_db[f"_count_{arg_name}"] = coverage["_count"]

    return uncovered_db


def main() -> int:
    args = get_args()

    available_commands = {
        "cardano-cli": {"_count": 0, "shelley": get_available_commands(["cardano-cli", "shelley"])}
    }
    try:
        coverage = get_coverage(args.input_files, available_commands)
    except AttributeError as exc:
        LOGGER.error(str(exc))
        return 1

    report = get_report("cardano-cli", coverage, uncovered_only=args.uncovered_only)
    helpers.write_json(args.output_file, report)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        format="%(name)s:%(levelname)s:%(message)s", level=logging.INFO,
    )
    sys.exit(main())
