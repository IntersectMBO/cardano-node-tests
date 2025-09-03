#!/usr/bin/env python3
"""Defragment address UTxOs."""

import argparse
import logging
import os
import pathlib as pl
import sys

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import defragment_utxos
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


def get_args() -> argparse.Namespace:
    """Get command line arguments."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", maxsplit=1)[0])
    parser.add_argument(
        "-a",
        "--address",
        required=True,
        help="Address to defragment.",
    )
    parser.add_argument(
        "-s",
        "--skey-file",
        required=True,
        type=helpers.check_file_arg,
        help="Path to skey file.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.INFO)
    args = get_args()

    socket_env = os.environ.get("CARDANO_NODE_SOCKET_PATH")
    if not socket_env:
        LOGGER.error("The `CARDANO_NODE_SOCKET_PATH` environment variable is not set.")
        return 1

    state_dir = pl.Path(socket_env).parent
    cluster_obj = clusterlib.ClusterLib(state_dir=state_dir)
    defragment_utxos.defragment(
        cluster_obj=cluster_obj, address=args.address, skey_file=args.skey_file
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
