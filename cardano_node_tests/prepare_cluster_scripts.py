#!/usr/bin/env python3
"""Create a directory with scripts and config files for running cluster instance.

For settings it uses the same env variables as when running the tests.
"""
import argparse
import logging
import sys
from pathlib import Path

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import cluster_scripts
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType

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
        "-s",
        "--scripts-dir",
        required=False,
        type=helpers.check_dir_arg,
        help="Path to directory with scripts templates",
    )
    parser.add_argument(
        "-i",
        "--instance-num",
        required=False,
        type=int,
        default=0,
        help="Instance number in the sequence of cluster instances (default: 0)",
    )
    return parser.parse_args()


def prepare_scripts_files(
    destdir: FileType,
    scriptsdir: FileType = "",
    instance_num: int = 0,
) -> cluster_scripts.InstanceFiles:
    """Prepare scripts files for starting and stopping cluster instance."""
    start_script: FileType = ""
    stop_script: FileType = ""

    if scriptsdir:
        scriptsdir = Path(scriptsdir)
        start_script = next(scriptsdir.glob("start-cluster*"), "")
        stop_script = next(scriptsdir.glob("stop-cluster*"), "")
        if not (start_script and stop_script):
            raise RuntimeError(f"Start/stop scripts not found in '{scriptsdir}'.")

    startup_files = cluster_nodes.get_cluster_type().cluster_scripts.prepare_scripts_files(
        destdir=destdir,
        instance_num=instance_num,
        start_script=start_script,
        stop_script=stop_script,
    )
    return startup_files


def main() -> int:
    logging.basicConfig(
        format="%(name)s:%(levelname)s:%(message)s",
        level=logging.INFO,
    )
    args = get_args()

    destdir = Path(args.dest_dir)
    if destdir.exists():
        LOGGER.error(f"Destination directory '{destdir}' already exists.")
        return 1
    destdir.mkdir(parents=True)

    scriptsdir: FileType = Path(args.scripts_dir) if args.scripts_dir else ""

    try:
        prepare_scripts_files(
            destdir=destdir, scriptsdir=scriptsdir, instance_num=args.instance_num
        )
    except Exception as exc:
        LOGGER.error(str(exc))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
