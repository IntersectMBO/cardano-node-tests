#!/usr/bin/env python3
import argparse
import json
import subprocess

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
        "-o", "--output-file", required=True, help="Path to result file",
    )
    parser.add_argument(
        "-a", "--all", action="store_true", help="Output all results, not only uncovered",
    )
    return parser.parse_args(args)


def merge_dicts(dict_a, dict_b):
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


def parse_out(out: str):
    """Parse command output."""
    lines = out.splitlines()
    section_start = False
    items = []
    for line in lines:
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
            items.append(item)
    return items


def get_available_commands(cmd_db: dict, cli_args: UnpackableSequence):
    """Recursively get all available commands and arguments."""
    out = cli(cli_args)
    items = parse_out(out)
    for item in items:
        if item.startswith("-"):
            cmd_db[item] = {"_count": 0}
            continue
        item_dict: dict = {"_count": 0}
        cmd_db[item] = item_dict
        get_available_commands(item_dict, [*cli_args, item])


def cli(cli_args) -> str:
    """Run the `cardano-cli` command."""
    cmd = ["cardano-cli", "shelley", *cli_args]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    __, stderr = p.communicate()
    return stderr.decode()


def get_result(input_files, cmd_db):
    """Get coverage result by merging available data."""
    for in_file in input_files:
        with open(in_file) as in_json:
            coverage = json.load(in_json)
        merge_dicts(cmd_db, coverage)
    return cmd_db


def filter_uncovered(results_db):
    """Return only uncovered commands/arguments."""
    this_db = {}
    for key, value in results_db.items():
        if key == "_count":
            continue
        if len(value) != 1:
            ret_db = filter_uncovered(value)
            if ret_db:
                this_db[key] = ret_db
                continue
        if value["_count"] == 0:
            this_db[key] = False
    return this_db


def main():
    args = get_args()

    shelley = {"_count": 0}
    cmd_db = {"cardano-cli": {"shelley": shelley}}

    get_available_commands(shelley, [])
    get_result(args.input_files, cmd_db)

    if args.all:
        out_content = cmd_db
    else:
        out_content = {"cardano-cli": filter_uncovered(cmd_db["cardano-cli"])}
    helpers.write_json(args.output_file, out_content)


if __name__ == "__main__":
    main()
