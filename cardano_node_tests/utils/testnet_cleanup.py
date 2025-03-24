"""Cleanup a testnet with the help of testing artifacts.

* withdraw rewards
* deregister stake addresses
* retire DReps
* return funds to faucet
"""

import concurrent.futures
import functools
import logging
import pathlib as pl
import queue
import random
import time
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import defragment_utxos
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


def deregister_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    pool_user: clusterlib.PoolUser,
    stake_addr_info: clusterlib.StakeAddrInfo,
    name_template: str,
    deposit_amt: int,
) -> None:
    """Deregister stake address."""
    withdrawals = []
    if stake_addr_info.reward_account_balance:
        withdrawals = [clusterlib.TxOut(address=pool_user.stake.address, amount=-1)]

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

    dereg_failed = False
    try:
        cluster_obj.g_transaction.send_tx(
            src_address=pool_user.payment.address,
            tx_name=f"{name_template}_dereg_withdraw_stake_addr",
            tx_files=tx_files_deregister,
            withdrawals=withdrawals,
            deposit=-deposit_amt,
        )
    except clusterlib.CLIError:
        dereg_failed = True
        LOGGER.error(f"Failed to deregister stake address '{pool_user.stake.address}'")  # noqa: TRY400
    else:
        LOGGER.debug(f"Deregistered stake address '{pool_user.stake.address}'")

    # Try to at least withdraw rewards if deregistration was not successful
    if dereg_failed and withdrawals:
        tx_files_withdrawal = clusterlib.TxFiles(
            signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
        )
        try:
            cluster_obj.g_transaction.send_tx(
                src_address=pool_user.payment.address,
                tx_name=f"rf_{name_template}_reward_withdrawal",
                tx_files=tx_files_withdrawal,
                withdrawals=withdrawals,
            )
        except clusterlib.CLIError:
            LOGGER.error(f"Failed to withdraw rewards for '{pool_user.stake.address}'")  # noqa: TRY400
        else:
            LOGGER.debug(f"Withdrawn rewards for '{pool_user.stake.address}'")


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
        LOGGER.debug(f"Retired a DRep '{name_template}'")


def get_tx_inputs(
    cluster_obj: clusterlib.ClusterLib,
    src_addrs: list[clusterlib.AddressRecord],
) -> tp.Tuple[list[clusterlib.UTXOData], list[pl.Path]]:
    """Return signing keys and transaction inputs for given addresses."""
    skeys = []
    txins = []
    for a in src_addrs:
        utxos = cluster_obj.g_query.get_utxo(address=a.address)
        utxos_ids_excluded = {
            f"{u.utxo_hash}#{u.utxo_ix}"
            for u in utxos
            if u.coin != clusterlib.DEFAULT_COIN or u.datum_hash
        }
        txins_ok = [u for u in utxos if f"{u.utxo_hash}#{u.utxo_ix}" not in utxos_ids_excluded]
        if txins_ok:
            txins.extend(txins_ok)
            skeys.append(a.skey_file)

    return txins, skeys


def return_funds_to_faucet(
    cluster_obj: clusterlib.ClusterLib,
    txins: list[clusterlib.UTXOData],
    skeys: list[pl.Path],
    faucet_address: str,
    tx_name: str = "",
) -> None:
    """Send funds from `txins` to `faucet_address`."""
    tx_name = tx_name or helpers.get_timestamped_rand_str()
    tx_name = f"rf_{tx_name}"

    # The amount of "-1" means all available funds.
    fund_dst = [clusterlib.TxOut(address=faucet_address, amount=-1)]
    fund_tx_files = clusterlib.TxFiles(signing_key_files=skeys)

    txins_len = len(txins)
    batch_size = min(100, txins_len)
    batch_num = 1
    for b in range(0, txins_len, batch_size):
        tx_name = f"{tx_name}_batch{batch_num}"
        batch_num += 1
        batch = txins[b : b + batch_size]
        batch_balance = functools.reduce(lambda x, y: x + y.amount, batch, 0)

        # Skip if there is too little Lovelace
        if batch_balance < 1_000_000:
            return

        # Try to return funds; don't mind if there's not enough funds for fees etc.
        try:
            cluster_obj.g_transaction.send_tx(
                src_address=txins[0].address,
                tx_name=tx_name,
                txins=batch,
                txouts=fund_dst,
                tx_files=fund_tx_files,
                verify_tx=False,
            )
        except clusterlib.CLIError:
            LOGGER.error(f"Failed to return funds from addresses for '{tx_name}'")  # noqa: TRY400
        else:
            LOGGER.debug(f"Returned funds from addresses '{tx_name}'")


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
    num_threads = min(10, len(files_found) // 200)

    stake_deposit_amt = cluster_obj.g_query.get_address_deposit()

    def _run(files: list[pl.Path]) -> None:
        skeys = []
        txins = []
        for fpath in files:
            f_name = fpath.name
            if f_name == "faucet.addr":
                continue

            # Add random sleep for < 0.5s to prevent
            # "Network.Socket.connect: <socket: 11>: resource exhausted"
            time.sleep(random.random() / 2)

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

                deregister_stake_addr(
                    cluster_obj=cluster_obj,
                    pool_user=pool_user,
                    stake_addr_info=stake_addr_info,
                    name_template=f_name,
                    deposit_amt=stake_deposit_amt,
                )
            else:
                try:
                    payment = create_addr_record(fpath)
                except ValueError as exc:
                    LOGGER.warning(f"Skipping '{fpath}':\n'{exc}'")
                    continue
                f_txins, f_skeys = get_tx_inputs(cluster_obj=cluster_obj, src_addrs=[payment])
                skeys.extend(f_skeys)
                txins.extend(f_txins)

        return_funds_to_faucet(
            cluster_obj=cluster_obj,
            txins=txins,
            skeys=skeys,
            faucet_address=faucet_payment.address,
        )

    # Run cleanup in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_run, f) for f in files_found]
        concurrent.futures.wait(futures)

    cluster_obj.wait_for_new_block(new_blocks=3)


def cleanup_certs(
    cluster_obj: clusterlib.ClusterLib, location: pl.Path, faucet_payment: clusterlib.AddressRecord
) -> None:
    """Cleanup DRep certs."""
    files_found = list(find_cert_files(location))
    num_threads = min(10, len(files_found) // 10)

    drep_deposit_amt = cluster_obj.g_query.get_drep_deposit()

    # Fund the addresses that will pay for fees
    fund_addrs = [
        cluster_obj.g_address.gen_payment_addr_and_keys(name=f"certs_cleanup{i}")
        for i in range(num_threads + 1)
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
        # Add random sleep for < 0.5s to prevent
        # "Network.Socket.connect: <socket: 11>: resource exhausted"
        time.sleep(random.random() / 2)

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
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_run, f, addrs_queue) for f in files_found]
        concurrent.futures.wait(futures)

    # Return funds from the addresses that paid for fees
    txins, skeys = get_tx_inputs(cluster_obj=cluster_obj, src_addrs=fund_addrs)
    return_funds_to_faucet(
        cluster_obj=cluster_obj,
        txins=txins,
        skeys=skeys,
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


def addresses_info(cluster_obj: clusterlib.ClusterLib, location: pl.Path) -> tp.Tuple[int, int]:
    """Return the total balance and rewards of all addresses in the given location."""
    balance = 0
    rewards = 0
    files_found = group_addr_files(find_addr_files(location))

    for files in files_found:
        for fpath in files:
            f_name = fpath.name
            if f_name == "faucet.addr":
                continue

            # Add sleep to prevent
            # "Network.Socket.connect: <socket: 11>: resource exhausted"
            time.sleep(0.1)

            if f_name.endswith("_stake.addr"):
                address = clusterlib.read_address_from_file(fpath)
                stake_addr_info = cluster_obj.g_query.get_stake_addr_info(address)
                if not stake_addr_info:
                    continue
                f_rewards = stake_addr_info.reward_account_balance
                if f_rewards:
                    rewards += f_rewards
                    LOGGER.info(f"{f_rewards / 1_000_000} ADA on '{fpath}'")
            else:
                address = clusterlib.read_address_from_file(fpath)
                utxos = cluster_obj.g_query.get_utxo(address=address)
                lovelace_utxos = [u for u in utxos if u.coin == clusterlib.DEFAULT_COIN]
                f_balance = functools.reduce(lambda x, y: x + y.amount, lovelace_utxos, 0)
                if f_balance:
                    has_tokens = len(lovelace_utxos) != len(utxos)
                    tokens_str = " + tokens" if has_tokens else ""
                    LOGGER.info(f"{f_balance / 1_000_000} ADA{tokens_str} on '{fpath}'")
                    balance += f_balance

    return balance, rewards


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
