"""Cleanup a testnet with the help of testing artifacts.

* Withdraw rewards
* Deregister stake addresses
* Retire DReps
* Return funds to faucet
"""

import concurrent.futures
import dataclasses
import functools
import itertools
import json
import logging
import pathlib as pl
import random
import time
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import defragment_utxos
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view

LOGGER = logging.getLogger(__name__)

TxInputGroup = list[tuple[list[clusterlib.UTXOData], pl.Path]]

# Leave ample room for transaction overhead and payment inputs. Oversized or stale
# certificate batches are split further on submission failure.
CERT_BATCH_SIZE = 20
QUERY_BATCH_SIZE = 100


@dataclasses.dataclass
class _CertificateAction:
    """Certificates, witnesses and balance changes for one cleanup credential."""

    address: str
    certificates: list[pl.Path]
    signing_keys: list[pl.Path]
    deposit: int = 0
    withdrawals: list[clusterlib.TxOut] = dataclasses.field(default_factory=list)


def _submit_certificate_batches(
    *,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    actions: list[_CertificateAction],
    name_template: str,
) -> list[_CertificateAction]:
    """Submit bounded certificate batches, isolating rejected actions by splitting.

    Keep confirmation enabled: subsequent batches spend the funding address's change.
    Do not split after an uncertain confirmation failure, since the original transaction
    might still be applied. Return only actions whose transactions were confirmed.
    """
    successful = []

    def _submit(batch: list[_CertificateAction], name: str) -> None:
        tx_files = clusterlib.TxFiles(
            certificate_files=list(itertools.chain.from_iterable(a.certificates for a in batch)),
            signing_key_files=list(
                dict.fromkeys(
                    [
                        payment_addr.skey_file,
                        *itertools.chain.from_iterable(a.signing_keys for a in batch),
                    ]
                )
            ),
        )
        try:
            cluster_obj.g_transaction.send_tx(
                src_address=payment_addr.address,
                tx_name=name,
                tx_files=tx_files,
                withdrawals=list(itertools.chain.from_iterable(a.withdrawals for a in batch)),
                deposit=sum(a.deposit for a in batch),
                witness_count_add=2,
            )
        except clusterlib.CLIError as exc:
            uncertain = "didn't make it to the chain" in str(exc) or "Failed to resubmit" in str(
                exc
            )
            if len(batch) == 1 or uncertain:
                LOGGER.exception(f"Failed cleanup batch '{name}': {[a.address for a in batch]}")
                return
            LOGGER.warning(f"Splitting rejected cleanup batch '{name}' ({len(batch)} credentials)")
            midpoint = len(batch) // 2
            _submit(batch[:midpoint], f"{name}_0")
            _submit(batch[midpoint:], f"{name}_1")
        else:
            successful.extend(batch)
            LOGGER.info(f"Confirmed cleanup batch '{name}' ({len(batch)} credentials)")

    for i, batch in enumerate(itertools.batched(actions, CERT_BATCH_SIZE, strict=False)):
        _submit(list(batch), f"rf_{name_template}_batch{i}")
    LOGGER.info(f"{name_template}: confirmed {len(successful)}/{len(actions)} credential actions")
    return successful


def _stake_registration_action(
    *,
    cluster_obj: clusterlib.ClusterLib,
    stake_addr: clusterlib.AddressRecord,
    stake_addr_info: clusterlib.StakeAddrInfo,
    name_template: str,
    deposit_amt: int,
) -> _CertificateAction | None:
    """Prepare re-registration and vote delegation when needed.

    Preserve re-registration of unqueryable accounts so any remaining rewards can be
    discovered after registration. Rewards require vote delegation before withdrawal.
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

    if not rereg_certs:
        return None
    return _CertificateAction(
        address=stake_addr.address,
        certificates=rereg_certs,
        signing_keys=[stake_addr.skey_file],
        deposit=rereg_deposit,
    )


def _stake_deregistration_action(
    *,
    cluster_obj: clusterlib.ClusterLib,
    stake_addr: clusterlib.AddressRecord,
    stake_addr_info: clusterlib.StakeAddrInfo,
    name_template: str,
    deposit_amt: int,
) -> _CertificateAction:
    """Prepare a reward withdrawal and stake deregistration."""
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
    return _CertificateAction(
        address=stake_addr.address,
        certificates=[stake_addr_dereg_cert],
        signing_keys=[stake_addr.skey_file],
        withdrawals=withdrawals,
        deposit=-deposit_amt,
    )


def _drep_retirement_action(
    *,
    cluster_obj: clusterlib.ClusterLib,
    drep_keys: clusterlib.KeyPair,
    name_template: str,
    deposit_amt: int,
) -> _CertificateAction:
    """Prepare a DRep retirement and deposit refund."""
    ret_cert = cluster_obj.g_governance.drep.gen_retirement_cert(
        cert_name=f"rf_{name_template}",
        deposit_amt=deposit_amt,
        drep_vkey_file=drep_keys.vkey_file,
    )
    return _CertificateAction(
        address=name_template,
        certificates=[ret_cert],
        signing_keys=[drep_keys.skey_file],
        deposit=-deposit_amt,
    )


def get_tx_inputs(
    *, cluster_obj: clusterlib.ClusterLib, src_addrs: list[clusterlib.AddressRecord]
) -> TxInputGroup:
    """Return signing keys and transaction inputs for given addresses.

    Exclude UTxOs that contain tokens.
    Don't exclude UTxOs with datum. All the UTxOs here has 'skey', and so the UTxOs with datum
    are spendable reference inputs.
    """
    recs: TxInputGroup = []
    for addresses in itertools.batched(src_addrs, QUERY_BATCH_SIZE, strict=False):
        utxos = cluster_obj.g_query.get_utxo(address=[a.address for a in addresses])
        utxos_ids_excluded = {
            f"{u.utxo_hash}#{u.utxo_ix}" for u in utxos if u.coin != clusterlib.DEFAULT_COIN
        }
        by_address: dict[str, list[clusterlib.UTXOData]] = {}
        for utxo in utxos:
            if f"{utxo.utxo_hash}#{utxo.utxo_ix}" not in utxos_ids_excluded:
                by_address.setdefault(utxo.address, []).append(utxo)
        recs.extend(
            (by_address[a.address], a.skey_file) for a in addresses if a.address in by_address
        )

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
    tx_inputs: TxInputGroup, *, batch_size: int = 100
) -> tp.Generator[TxInputGroup]:
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
    *,
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
    submitted_inputs = []
    batch_num = 1
    for batch in batch_tx_inputs(tx_inputs=dedup_tx_inputs(tx_inputs=tx_inputs)):
        txins, skeys = flatten_tx_inputs(tx_inputs=batch)
        fund_tx_files = clusterlib.TxFiles(signing_key_files=skeys)
        batch_tx_name = f"{tx_name}_batch{batch_num}"
        witness_count_add = max(2, len(skeys) // 20)

        # Try to return funds
        last_excp = None
        for attempt in range(1, 4):
            try:
                tx_output = cluster_obj.g_transaction.send_tx(
                    src_address=txins[0].address,
                    tx_name=f"{batch_tx_name}_try{attempt}",
                    txins=txins,
                    txouts=fund_dst,
                    tx_files=fund_tx_files,
                    witness_count_add=witness_count_add,
                    verify_tx=False,
                )
            except clusterlib.CLIError as excp:
                last_excp = excp
                if "FeeTooSmallUTxO" in str(excp):
                    witness_count_add += 5
                    continue
                LOGGER.exception(f"Failed to return funds from addresses for '{batch_tx_name}'")
            else:
                submitted_inputs.append(tx_output.txins[0])
                LOGGER.debug(f"Returned funds from addresses '{batch_tx_name}'")
            break
        else:
            LOGGER.error(
                f"Failed to return funds from addresses for '{batch_tx_name}'", exc_info=last_excp
            )
        batch_num += 1

    if submitted_inputs:
        try:
            clusterlib_utils.check_txins_spent(cluster_obj=cluster_obj, txins=submitted_inputs)
        except AssertionError:
            LOGGER.exception("Some return-funds transactions were not confirmed")
    LOGGER.info(f"Returned funds: submitted {len(submitted_inputs)} transactions")


def create_addr_record(*, addr_file: pl.Path) -> clusterlib.AddressRecord:
    """Return a `clusterlib.AddressRecord`."""
    f_name = addr_file.name.replace(".addr", "")
    basedir = addr_file.parent
    vkey_file = basedir / f"{f_name}.vkey"
    skey_file = basedir / f"{f_name}.skey"

    if not (vkey_file.exists() and skey_file.exists()):
        err = f"{addr_file}: keys not available"
        raise ValueError(err)

    addr_record = clusterlib.AddressRecord(
        address=clusterlib.read_address_from_file(addr_file),
        vkey_file=vkey_file,
        skey_file=skey_file,
    )
    return addr_record


def find_addr_files(location: pl.Path) -> tp.Iterator[pl.Path]:
    r"""Find all '\*.addr' files in given location and its subdirectories."""
    return location.glob("**/*.addr")


def find_submitted_tx_files(location: pl.Path) -> tp.Iterator[pl.Path]:
    r"""Find all '\*.submitted' files in given location and its subdirectories."""
    return location.glob("**/*.submitted")


def find_cert_files(location: pl.Path) -> tp.Iterator[pl.Path]:
    r"""Find all '\*_drep_reg.cert' files in given location and its subdirectories."""
    return location.glob("**/*_drep_reg.cert")


def filter_addr_files(
    file_paths: tp.Iterator[pl.Path],
) -> tp.Iterator[pl.Path]:
    r"""Skip the '\*_pycurrent' symlinks to pytest temp dirs and faucet address."""
    return (
        f
        for f in file_paths
        if "_pycurrent" not in (str_f := str(f)) and not str_f.endswith("faucet.addr")
    )


def dedup_addresses(
    file_paths: tp.Iterator[pl.Path],
) -> tp.Iterator[pl.Path]:
    """Deduplicate addresses."""
    seen_addrs = set()
    for fpath in file_paths:
        address = clusterlib.read_address_from_file(fpath)
        if address in seen_addrs:
            continue
        seen_addrs.add(address)
        yield fpath


def _fund_cleanup_addresses(
    *,
    cluster_obj: clusterlib.ClusterLib,
    faucet_payment: clusterlib.AddressRecord,
    count: int,
    name_template: str,
) -> list[clusterlib.AddressRecord]:
    """Fund exactly the workers needed for certificate cleanup."""
    if not count:
        return []
    fund_addrs = [
        cluster_obj.g_address.gen_payment_addr_and_keys(name=f"{name_template}{i}")
        for i in range(count)
    ]
    cluster_obj.g_transaction.send_tx(
        src_address=faucet_payment.address,
        tx_name=f"fund_{name_template}",
        txouts=[clusterlib.TxOut(address=f.address, amount=300_000_000) for f in fund_addrs],
        tx_files=clusterlib.TxFiles(signing_key_files=[faucet_payment.skey_file]),
    )
    return fund_addrs


def _query_registered_stake_addresses(
    *, cluster_obj: clusterlib.ClusterLib, registered: list[_CertificateAction]
) -> dict[str, clusterlib.StakeAddrInfo]:
    """Query newly registered accounts, retrying lagging visibility checks together."""
    pending = {a.address for a in registered}
    stake_info = {}
    for attempt in range(3):
        if not pending:
            break
        if attempt:
            cluster_obj.wait_for_new_block()
        for address in sorted(pending):
            info = cluster_obj.g_query.get_stake_addr_info(address)
            if info and info.vote_delegation:
                stake_info[address] = info
                pending.remove(address)
    return stake_info


def _cleanup_stake_addresses(
    *,
    cluster_obj: clusterlib.ClusterLib,
    stake_addrs: list[clusterlib.AddressRecord],
    payment_addr: clusterlib.AddressRecord,
    deposit_amt: int,
    name_template: str,
) -> None:
    """Re-register, discover rewards, then withdraw and deregister in batches."""
    # Recycle registration deposits before starting another batch. Otherwise a large
    # artifact directory could exhaust a worker's funding during re-registration.
    if len(stake_addrs) > CERT_BATCH_SIZE:
        for i, batch in enumerate(itertools.batched(stake_addrs, CERT_BATCH_SIZE, strict=False)):
            _cleanup_stake_addresses(
                cluster_obj=cluster_obj,
                stake_addrs=list(batch),
                payment_addr=payment_addr,
                deposit_amt=deposit_amt,
                name_template=f"{name_template}_part{i}",
            )
        return
    stake_info = {}
    registrations = []
    for i, stake_addr in enumerate(stake_addrs):
        # Limit concurrent node connections during discovery, not payment UTxO batching.
        time.sleep(random.random() / 2)
        info = cluster_obj.g_query.get_stake_addr_info(stake_addr.address)
        stake_info[stake_addr.address] = info
        action = _stake_registration_action(
            cluster_obj=cluster_obj,
            stake_addr=stake_addr,
            stake_addr_info=info,
            name_template=f"{name_template}_{i}",
            deposit_amt=deposit_amt,
        )
        if action:
            registrations.append(action)

    registered = _submit_certificate_batches(
        cluster_obj=cluster_obj,
        payment_addr=payment_addr,
        actions=registrations,
        name_template=f"{name_template}_rereg",
    )
    # Previously unregistered accounts may now expose rewards.
    registered_info = _query_registered_stake_addresses(
        cluster_obj=cluster_obj, registered=registered
    )
    stake_info.update(registered_info)
    needs_registration = {a.address for a in registrations}
    deregistrations = []
    for i, stake_addr in enumerate(stake_addrs):
        if stake_addr.address in needs_registration and stake_addr.address not in registered_info:
            LOGGER.error(f"Skipping unconfirmed stake registration '{stake_addr.address}'")
            continue
        info = stake_info[stake_addr.address]
        refund = info.registration_deposit if info.registration_deposit >= 0 else deposit_amt
        deregistrations.append(
            _stake_deregistration_action(
                cluster_obj=cluster_obj,
                stake_addr=stake_addr,
                stake_addr_info=info,
                name_template=f"{name_template}_{i}",
                deposit_amt=refund,
            )
        )
    _submit_certificate_batches(
        cluster_obj=cluster_obj,
        payment_addr=payment_addr,
        actions=deregistrations,
        name_template=f"{name_template}_dereg",
    )


def cleanup_addresses(
    *,
    cluster_obj: clusterlib.ClusterLib,
    location: pl.Path,
    faucet_payment: clusterlib.AddressRecord,
) -> None:
    """Discover payment inputs and clean stake accounts using bounded batches."""
    started = time.monotonic()
    files_found = list(dedup_addresses(filter_addr_files(find_addr_files(location))))
    payment_addrs = []
    stake_addrs = []
    for fpath in files_found:
        try:
            address = create_addr_record(addr_file=fpath)
        except ValueError as exc:
            LOGGER.debug(f"Skipping: {exc}")
            continue
        if fpath.name.endswith("_stake.addr"):
            stake_addrs.append(address)
        else:
            payment_addrs.append(address)
    LOGGER.info(
        f"Address discovery: {len(payment_addrs)} payment, {len(stake_addrs)} stake accounts"
    )
    tx_inputs = get_tx_inputs(cluster_obj=cluster_obj, src_addrs=payment_addrs)
    LOGGER.info(f"Payment UTxO discovery finished in {time.monotonic() - started:.1f}s")

    num_threads = min(10, (len(stake_addrs) + CERT_BATCH_SIZE - 1) // CERT_BATCH_SIZE)
    fund_addrs = _fund_cleanup_addresses(
        cluster_obj=cluster_obj,
        faucet_payment=faucet_payment,
        count=num_threads,
        name_template="addrs_cleanup",
    )
    if stake_addrs:
        stake_started = time.monotonic()
        stake_deposit_amt = cluster_obj.g_query.get_address_deposit()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [
                executor.submit(
                    _cleanup_stake_addresses,
                    cluster_obj=cluster_obj,
                    stake_addrs=stake_addrs[i::num_threads],
                    payment_addr=addr,
                    deposit_amt=stake_deposit_amt,
                    name_template=f"stake_cleanup{i}",
                )
                for i, addr in enumerate(fund_addrs)
            ]
            for future in futures:
                future.result()
        LOGGER.info(f"Stake cleanup finished in {time.monotonic() - stake_started:.1f}s")

    tx_inputs.extend(get_tx_inputs(cluster_obj=cluster_obj, src_addrs=fund_addrs))
    return_funds_to_faucet(
        cluster_obj=cluster_obj,
        tx_inputs=tx_inputs,
        faucet_address=faucet_payment.address,
    )
    LOGGER.info(f"Address cleanup finished in {time.monotonic() - started:.1f}s")


def _find_drep_keys(location: pl.Path) -> list[clusterlib.KeyPair]:
    """Find unique DRep keys, skipping missing or unreadable artifacts."""
    keys = []
    seen_keys = set()
    for cert_file in find_cert_files(location):
        vkey_file = cert_file.parent / cert_file.name.replace("_reg.cert", ".vkey")
        skey_file = vkey_file.with_suffix(".skey")
        if not (vkey_file.exists() and skey_file.exists()):
            LOGGER.debug(f"Skipping DRep with missing keys: {cert_file}")
            continue
        try:
            identity = json.loads(vkey_file.read_text())["cborHex"]
        except (OSError, ValueError, KeyError):
            LOGGER.exception(f"Skipping unreadable DRep key: {vkey_file}")
            continue
        if identity in seen_keys:
            continue
        seen_keys.add(identity)
        keys.append(clusterlib.KeyPair(vkey_file=vkey_file, skey_file=skey_file))
    return keys


def cleanup_certs(
    *,
    cluster_obj: clusterlib.ClusterLib,
    location: pl.Path,
    faucet_payment: clusterlib.AddressRecord,
) -> None:
    """Retire registered DReps in batches, skipping duplicates and missing keys."""
    started = time.monotonic()
    keys = _find_drep_keys(location)
    if not keys:
        LOGGER.info("No DRep keys to clean up")
        return

    drep_deposit_amt = cluster_obj.g_query.get_drep_deposit()

    def _discover(item: tuple[int, clusterlib.KeyPair]) -> _CertificateAction | None:
        i, drep_keys = item
        time.sleep(random.random() / 2)
        try:
            state = cluster_obj.g_query.get_drep_state(drep_vkey_file=drep_keys.vkey_file)
        except clusterlib.CLIError:
            LOGGER.exception(f"Failed to query DRep: {drep_keys.vkey_file}")
            return None
        if not state:
            return None
        return _drep_retirement_action(
            cluster_obj=cluster_obj,
            drep_keys=drep_keys,
            name_template=f"drep_cleanup{i}",
            deposit_amt=state[0][1].get("deposit", drep_deposit_amt),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(keys))) as executor:
        actions = [a for a in executor.map(_discover, enumerate(keys)) if a is not None]
    LOGGER.info(
        f"DRep discovery: {len(actions)}/{len(keys)} registered, "
        f"finished in {time.monotonic() - started:.1f}s"
    )
    if not actions:
        return

    num_threads = min(10, (len(actions) + CERT_BATCH_SIZE - 1) // CERT_BATCH_SIZE)
    fund_addrs = _fund_cleanup_addresses(
        cluster_obj=cluster_obj,
        faucet_payment=faucet_payment,
        count=num_threads,
        name_template="certs_cleanup",
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [
            executor.submit(
                _submit_certificate_batches,
                cluster_obj=cluster_obj,
                payment_addr=addr,
                actions=actions[i::num_threads],
                name_template=f"drep_cleanup{i}_retire",
            )
            for i, addr in enumerate(fund_addrs)
        ]
        for future in futures:
            future.result()

    # Return funds from the addresses that paid for fees
    tx_inputs = get_tx_inputs(cluster_obj=cluster_obj, src_addrs=fund_addrs)
    return_funds_to_faucet(
        cluster_obj=cluster_obj,
        tx_inputs=tx_inputs,
        faucet_address=faucet_payment.address,
        tx_name="certs_cleanup_return",
    )
    LOGGER.info(f"Certificate cleanup finished in {time.monotonic() - started:.1f}s")


def is_framework_testnet() -> bool:
    """Determine if the testnet is a testnet started by the testing framework."""
    try:
        helpers.check_cardano_node_socket_path()
    except ValueError:
        return False
    return True


def _get_faucet_payment_rec(
    *, address: str = "", skey_file: clusterlib.FileType = ""
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
    elif is_framework_testnet():
        # Try to infer the faucet address and keys from cluster env
        cluster_env = cluster_nodes.get_cluster_env()
        faucet_addr_file = cluster_env.state_dir / "shelley" / "faucet.addr"
        if not faucet_addr_file.exists():
            err = f"Faucet address file not found at '{faucet_addr_file}'."
            raise FileNotFoundError(err)
        faucet_payment = create_addr_record(addr_file=faucet_addr_file)
    else:
        err = "Faucet address and keys need to be provided for non-framework testnets."
        raise RuntimeError(err)

    return faucet_payment


def addresses_info(*, cluster_obj: clusterlib.ClusterLib, location: pl.Path) -> tp.Tuple[int, int]:
    """Return the total balance and rewards of all addresses in the given location."""
    balance = 0
    rewards = 0
    files_found = list(filter_addr_files(find_addr_files(location)))

    seen_addrs = set()
    for fpath in files_found:
        f_name = fpath.name

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


def fees_info(*, cluster_obj: clusterlib.ClusterLib, location: pl.Path) -> int:
    """Return the total fees of all signed transactions in the given location."""
    fees = 0
    for fpath in find_submitted_tx_files(location):
        tx_loaded = tx_view.load_tx_view(cluster_obj=cluster_obj, tx_body_file=fpath)
        tx_fee = int(tx_loaded.get("fee", "0 Lovelace").split()[0])
        LOGGER.info(f"{tx_fee} Lovelace fee on '{fpath}'")
        fees += tx_fee
    return fees


def cleanup(
    *,
    cluster_obj: clusterlib.ClusterLib,
    location: clusterlib.FileType,
    faucet_address: str = "",
    faucet_skey_file: clusterlib.FileType = "",
) -> None:
    """Cleanup a testnet with the help of testing artifacts."""
    started = time.monotonic()
    location = pl.Path(location).expanduser().resolve()
    faucet_payment = _get_faucet_payment_rec(address=faucet_address, skey_file=faucet_skey_file)

    cleanup_faucet_balance = cluster_obj.g_query.get_address_balance(address=faucet_payment.address)
    LOGGER.info(f"Cleanup faucet balance: {cleanup_faucet_balance}")

    cleanup_addresses(cluster_obj=cluster_obj, location=location, faucet_payment=faucet_payment)
    cleanup_certs(cluster_obj=cluster_obj, location=location, faucet_payment=faucet_payment)

    # Defragment faucet address UTxOs
    defrag_started = time.monotonic()
    defragment_utxos.defragment(
        cluster_obj=cluster_obj, address=faucet_payment.address, skey_file=faucet_payment.skey_file
    )
    LOGGER.info(f"Faucet defragmentation finished in {time.monotonic() - defrag_started:.1f}s")

    final_faucet_balance = cluster_obj.g_query.get_address_balance(address=faucet_payment.address)
    LOGGER.info(f"Final faucet balance: {final_faucet_balance}")
    LOGGER.info(f"Cleanup finished in {time.monotonic() - started:.1f}s")
