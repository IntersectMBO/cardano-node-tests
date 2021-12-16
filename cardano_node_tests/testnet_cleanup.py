#!/usr/bin/env python3
"""Cleanup a testnet with the help of testing artifacts.

* withdraw rewards
* deregister stake addresses
* return funds to faucet
"""
import argparse
import logging

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import testnet_cleanup

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
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        format="%(name)s:%(levelname)s:%(message)s",
        level=logging.INFO,
    )
    args = get_args()

    cluster_obj = cluster_nodes.get_cluster_type().get_cluster_obj()
    testnet_cleanup.cleanup(cluster_obj=cluster_obj, location=args.artifacts_base_dir)


if __name__ == "__main__":
    main()
