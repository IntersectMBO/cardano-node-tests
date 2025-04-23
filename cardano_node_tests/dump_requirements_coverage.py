#!/usr/bin/env python3
"""Generate coverage results for external requirements."""

import argparse
import json
import logging
import sys

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import requirements

LOGGER = logging.getLogger(__name__)


def get_args() -> argparse.Namespace:
    """Get command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument(
        "-m",
        "--requirements-mapping",
        required=True,
        help="JSON file with requirements mapping",
    )
    parser.add_argument(
        "-o",
        "--output-file",
        required=True,
        help="File where to save coverage results",
    )
    parser.add_argument(
        "-a",
        "--artifacts-base-dir",
        type=helpers.check_dir_arg,
        help="Path to a directory with testing artifacts",
    )
    parser.add_argument(
        "-i",
        "--input-files",
        nargs="+",
        type=helpers.check_file_arg,
        help="Path to coverage results to merge into a final result",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.INFO)
    args = get_args()

    if not (args.artifacts_base_dir or args.input_files):
        LOGGER.error("Either `--artifacts-base-dir` or `--input-files` must be provided")
        return 1

    executed_req = {}
    if args.artifacts_base_dir:
        executed_req = requirements.collect_executed_req(base_dir=args.artifacts_base_dir)

    input_reqs = []
    if args.input_files:
        for input_file in args.input_files:
            with open(input_file, encoding="utf-8") as in_fp:
                input_reqs.append(json.load(in_fp))

    merged_reqs = requirements.merge_reqs(executed_req, *input_reqs)

    try:
        report = requirements.get_mapped_req(
            mapping=args.requirements_mapping, executed_req=merged_reqs
        )
    except ValueError as excp:
        LOGGER.error(str(excp))  # noqa: TRY400
        return 1

    helpers.write_json(out_file=args.output_file, content=report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
