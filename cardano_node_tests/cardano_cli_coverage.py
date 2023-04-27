#!/usr/bin/env python3
"""Generate coverage report for `cardano-cli` sub-commands and options."""
import argparse
import copy
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Iterable
from typing import List
from typing import Tuple

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

SKIPPED = (
    "build-script",
    "byron",
    "convert-byron-genesis-vkey",
    "convert-itn-bip32-key",
    "convert-itn-extended-key",
    "convert-itn-key",
    "help",
    "version",
    "--byron-era",
    "--byron-key",
    "--byron-mode",
    "--epoch-slots",
    "--help",
    "--icarus-payment-key",
    "--mainnet",
    "--shelley-mode",
    "--version",
)


def get_args() -> argparse.Namespace:
    """Get script command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument(
        "-i",
        "--input-files",
        required=True,
        nargs="+",
        type=helpers.check_file_arg,
        help="Path to coverage files",
    )
    parser.add_argument(
        "-o",
        "--output-file",
        help="File where to save coverage results",
    )
    parser.add_argument(
        "-u",
        "--uncovered-only",
        action="store_true",
        help="Report only uncovered arguments",
    )
    parser.add_argument(
        "-p",
        "--print-coverage",
        action="store_true",
        help="Print coverage percentage",
    )
    parser.add_argument(
        "-b",
        "--badge-icon-url",
        action="store_true",
        help="Print badge icon URL",
    )
    parser.add_argument(
        "--ignore-skips",
        action="store_true",
        help="Include all commands and arguments, ignore list of items to skip",
    )
    return parser.parse_args()


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
        # skipped arguments and commands are not in the available commands dict
        elif key not in dict_a:
            continue
        elif not isinstance(value, dict):
            dict_a[key] = value
        else:
            merge_coverage(dict_a[key], value)

    return dict_a


def cli(cli_args: Iterable[str]) -> str:
    """Run the `cardano-cli` command."""
    assert not isinstance(cli_args, str), "`cli_args` must be sequence of strings"
    with subprocess.Popen(list(cli_args), stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
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
            # skip line with subsection description
            if not line.startswith(" "):
                continue
            line_s = line.strip()
            if not line_s:
                continue
            item = line_s.split()[0]
            # in case the item looks like "-h,--help", take only the long option
            arg = item.split(",")[-1].strip()
            cli_args.append(arg)

    return cli_args


def get_available_commands(cli_args: Iterable[str], ignore_skips: bool = False) -> dict:
    """Get all available cardano-cli sub-commands and options."""
    cli_out = cli(cli_args)
    new_cli_args = parse_cmd_output(cli_out)
    command_dict: dict = {"_count": 0}
    for arg in new_cli_args:
        if not ignore_skips and arg in SKIPPED:
            continue
        if arg.startswith("-"):
            command_dict[arg] = {"_count": 0}
            continue
        command_dict[arg] = get_available_commands([*cli_args, arg], ignore_skips=ignore_skips)

    return command_dict


def get_log_coverage(log_file: Path) -> dict:
    """Get coverage info from log file containing CLI commands."""
    coverage_dict: dict = {}
    with open(log_file, encoding="utf-8") as infile:
        for line in infile:
            if not line.startswith("cardano-cli"):
                continue
            clusterlib.record_cli_coverage(cli_args=line.split(), coverage_dict=coverage_dict)

    return coverage_dict


def get_coverage(coverage_files: List[Path], available_commands: dict) -> dict:
    """Get coverage info by merging available data."""
    coverage_dict = copy.deepcopy(available_commands)
    for in_coverage in coverage_files:
        if in_coverage.suffix == ".json":
            with open(in_coverage, encoding="utf-8") as infile:
                coverage = json.load(infile)
        else:
            coverage = get_log_coverage(in_coverage)

        if coverage.get("cardano-cli", {}).get("_count") is None:
            raise AttributeError(
                f"Data in '{in_coverage}' doesn't seem to be in proper coverage format."
            )

        coverage_dict = merge_coverage(coverage_dict, coverage)

    return coverage_dict


def get_report(
    arg_name: str, coverage: dict, uncovered_only: bool = False
) -> Tuple[dict, int, int]:
    """Generate coverage report."""
    uncovered_db: dict = {}
    covered_count = 0
    uncovered_count = 0
    for key, value in coverage.items():
        if key == "_count":
            continue
        if len(value) != 1:
            ret_db, ret_covered_count, ret_uncovered_count = get_report(
                arg_name=key, coverage=value, uncovered_only=uncovered_only
            )
            covered_count += ret_covered_count
            uncovered_count += ret_uncovered_count
            if ret_db:
                uncovered_db[key] = ret_db
            continue
        count = value["_count"]
        if count == 0:
            uncovered_db[key] = 0
            uncovered_count += 1
        else:
            covered_count += 1
        if count and not uncovered_only:
            uncovered_db[key] = count

    if uncovered_db and "_count" in coverage:
        uncovered_db[f"_count_{arg_name}"] = coverage["_count"]
        uncovered_db[f"_coverage_{arg_name}"] = (
            (100 / ((covered_count + uncovered_count) / covered_count)) if covered_count else 0
        )

    return uncovered_db, covered_count, uncovered_count


def get_badge_icon(report: dict) -> str:
    """Return URL of badge icon."""
    coverage_percentage = report["cardano-cli"]["_coverage_cardano-cli"]
    color = "green"
    if coverage_percentage < 50:
        color = "red"
    elif coverage_percentage < 90:
        color = "yellow"
    icon_url = (
        "https://img.shields.io/static/v1?label=cli%20coverage&"
        f"message={coverage_percentage:.2f}%&color={color}&style=for-the-badge"
    )
    return icon_url


def main() -> int:
    logging.basicConfig(
        format="%(name)s:%(levelname)s:%(message)s",
        level=logging.INFO,
    )
    args = get_args()

    if not (args.output_file or args.print_coverage or args.badge_icon_url):
        LOGGER.error("One of --output-file, --print-coverage or --badge-icon-url is needed")
        return 1

    available_commands = {
        "cardano-cli": get_available_commands(["cardano-cli"], ignore_skips=args.ignore_skips)
    }
    try:
        coverage = get_coverage(
            coverage_files=args.input_files, available_commands=available_commands
        )
    except AttributeError as exc:
        LOGGER.error(str(exc))
        return 1

    report, *__ = get_report(
        arg_name="cardano-cli", coverage=coverage, uncovered_only=args.uncovered_only
    )

    if args.output_file:
        helpers.write_json(args.output_file, report)
    if args.print_coverage:
        print(report["cardano-cli"]["_coverage_cardano-cli"])
    if args.badge_icon_url:
        print(get_badge_icon(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
