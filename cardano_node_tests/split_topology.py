#!/usr/bin/env python3
"""Generate topology files for split cluster.

For settings it uses the same env variables as when running the tests.
"""
import argparse
import logging
import sys
from pathlib import Path

from cardano_node_tests.utils import cluster_nodes

LOGGER = logging.getLogger(__name__)


def get_args() -> argparse.Namespace:
    """Get command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument(
        "-d",
        "--dest-dir",
        required=True,
        help="Path to destination directory",
    )
    parser.add_argument(
        "-i",
        "--instance-num",
        required=False,
        type=int,
        default=0,
        help="Instance number in the sequence of cluster instances (default: 0)",
    )
    parser.add_argument(
        "-o",
        "--offset",
        required=False,
        type=int,
        default=0,
        help="Difference in number of nodes in cluster 1 vs cluster 2 (default: 0)",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        format="%(name)s:%(levelname)s:%(message)s",
        level=logging.INFO,
    )
    args = get_args()

    destdir = Path(args.dest_dir)
    destdir.mkdir(parents=True, exist_ok=True)

    try:
        cluster_nodes.get_cluster_type().cluster_scripts.gen_split_topology_files(
            destdir=destdir,
            instance_num=args.instance_num,
            offset=args.offset,
        )
    except Exception as exc:
        LOGGER.error(str(exc))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
