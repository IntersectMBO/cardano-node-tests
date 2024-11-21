#!/usr/bin/env python3
"""Cleanup a testnet with the help of testing artifacts.

* withdraw rewards
* deregister stake addresses
* retire DReps
* return funds to faucet
"""

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
        type=helpers.check_dir_arg,
        help="Path to a directory with testing artifacts",
    )
    parser.add_argument(
        "-f",
        "--address",
        required=True,
        help="Faucet address",
    )
    parser.add_argument(
        "-s",
        "--skey-file",
        required=True,
        type=helpers.check_file_arg,
        help="Path to faucet skey file",
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
    if not os.environ.get("BOOTSTRAP_DIR"):
        LOGGER.error("The `BOOTSTRAP_DIR` environment variable is not set.")
        return 1

    state_dir = pl.Path(socket_env).parent
    cluster_obj = clusterlib.ClusterLib(state_dir=state_dir)
    testnet_cleanup.cleanup(
        cluster_obj=cluster_obj,
        location=args.artifacts_base_dir,
        faucet_address=args.address,
        faucet_skey_file=args.skey_file,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
