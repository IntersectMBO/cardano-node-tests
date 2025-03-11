"""Cleanup a testnet with the help of testing artifacts.

* withdraw rewards
* deregister stake addresses
* retire DReps
* return funds to faucet
"""

import concurrent.futures
import functools
import itertools
import logging
import pathlib as pl
import queue
import random
import time
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import defragment_utxos

LOGGER = logging.getLogger(__name__)


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

    try:
        cluster_obj.g_transaction.send_tx(
            src_address=dst_address,
            tx_name=f"rf_{name_template}_reward_withdrawal",
            tx_files=tx_files_withdrawal,
            withdrawals=[clusterlib.TxOut(address=stake_addr_record.address, amount=-1)],
        )
    except clusterlib.CLIError:
        LOGGER.error(f"Failed to withdraw rewards for '{stake_addr_record.address}'")  # noqa: TRY400
    else:
        LOGGER.info(f"Withdrawn rewards for '{stake_addr_record.address}'")


def deregister_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    pool_user: clusterlib.PoolUser,
    name_template: str,
    deposit_amt: int,
) -> None:
    """Deregister stake address."""
    # Files for deregistering stake address
    stake_addr_dereg_cert = cluster_obj.g_stake_address.gen_stake_addr_deregistration_cert(
        addr_name=f"rf_{name_template}_addr0_dereg",
        deposit_amt=deposit_amt,
        stake_vkey_file=pool_user.stake.vkey_file,
    )
    tx_files_deregister = clusterlib.TxFiles(
        certificate_files=[stake_addr_dereg_cert],
        signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
    )

    try:
        cluster_obj.g_transaction.send_tx(
            src_address=pool_user.payment.address,
            tx_name=f"{name_template}_dereg_stake_addr",
            tx_files=tx_files_deregister,
            deposit=-deposit_amt,
        )
    except clusterlib.CLIError:
        LOGGER.error(f"Failed to deregister stake address '{pool_user.stake.address}'")  # noqa: TRY400
    else:
        LOGGER.info(f"Deregistered stake address '{pool_user.stake.address}'")


def retire_drep(
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    drep_keys: clusterlib.KeyPair,
    name_template: str,
    deposit_amt: int,
) -> None:
    """Retire a DRep."""
    ret_cert = cluster_obj.g_conway_governance.drep.gen_retirement_cert(
        cert_name=f"{name_template}_cleanup",
        deposit_amt=deposit_amt,
        drep_vkey_file=drep_keys.vkey_file,
    )
    tx_files = clusterlib.TxFiles(
        certificate_files=[ret_cert],
        signing_key_files=[payment_addr.skey_file, drep_keys.skey_file],
    )

    try:
        cluster_obj.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=f"{name_template}_retire_drep",
            tx_files=tx_files,
            deposit=-deposit_amt,
        )
    except clusterlib.CLIError:
        LOGGER.error(f"Failed to retire a DRep '{name_template}'")  # noqa: TRY400
    else:
        LOGGER.info(f"Retired a DRep '{name_template}'")


def return_funds_to_faucet(
    cluster_obj: clusterlib.ClusterLib,
    src_addrs: list[clusterlib.AddressRecord],
    faucet_address: str,
    tx_name: str,
) -> None:
    """Send funds from `src_addr`s to `faucet_address`."""
    tx_name = f"rf_{tx_name}"
    # The amount of "-1" means all available funds.
    fund_dst = [clusterlib.TxOut(address=faucet_address, amount=-1)]
    fund_tx_files = clusterlib.TxFiles(signing_key_files=[f.skey_file for f in src_addrs])

    txins_nested = [cluster_obj.g_query.get_utxo(address=f.address) for f in src_addrs]
    txins = list(itertools.chain.from_iterable(txins_nested))
    utxos_balance = functools.reduce(lambda x, y: x + y.amount, txins, 0)

    # Skip if there no (or too little) Lovelace
    if utxos_balance < 1000_000:
        return

    # If the balance is too low, add a faucet UTxO so there's enough funds for fee
    # and the total amount is higher than min ADA value
    if utxos_balance < 3000_000:
        faucet_utxos = cluster_obj.g_query.get_utxo(
            address=faucet_address, coins=[clusterlib.DEFAULT_COIN]
        )
        futxo = random.choice(faucet_utxos)
        txins.append(futxo)

    # Try to return funds; don't mind if there's not enough funds for fees etc.
    try:
        cluster_obj.g_transaction.send_tx(
            src_address=src_addrs[0].address,
            tx_name=tx_name,
            txins=txins,
            txouts=fund_dst,
            tx_files=fund_tx_files,
            verify_tx=False,
        )
    except clusterlib.CLIError:
        LOGGER.error(f"Failed to return funds from addresses for '{tx_name}'")  # noqa: TRY400
    else:
        LOGGER.info(f"Returned funds from addresses '{tx_name}'")


def create_addr_record(addr_file: pl.Path) -> clusterlib.AddressRecord:
    """Return a `clusterlib.AddressRecord`."""
    f_name = addr_file.name.replace(".addr", "")
    basedir = addr_file.parent
    vkey_file = basedir / f"{f_name}.vkey"
    skey_file = basedir / f"{f_name}.skey"

    if not (vkey_file.exists() and skey_file.exists()):
        msg = f"Keys for '{addr_file}' not available."
        raise ValueError(msg)

    addr_record = clusterlib.AddressRecord(
        address=clusterlib.read_address_from_file(addr_file),
        vkey_file=vkey_file,
        skey_file=skey_file,
    )
    return addr_record


def find_addr_files(location: pl.Path) -> tp.Generator[pl.Path, None, None]:
    r"""Find all '\*.addr' files in given location and it's subdirectories."""
    return location.glob("**/*.addr")


def find_cert_files(location: pl.Path) -> tp.Generator[pl.Path, None, None]:
    r"""Find all '\*_drep_reg.cert' files in given location and it's subdirectories."""
    return location.glob("**/*_drep_reg.cert")


def group_addr_files(file_paths: tp.Generator[pl.Path, None, None]) -> list[list[pl.Path]]:
    """Group payment address files with corresponding stake address files.

    These need to be processed together - funds are transferred from payment address after
    the stake address was deregistered.
    """
    curr_group: list[pl.Path] = []
    path_groups: list[list[pl.Path]] = [curr_group]
    prev_basename = ""

    # Reverse-sort the list so stake address files are processes before payment address files
    for f in sorted(file_paths, reverse=True):
        # Skip the '*_pycurrent' symlinks to pytest temp dirs
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


def cleanup_addresses(
    cluster_obj: clusterlib.ClusterLib, location: pl.Path, faucet_payment: clusterlib.AddressRecord
) -> None:
    """Cleanup addresses."""
    files_found = group_addr_files(find_addr_files(location))
    stake_deposit_amt = cluster_obj.g_query.get_address_deposit()

    def _run(files: list[pl.Path]) -> None:
        for fpath in files:
            # Add random sleep for < 1s to prevent
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

                stake_addr_info = cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address)
                if not stake_addr_info:
                    continue

                if stake_addr_info.reward_account_balance:
                    withdraw_reward(
                        cluster_obj=cluster_obj,
                        stake_addr_record=stake,
                        dst_addr_record=payment,
                        name_template=f_name,
                    )

                deregister_stake_addr(
                    cluster_obj=cluster_obj,
                    pool_user=pool_user,
                    name_template=f_name,
                    deposit_amt=stake_deposit_amt,
                )
            else:
                try:
                    payment = create_addr_record(fpath)
                except ValueError as exc:
                    LOGGER.warning(f"Skipping '{fpath}':\n'{exc}'")
                    continue
                return_funds_to_faucet(
                    cluster_obj=cluster_obj,
                    src_addrs=[payment],
                    faucet_address=faucet_payment.address,
                    tx_name=f_name,
                )

    # Run cleanup in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_run, f) for f in files_found]
        concurrent.futures.wait(futures)


def cleanup_certs(
    cluster_obj: clusterlib.ClusterLib, location: pl.Path, faucet_payment: clusterlib.AddressRecord
) -> None:
    """Cleanup DRep certs."""
    files_found = find_cert_files(location)
    drep_deposit_amt = cluster_obj.conway_genesis["dRepDeposit"]

    # Fund the addresses that will pay for fees
    fund_addrs = [
        cluster_obj.g_address.gen_payment_addr_and_keys(name=f"certs_cleanup{i}") for i in range(11)
    ]
    fund_dst = [clusterlib.TxOut(address=f.address, amount=300_000_000) for f in fund_addrs]
    fund_tx_files = clusterlib.TxFiles(signing_key_files=[faucet_payment.skey_file])
    cluster_obj.g_transaction.send_tx(
        src_address=faucet_payment.address,
        tx_name="fund_certs_cleanup",
        txouts=fund_dst,
        tx_files=fund_tx_files,
    )

    addrs_queue: queue.Queue[clusterlib.AddressRecord] = queue.Queue()
    for a in fund_addrs:
        addrs_queue.put(a)

    def _run(cert_file: pl.Path, addrs_queue: queue.Queue[clusterlib.AddressRecord]) -> None:
        # Add random sleep for < 1s to prevent
        # "Network.Socket.connect: <socket: 11>: resource exhausted"
        time.sleep(random.random())

        fname = cert_file.name
        fdir = cert_file.parent
        vkey_file = fdir / cert_file.name.replace("_reg.cert", ".vkey")
        skey_file = vkey_file.with_suffix(".skey")
        drep_keys = clusterlib.KeyPair(vkey_file=vkey_file, skey_file=skey_file)

        addr = addrs_queue.get()
        try:
            retire_drep(
                cluster_obj=cluster_obj,
                payment_addr=addr,
                drep_keys=drep_keys,
                name_template=fname,
                deposit_amt=drep_deposit_amt,
            )
        finally:
            addrs_queue.put(addr)

    # Run cleanup in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_run, f, addrs_queue) for f in files_found]
        concurrent.futures.wait(futures)

    # Return funds from the addresses that paid for fees
    return_funds_to_faucet(
        cluster_obj=cluster_obj,
        src_addrs=fund_addrs,
        faucet_address=faucet_payment.address,
        tx_name="certs_cleanup_return",
    )


def _get_faucet_payment_rec(
    address: str = "",
    skey_file: clusterlib.FileType = "",
) -> clusterlib.AddressRecord:
    if address or skey_file:
        if not (address and skey_file):
            err = "Both 'address' and 'skey_file' need to be set."
            raise ValueError(err)

        faucet_payment = clusterlib.AddressRecord(
            address=address,
            vkey_file=pl.Path("/nonexistent"),  # We don't need this for faucet
            skey_file=pl.Path(skey_file),
        )
    else:
        # Try to infer the faucet address and keys from cluster env
        cluster_env = cluster_nodes.get_cluster_env()
        faucet_addr_file = cluster_env.state_dir / "shelley" / "faucet.addr"
        faucet_payment = create_addr_record(faucet_addr_file)

    return faucet_payment


def cleanup(
    cluster_obj: clusterlib.ClusterLib,
    location: clusterlib.FileType,
    faucet_address: str = "",
    faucet_skey_file: clusterlib.FileType = "",
) -> None:
    """Cleanup a testnet with the help of testing artifacts."""
    location = pl.Path(location).expanduser().resolve()
    faucet_payment = _get_faucet_payment_rec(address=faucet_address, skey_file=faucet_skey_file)
    cleanup_addresses(cluster_obj=cluster_obj, location=location, faucet_payment=faucet_payment)
    cleanup_certs(cluster_obj=cluster_obj, location=location, faucet_payment=faucet_payment)

    # Defragment faucet address UTxOs
    defragment_utxos.defragment(
        cluster_obj=cluster_obj, address=faucet_payment.address, skey_file=faucet_payment.skey_file
    )

    faucet_balance = cluster_obj.g_query.get_address_balance(address=faucet_payment.address)
    LOGGER.info(f"Final faucet balance: {faucet_balance}")
