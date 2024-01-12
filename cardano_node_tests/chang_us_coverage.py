#!/usr/bin/env python3
"""Generate coverage report of Chang User Stories."""
import argparse
import json
import logging

from cardano_node_tests.utils import requirements

LOGGER = logging.getLogger(__name__)


def get_args() -> argparse.Namespace:
    """Get command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument(
        "-i",
        "--input-file",
        required=True,
        help="File with coverage results",
    )
    parser.add_argument(
        "-t",
        "--report-template",
        required=True,
        help="File with report template",
    )
    parser.add_argument(
        "-o",
        "--output-report",
        required=True,
        help="Report file",
    )
    return parser.parse_args()


def _get_color(status: str) -> str:
    if status == requirements.Statuses.SUCCESS:
        return "green"
    if status == requirements.Statuses.FAILURE:
        return "red"
    return "grey"


def main() -> None:
    logging.basicConfig(
        format="%(name)s:%(levelname)s:%(message)s",
        level=logging.INFO,
    )
    args = get_args()

    with open(args.report_template, encoding="utf-8") as in_fp:
        report = in_fp.read()

    with open(args.input_file, encoding="utf-8") as in_fp:
        coverage = json.load(in_fp)

    chang_group: dict = coverage.get(requirements.GroupsKnown.CHANG_US)

    for req_id, req_data in chang_group.items():
        color = _get_color(req_data["status"])
        report = report.replace(f"/{req_id}-grey", f"/{req_id}-{color}")
        url = req_data.get("url")
        report = report.replace(f"https://github.com/{req_id}-404", url)

    with open(args.output_report, "w", encoding="utf-8") as out_fp:
        out_fp.write(report)


if __name__ == "__main__":
    main()
