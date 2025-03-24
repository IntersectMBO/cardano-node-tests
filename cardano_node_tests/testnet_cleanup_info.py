#!/usr/bin/env python3
"""Print the Lovelace balance and rewards of all addresses in the given location."""

import argparse
import logging
import os
import pathlib as pl
import sys

from cardano_clusterlib import clusterlib

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
        type=helpers.check_dir_arg_keep,
        help="Path to a directory with testing artifacts",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        format="%(name)s:%(levelname)s:%(message)s",
        level=logging.INFO,
    )
    args = get_args()

    socket_env = os.environ.get("CARDANO_NODE_SOCKET_PATH")
    if not socket_env:
        LOGGER.error("The `CARDANO_NODE_SOCKET_PATH` environment variable is not set.")
        return 1

    state_dir = pl.Path(socket_env).parent
    cluster_obj = clusterlib.ClusterLib(state_dir=state_dir)
    location = args.artifacts_base_dir
    balance, rewards = testnet_cleanup.addresses_info(cluster_obj=cluster_obj, location=location)
    LOGGER.info(f"Uncleaned balance: {balance} Lovelace ({balance / 1_000_000} ADA)")
    LOGGER.info(f"Uncleaned rewards: {rewards} Lovelace ({rewards / 1_000_000} ADA)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
