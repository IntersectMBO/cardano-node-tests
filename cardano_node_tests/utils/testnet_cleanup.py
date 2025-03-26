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
import random
import time
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import defragment_utxos
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

TxInputGroup = list[tuple[list[clusterlib.UTXOData], pl.Path]]


def reregister_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    stake_addr: clusterlib.AddressRecord,
    stake_addr_info: clusterlib.StakeAddrInfo,
    name_template: str,
    deposit_amt: int,
) -> clusterlib.StakeAddrInfo:
    """Re-register stake address.

    The address needs to be registered and delegated to a DRep to be able to withdraw rewards.
    """
    rereg_certs = []
    rereg_deposit = 0

    is_not_registered = not stake_addr_info
    has_no_vote_deleg = not stake_addr_info.vote_delegation
    has_rewards = stake_addr_info and stake_addr_info.reward_account_balance

    if is_not_registered:
        reg_cert = cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"rf_{name_template}",
            deposit_amt=deposit_amt,
            stake_vkey_file=stake_addr.vkey_file,
        )
        rereg_certs.append(reg_cert)
        rereg_deposit += deposit_amt

    # We need to delegate votes only when there are rewards to withdraw
    if is_not_registered or (has_rewards and has_no_vote_deleg):
        deleg_cert = cluster_obj.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"rf_{name_template}",
            stake_vkey_file=stake_addr.vkey_file,
            always_abstain=True,
        )
        rereg_certs.append(deleg_cert)

    if rereg_certs:
        tx_files_register = clusterlib.TxFiles(
            certificate_files=rereg_certs,
            signing_key_files=[payment_addr.skey_file, stake_addr.skey_file],
        )
        try:
            cluster_obj.g_transaction.send_tx(
                src_address=payment_addr.address,
                tx_name=f"rf_{name_template}_rereg_stake",
                tx_files=tx_files_register,
                deposit=rereg_deposit,
            )
        except clusterlib.CLIError:
            LOGGER.error(f"Failed to re-register stake address '{stake_addr.address}'")  # noqa: TRY400
        else:
            LOGGER.debug(f"Re-registered stake address '{stake_addr.address}'")
            stake_addr_info = cluster_obj.g_query.get_stake_addr_info(stake_addr.address)

    return stake_addr_info


def deregister_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    stake_addr: clusterlib.AddressRecord,
    stake_addr_info: clusterlib.StakeAddrInfo,
    name_template: str,
    deposit_amt: int,
) -> None:
    """Deregister stake address."""
    withdrawals = []
    if stake_addr_info.reward_account_balance:
        withdrawals = [
            clusterlib.TxOut(
                address=stake_addr.address, amount=stake_addr_info.reward_account_balance
            )
        ]

    # Files for deregistering stake address
    stake_addr_dereg_cert = cluster_obj.g_stake_address.gen_stake_addr_deregistration_cert(
        addr_name=f"rf_{name_template}",
        deposit_amt=deposit_amt,
        stake_vkey_file=stake_addr.vkey_file,
    )
    tx_files_deregister = clusterlib.TxFiles(
        certificate_files=[stake_addr_dereg_cert],
        signing_key_files=[payment_addr.skey_file, stake_addr.skey_file],
    )

    try:
        cluster_obj.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=f"rf_{name_template}_dereg_withdraw_stake",
            tx_files=tx_files_deregister,
            withdrawals=withdrawals,
            deposit=-deposit_amt,
        )
    except clusterlib.CLIError:
        LOGGER.error(f"Failed to deregister stake address '{stake_addr.address}'")  # noqa: TRY400
    else:
        LOGGER.debug(f"Deregistered stake address '{stake_addr.address}'")


def retire_drep(
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    drep_keys: clusterlib.KeyPair,
    name_template: str,
    deposit_amt: int,
) -> None:
    """Retire a DRep."""
    ret_cert = cluster_obj.g_conway_governance.drep.gen_retirement_cert(
        cert_name=f"rf_{name_template}",
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
            tx_name=f"rf_{name_template}_retire_drep",
            tx_files=tx_files,
            deposit=-deposit_amt,
        )
    except clusterlib.CLIError:
        LOGGER.error(f"Failed to retire a DRep '{name_template}'")  # noqa: TRY400
    else:
        LOGGER.debug(f"Retired a DRep '{name_template}'")


def get_tx_inputs(
    cluster_obj: clusterlib.ClusterLib, src_addrs: list[clusterlib.AddressRecord]
) -> TxInputGroup:
    """Return signing keys and transaction inputs for given addresses.

    Exclude UTxOs that contain tokens.
    Don't exclude UTxOs with datum. All the UTxOs here has 'skey', and so the UTxOs with datum
    are spendable reference inputs.
    """
    recs = []
    for a in src_addrs:
        utxos = cluster_obj.g_query.get_utxo(address=a.address)
        utxos_ids_excluded = {
            f"{u.utxo_hash}#{u.utxo_ix}" for u in utxos if u.coin != clusterlib.DEFAULT_COIN
        }
        txins_ok = [u for u in utxos if f"{u.utxo_hash}#{u.utxo_ix}" not in utxos_ids_excluded]
        if txins_ok:
            recs.append((txins_ok, a.skey_file))

    return recs


def dedup_tx_inputs(tx_inputs: TxInputGroup) -> TxInputGroup:
    """Deduplicate transaction inputs."""
    seen_paths = set()
    unique_inputs = []

    for utxos, path in tx_inputs:
        if path not in seen_paths:
            seen_paths.add(path)
            unique_inputs.append((utxos, path))

    return unique_inputs


def batch_tx_inputs(
    tx_inputs: TxInputGroup, batch_size: int = 100
) -> tp.Generator[TxInputGroup, None, None]:
    """Batch transaction inputs."""
    current_batch: TxInputGroup = []
    current_utxo_count = 0
    input_queue = [(list(utxos), path) for utxos, path in tx_inputs]

    while input_queue:
        utxos, path = input_queue.pop(0)
        remaining_space = batch_size - current_utxo_count

        if len(utxos) <= remaining_space:
            current_batch.append((utxos, path))
            current_utxo_count += len(utxos)
        else:
            fitting_utxos = utxos[:remaining_space]
            leftover_utxos = utxos[remaining_space:]
            current_batch.append((fitting_utxos, path))
            current_utxo_count += len(fitting_utxos)
            input_queue.insert(0, (leftover_utxos, path))

        if current_utxo_count >= batch_size:
            yield current_batch
            current_batch = []
            current_utxo_count = 0

    if current_batch:
        yield current_batch


def flatten_tx_inputs(
    tx_inputs: TxInputGroup,
) -> tp.Tuple[list[clusterlib.UTXOData], list[pl.Path]]:
    """Flatten transaction inputs."""
    utxo_lists, skeys = zip(*tx_inputs) if tx_inputs else ([], [])
    return list(itertools.chain.from_iterable(utxo_lists)), list(skeys)


def return_funds_to_faucet(
    cluster_obj: clusterlib.ClusterLib,
    tx_inputs: TxInputGroup,
    faucet_address: str,
    tx_name: str = "",
) -> None:
    """Send funds from `tx_inputs` to `faucet_address`."""
    tx_name = tx_name or helpers.get_timestamped_rand_str()
    tx_name = f"rf_{tx_name}"

    # The amount of "-1" means all available funds.
    fund_dst = [clusterlib.TxOut(address=faucet_address, amount=-1)]

    # Tx inputs deduplication is not strictly needed, as we are deduplicating the
    # address files. Keeping it here for separation of concerns.
    for batch in batch_tx_inputs(tx_inputs=dedup_tx_inputs(tx_inputs=tx_inputs)):
        txins, skeys = flatten_tx_inputs(tx_inputs=batch)
        fund_tx_files = clusterlib.TxFiles(signing_key_files=skeys)

        # Try to return funds; don't mind if there's not enough funds for fees etc.
        try:
            cluster_obj.g_transaction.send_tx(
                src_address=txins[0].address,
                tx_name=tx_name,
                txins=txins,
                txouts=fund_dst,
                tx_files=fund_tx_files,
                verify_tx=False,
            )
        except clusterlib.CLIError:
            LOGGER.exception(f"Failed to return funds from addresses for '{tx_name}'")
        else:
            LOGGER.debug(f"Returned funds from addresses '{tx_name}'")

    cluster_obj.wait_for_new_block(new_blocks=3)


def create_addr_record(addr_file: pl.Path) -> clusterlib.AddressRecord:
    """Return a `clusterlib.AddressRecord`."""
    f_name = addr_file.name.replace(".addr", "")
    basedir = addr_file.parent
    vkey_file = basedir / f"{f_name}.vkey"
    skey_file = basedir / f"{f_name}.skey"

    if not (vkey_file.exists() and skey_file.exists()):
        msg = f"{addr_file}: keys not available"
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


def filter_addr_files(
    file_paths: tp.Generator[pl.Path, None, None],
) -> tp.Generator[pl.Path, None, None]:
    """Skip the '*_pycurrent' symlinks to pytest temp dirs and faucet address."""
    return (
        f
        for f in file_paths
        if "_pycurrent" not in (str_f := str(f)) and not str_f.endswith("faucet.addr")
    )


def dedup_addresses(
    file_paths: tp.Generator[pl.Path, None, None],
) -> tp.Generator[pl.Path, None, None]:
    """Deduplicate addresses."""
    seen_addrs = set()
    for fpath in file_paths:
        address = clusterlib.read_address_from_file(fpath)
        if address in seen_addrs:
            continue
        seen_addrs.add(address)
        yield fpath


def cleanup_addresses(
    cluster_obj: clusterlib.ClusterLib, location: pl.Path, faucet_payment: clusterlib.AddressRecord
) -> None:
    """Cleanup addresses."""
    files_found = list(dedup_addresses(filter_addr_files(find_addr_files(location))))
    num_threads = min(10, (len(files_found) // 200) + 1)
    file_chunks = [files_found[i::num_threads] for i in range(num_threads)]

    # Fund the addresses that will pay for fees
    fund_addrs = [
        cluster_obj.g_address.gen_payment_addr_and_keys(name=f"addrs_cleanup{i}")
        for i in range(num_threads)
    ]
    fund_dst = [clusterlib.TxOut(address=f.address, amount=300_000_000) for f in fund_addrs]
    fund_tx_files = clusterlib.TxFiles(signing_key_files=[faucet_payment.skey_file])
    cluster_obj.g_transaction.send_tx(
        src_address=faucet_payment.address,
        tx_name="fund_addrs_cleanup",
        txouts=fund_dst,
        tx_files=fund_tx_files,
    )

    stake_deposit_amt = cluster_obj.g_query.get_address_deposit()

    def _run(files: list[pl.Path], f_addr: clusterlib.AddressRecord) -> TxInputGroup:
        tx_inputs = []
        for fpath in files:
            f_name = fpath.name

            # Add random sleep for < 0.5s to prevent
            # "Network.Socket.connect: <socket: 11>: resource exhausted"
            time.sleep(random.random() / 2)

            if f_name.endswith("_stake.addr"):
                try:
                    stake_addr = create_addr_record(fpath)
                except ValueError as exc:
                    LOGGER.debug(f"Skipping: {exc}")
                    continue

                stake_addr_info = reregister_stake_addr(
                    cluster_obj=cluster_obj,
                    payment_addr=f_addr,
                    stake_addr=stake_addr,
                    stake_addr_info=cluster_obj.g_query.get_stake_addr_info(stake_addr.address),
                    name_template=f_name,
                    deposit_amt=stake_deposit_amt,
                )

                deregister_stake_addr(
                    cluster_obj=cluster_obj,
                    payment_addr=f_addr,
                    stake_addr=stake_addr,
                    stake_addr_info=stake_addr_info,
                    name_template=f_name,
                    deposit_amt=stake_deposit_amt,
                )
            else:
                try:
                    payment_addr = create_addr_record(fpath)
                except ValueError as exc:
                    LOGGER.debug(f"Skipping: {exc}")
                    continue
                tx_inputs.extend(get_tx_inputs(cluster_obj=cluster_obj, src_addrs=[payment_addr]))

        return tx_inputs

    # Run cleanup / discovery in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_run, ch, fund_addrs[i]) for i, ch in enumerate(file_chunks)]
    results = [f.result() for f in futures]

    # Return funds from the collected UTxOs
    tx_inputs = list(itertools.chain.from_iterable(results))
    tx_inputs.extend(get_tx_inputs(cluster_obj=cluster_obj, src_addrs=fund_addrs))
    return_funds_to_faucet(
        cluster_obj=cluster_obj,
        tx_inputs=tx_inputs,
        faucet_address=faucet_payment.address,
    )


def cleanup_certs(
    cluster_obj: clusterlib.ClusterLib, location: pl.Path, faucet_payment: clusterlib.AddressRecord
) -> None:
    """Cleanup certificates."""
    files_found = list(find_cert_files(location))
    num_threads = min(10, (len(files_found) // 10) + 1)
    file_chunks = [files_found[i::num_threads] for i in range(num_threads)]

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

    def _run(files: list[pl.Path], payment_addr: clusterlib.AddressRecord) -> None:
        for cert_file in files:
            # Add random sleep for < 0.5s to prevent
            # "Network.Socket.connect: <socket: 11>: resource exhausted"
            time.sleep(random.random() / 2)

            f_name = cert_file.name
            f_dir = cert_file.parent
            vkey_file = f_dir / cert_file.name.replace("_reg.cert", ".vkey")
            skey_file = vkey_file.with_suffix(".skey")
            drep_keys = clusterlib.KeyPair(vkey_file=vkey_file, skey_file=skey_file)

            retire_drep(
                cluster_obj=cluster_obj,
                payment_addr=payment_addr,
                drep_keys=drep_keys,
                name_template=f_name,
                deposit_amt=drep_deposit_amt,
            )
            # We don't need to deregister pools, because all tests clean the pools they created

    # Run cleanup in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_run, f, fund_addrs[i]) for i, f in enumerate(file_chunks)]
        concurrent.futures.wait(futures)

    # Return funds from the addresses that paid for fees
    tx_inputs = get_tx_inputs(cluster_obj=cluster_obj, src_addrs=fund_addrs)
    return_funds_to_faucet(
        cluster_obj=cluster_obj,
        tx_inputs=tx_inputs,
        faucet_address=faucet_payment.address,
        tx_name="certs_cleanup_return",
    )


def _get_faucet_payment_rec(
    address: str = "",
    skey_file: clusterlib.FileType = "",
) -> clusterlib.AddressRecord:
    """Get the faucet payment record.

    If address or skey_file is provided, use them to create the record.
    Otherwise, infer the faucet address and keys from the cluster environment.
    """
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
    files_found = list(filter_addr_files(find_addr_files(location)))

    seen_addrs = set()
    for fpath in files_found:
        f_name = fpath.name
        if f_name == "faucet.addr":
            continue

        address = clusterlib.read_address_from_file(fpath)
        if address in seen_addrs:
            continue
        seen_addrs.add(address)

        # Add sleep to prevent
        # "Network.Socket.connect: <socket: 11>: resource exhausted"
        time.sleep(0.1)

        if f_name.endswith("_stake.addr"):
            stake_addr_info = cluster_obj.g_query.get_stake_addr_info(address)
            if not stake_addr_info:
                continue

            f_rewards = stake_addr_info.reward_account_balance
            if f_rewards:
                rewards += f_rewards
                LOGGER.info(f"{f_rewards / 1_000_000} ADA on '{fpath}'")
        else:
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
