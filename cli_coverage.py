#!/usr/bin/env python3
import argparse
import copy
import json
import subprocess
from pathlib import Path
from typing import List

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import UnpackableSequence


def get_args(args=None):
    """Get command line arguments."""
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
        "-a", "--all", action="store_true", help="Output all results, not only uncovered",
    )
    return parser.parse_args(args)


def merge_dicts(dict_a: dict, dict_b: dict) -> dict:
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
        elif key not in dict_a or not isinstance(value, dict):
            dict_a[key] = value
        else:
            merge_dicts(dict_a[key], value)

    return dict_a


def cli(cli_args: UnpackableSequence) -> str:
    """Run the `cardano-cli` command."""
    p = subprocess.Popen(cli_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    __, stderr = p.communicate()
    return stderr.decode()


def parse_cmd_output(output: str) -> List[str]:
    """Parse cardano-cli command output, return sub-commands and options names."""
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
    """Get coverage result by merging available data."""
    coverage_dict = copy.deepcopy(available_commands)
    for in_json in input_jsons:
        with open(in_json) as infile:
            coverage = json.load(infile)
        coverage_dict = merge_dicts(coverage_dict, coverage)

    return coverage_dict


def get_uncovered(coverage: dict) -> dict:
    """Filter out only uncovered commands/arguments."""
    uncovered_db: dict = {}
    for key, value in coverage.items():
        if key == "_count":
            continue
        if len(value) != 1:
            ret_db = get_uncovered(value)
            if ret_db:
                uncovered_db[key] = ret_db
                continue
        if value["_count"] == 0:
            uncovered_db[key] = False

    return uncovered_db


def main():
    args = get_args()

    available_commands = {
        "cardano-cli": {"_count": 0, "shelley": get_available_commands(["cardano-cli", "shelley"])}
    }
    coverage = get_coverage(args.input_files, available_commands)

    out_file_content = coverage if args.all else get_uncovered(coverage)
    helpers.write_json(args.output_file, out_file_content)


if __name__ == "__main__":
    main()
