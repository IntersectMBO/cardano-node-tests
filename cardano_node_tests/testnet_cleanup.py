#!/usr/bin/env python3
"""Cleanup a testnet with the help of testing artifacts.

* withdraw rewards
* deregister stake addresses
* return funds to faucet
"""
import argparse
import concurrent.futures
import logging
import random
import time
from pathlib import Path
from typing import Generator
from typing import List

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType

LOGGER = logging.getLogger(__name__)


def get_args() -> argparse.Namespace:
    """Get command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "-a",
        "--artifacts-base-dir",
        required=True,
        type=helpers.check_dir_arg,
        help="Path to a directory with testing artifacts",
    )
    return parser.parse_args()


def withdraw_reward(
    cluster_obj: clusterlib.ClusterLib,
    stake_addr_record: clusterlib.AddressRecord,
    dst_addr_record: clusterlib.AddressRecord,
    name_template: str,
) -> None:
    """Withdraw rewards to payment address."""
    dst_address = dst_addr_record.address

    tx_files_withdrawal = clusterlib.TxFiles(
        signing_key_files=[dst_addr_record.skey_file, stake_addr_record.skey_file],
    )

    LOGGER.info(f"Withdrawing rewards for '{stake_addr_record.address}'")
    try:
        cluster_obj.send_tx(
            src_address=dst_address,
            tx_name=f"rf_{name_template}_reward_withdrawal",
            tx_files=tx_files_withdrawal,
            withdrawals=[clusterlib.TxOut(address=stake_addr_record.address, amount=-1)],
        )
    except clusterlib.CLIError:
        return


def deregister_stake_addr(
    cluster_obj: clusterlib.ClusterLib, pool_user: clusterlib.PoolUser, name_template: str
) -> None:
    """Deregister stake address."""
    # files for deregistering stake address
    stake_addr_dereg_cert = cluster_obj.gen_stake_addr_deregistration_cert(
        addr_name=f"rf_{name_template}_addr0_dereg", stake_vkey_file=pool_user.stake.vkey_file
    )
    tx_files_deregister = clusterlib.TxFiles(
        certificate_files=[stake_addr_dereg_cert],
        signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
    )

    LOGGER.info(f"Deregistering stake address '{pool_user.stake.address}'")
    try:
        cluster_obj.send_tx(
            src_address=pool_user.payment.address,
            tx_name=f"{name_template}_dereg_stake_addr",
            tx_files=tx_files_deregister,
        )
    except clusterlib.CLIError:
        return


def return_funds_to_faucet(
    cluster_obj: clusterlib.ClusterLib,
    src_addr: clusterlib.AddressRecord,
    faucet_addr: str,
    tx_name: str,
) -> None:
    """Send funds from `src_addr` to `faucet_addr`."""
    tx_name = f"rf_{tx_name}_return_funds"
    # the amount of "-1" means all available funds.
    fund_dst = [clusterlib.TxOut(address=faucet_addr, amount=-1)]
    fund_tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

    LOGGER.info(f"Returning funds from '{src_addr.address}'")
    # try to return funds; don't mind if there's not enough funds for fees etc.
    try:
        cluster_obj.send_funds(
            src_address=src_addr.address,
            destinations=fund_dst,
            tx_name=tx_name,
            tx_files=fund_tx_files,
            verify_tx=False,
        )
    except clusterlib.CLIError:
        pass


def create_addr_record(addr_file: Path) -> clusterlib.AddressRecord:
    """Return a `clusterlib.AddressRecord`."""
    f_name = addr_file.name.replace(".addr", "")
    basedir = addr_file.parent
    vkey_file = basedir / f"{f_name}.vkey"
    skey_file = basedir / f"{f_name}.skey"

    if not (vkey_file.exists() and skey_file.exists()):
        raise ValueError(f"Keys for '{addr_file}' not available.")

    addr_record = clusterlib.AddressRecord(
        address=clusterlib.read_address_from_file(addr_file),
        vkey_file=vkey_file,
        skey_file=skey_file,
    )
    return addr_record


def find_files(location: FileType) -> Generator[Path, None, None]:
    r"""Find all '\*.addr' files in given location and it's subdirectories."""
    location = Path(location).expanduser().resolve()
    return location.glob("**/*.addr")


def group_files(file_paths: Generator[Path, None, None]) -> List[List[Path]]:
    """Group payment address files with corresponding stake address files.

    These need to be processed together - funds are transfered from payment address after
    the stake address was deregistered.
    """
    curr_group: List[Path] = []
    path_groups: List[List[Path]] = [curr_group]
    prev_basename = ""

    # reverse-sort the list so stake address files are processes before payment address files
    for f in sorted(file_paths, reverse=True):
        # skip the '*_pycurrent' symlinks to pytest temp dirs
        if "_pycurrent" in str(f):
            continue
        basename = f.name.replace("_stake.addr", "").replace(".addr", "")
        if prev_basename == basename:
            curr_group.append(f)
            continue
        prev_basename = basename
        curr_group = [f]
        path_groups.append(curr_group)
    return path_groups


def cleanup(
    cluster_obj: clusterlib.ClusterLib,
    location: FileType,
) -> None:
    """Cleanup a testnet with the help of testing artifacts."""
    cluster_env = cluster_nodes.get_cluster_env()
    faucet_addr_file = cluster_env.state_dir / "shelley" / "faucet.addr"
    faucet_payment = create_addr_record(faucet_addr_file)
    files_found = group_files(find_files(location))

    def _run(files: List[Path]) -> None:
        for fpath in files:
            # add random sleep for < 1s to prevent
            # "Network.Socket.connect: <socket: 11>: resource exhausted"
            time.sleep(random.random())

            f_name = fpath.name
            if f_name == "faucet.addr":
                continue
            if f_name.endswith("_stake.addr"):
                payment_addr = fpath.parent / f_name.replace("_stake.addr", ".addr")
                try:
                    payment = create_addr_record(payment_addr)
                    stake = create_addr_record(fpath)
                except ValueError as exc:
                    LOGGER.warning(f"Skipping '{fpath}':\n'{exc}'")
                    continue

                pool_user = clusterlib.PoolUser(payment=payment, stake=stake)

                deregister_stake_addr(
                    cluster_obj=cluster_obj, pool_user=pool_user, name_template=f_name
                )

                withdraw_reward(
                    cluster_obj=cluster_obj,
                    stake_addr_record=stake,
                    dst_addr_record=payment,
                    name_template=f_name,
                )
            else:
                try:
                    payment = create_addr_record(fpath)
                except ValueError as exc:
                    LOGGER.warning(f"Skipping '{fpath}':\n'{exc}'")
                    continue
                return_funds_to_faucet(
                    cluster_obj=cluster_obj,
                    src_addr=payment,
                    faucet_addr=faucet_payment.address,
                    tx_name=f_name,
                )

    # run cleanup in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_run, f) for f in files_found]
        concurrent.futures.wait(futures)


def main() -> None:
    logging.basicConfig(
        format="%(name)s:%(levelname)s:%(message)s",
        level=logging.INFO,
    )
    args = get_args()

    cluster_obj = cluster_nodes.get_cluster_type().get_cluster_obj()
    cleanup(cluster_obj=cluster_obj, location=args.artifacts_base_dir)


if __name__ == "__main__":
    main()
