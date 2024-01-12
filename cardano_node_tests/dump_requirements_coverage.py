#!/usr/bin/env python3
"""Generate coverage results for external requirements."""
import argparse
import logging

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import requirements

LOGGER = logging.getLogger(__name__)


def get_args() -> argparse.Namespace:
    """Get command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument(
        "-a",
        "--artifacts-base-dir",
        required=True,
        type=helpers.check_dir_arg,
        help="Path to a directory with testing artifacts",
    )
    parser.add_argument(
        "-o",
        "--output-file",
        required=True,
        help="File where to save coverage results",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        format="%(name)s:%(levelname)s:%(message)s",
        level=logging.INFO,
    )
    args = get_args()

    report = requirements.collect_executed_req(base_dir=args.artifacts_base_dir)
    helpers.write_json(out_file=args.output_file, content=report)


if __name__ == "__main__":
    main()
