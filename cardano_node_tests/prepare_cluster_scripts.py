#!/usr/bin/env python3
"""Create a directory with scripts and config files for running cluster instance.

For settings it uses the same env variables as when running the tests.
"""

import argparse
import logging
import pathlib as pl
import shutil
import sys

import cardonnay_scripts

import cardano_node_tests.utils.types as ttypes
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import cluster_scripts

LOGGER = logging.getLogger(__name__)
SCRIPTS_DIR = pl.Path(__file__).parent / "cluster_scripts"


def get_args() -> argparse.Namespace:
    """Get command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument(
        "-d",
        "--dest-dir",
        help="Path to destination directory",
    )
    parser.add_argument(
        "-t",
        "--testnet-variant",
        help="Testnet variant to use.",
    )
    parser.add_argument(
        "-i",
        "--instance-num",
        type=int,
        default=0,
        help="Instance number in the sequence of cluster instances (default: 0)",
    )
    parser.add_argument(
        "-l",
        "--ls",
        action="store_true",
        help="List available testnet variants and exit.",
    )
    parser.add_argument(
        "-c",
        "--clean",
        action="store_true",
        help="Delete the destination directory if it already exists (default: false)",
    )
    return parser.parse_args()


def _variant_dirs(base: pl.Path) -> set[str]:
    return {
        d.name
        for d in base.iterdir()
        if d.is_dir() and "egg-info" not in d.name and d.name != "common"
    }


def get_testnet_variants() -> list[str]:
    local = _variant_dirs(SCRIPTS_DIR)
    external = _variant_dirs(pl.Path(str(cardonnay_scripts.SCRIPTS_ROOT)))
    return sorted(local | external)


def get_testnet_variant_scriptdir(testnet_variant: str) -> pl.Path | None:
    if testnet_variant in _variant_dirs(SCRIPTS_DIR):
        return SCRIPTS_DIR / testnet_variant

    cscripts_root = pl.Path(str(cardonnay_scripts.SCRIPTS_ROOT))
    if testnet_variant in _variant_dirs(cscripts_root):
        return cscripts_root / testnet_variant

    return None


def prepare_scripts_files(
    destdir: ttypes.FileType,
    testnet_variant: str,
    instance_num: int = 0,
) -> cluster_scripts.InstanceFiles:
    """Prepare scripts files for starting and stopping cluster instance."""
    start_script: ttypes.FileType = ""
    stop_script: ttypes.FileType = ""

    scriptsdir = get_testnet_variant_scriptdir(testnet_variant=testnet_variant)

    if scriptsdir:
        scriptsdir = pl.Path(scriptsdir)
        start_script = next(scriptsdir.glob("start-cluster*"), "")
        if not start_script:
            msg = f"Start script not found in '{scriptsdir}'."
            raise RuntimeError(msg)

    startup_files = cluster_nodes.get_cluster_type().cluster_scripts.prepare_scripts_files(
        destdir=destdir,
        instance_num=instance_num,
        start_script=start_script,
        stop_script=stop_script,
    )
    return startup_files


def main() -> int:
    logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.INFO)
    args = get_args()

    if args.ls:
        variants_str = "\n".join(get_testnet_variants())
        LOGGER.info(f"Available testnet variants:\n{variants_str}")
        return 0

    if not args.dest_dir:
        LOGGER.error("The 'destdir' must be set.")
    destdir = pl.Path(args.dest_dir)

    testnet_variant = args.testnet_variant
    if not testnet_variant:
        LOGGER.error("The testnet variant must be set.")

    if args.clean:
        shutil.rmtree(destdir, ignore_errors=True)

    if destdir.exists():
        LOGGER.error(f"Destination directory '{destdir}' already exists.")
        return 1

    destdir.mkdir(parents=True)

    try:
        prepare_scripts_files(
            destdir=destdir, testnet_variant=testnet_variant, instance_num=args.instance_num
        )
    except Exception:
        LOGGER.exception("Failure")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
