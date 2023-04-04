"""Functionality for interacting with db-sync."""
import functools
import itertools
import json
import logging
import time
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Union

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_queries

LOGGER = logging.getLogger(__name__)

NO_REPONSE_STR = "No response returned from db-sync:"


class MetadataRecord(NamedTuple):
    key: int
    json: Any
    bytes: memoryview


class ADAStashRecord(NamedTuple):
    address: str
    cert_index: int
    amount: int


class PotTransferRecord(NamedTuple):
    treasury: int
    reserves: int


class DelegationRecord(NamedTuple):
    address: str
    pool_id: str
    active_epoch_no: int


class RewardEpochRecord(NamedTuple):
    amount: int
    earned_epoch: int
    spendable_epoch: int
    type: str
    pool_id: str


class RewardRecord(NamedTuple):
    address: str
    rewards: List[RewardEpochRecord]
    reward_sum: int

    def __bool__(self) -> bool:
        return self.reward_sum > 0


class UTxORecord(NamedTuple):
    utxo_hash: str
    utxo_ix: int
    amount: int
    address: str
    coin: str = clusterlib.DEFAULT_COIN
    decoded_coin: str = ""
    datum_hash: str = ""
    inline_datum_hash: str = ""
    reference_script_hash: str = ""


class GetUTxORecord(NamedTuple):
    utxo_hash: str
    utxo_ix: int
    has_script: bool
    amount: int
    data_hash: str


class PaymentAddrRecord(NamedTuple):
    payment_address: str
    stake_address: Optional[str]
    amount_sum: int
    utxos: List[GetUTxORecord]

    def __bool__(self) -> bool:
        return self.amount_sum > 0


class PoolDataRecord(NamedTuple):
    id: int
    hash: str
    view: str
    cert_index: int
    vrf_key_hash: str
    pledge: int
    reward_addr: str
    active_epoch_no: int
    meta_id: int
    margin: float
    fixed_cost: int
    registered_tx_id: int
    metadata_url: str
    metadata_hash: str
    owners: List[str]
    relays: List[Dict[str, Dict[str, Any]]]
    retire_cert_index: int
    retire_announced_tx_id: int
    retiring_epoch: int


class ScriptRecord(NamedTuple):
    hash: str
    type: str
    serialised_size: int


class RedeemerRecord(NamedTuple):
    unit_mem: int
    unit_steps: int
    fee: int
    purpose: str
    script_hash: str
    value: dict


class ExtraKeyWitnessRecord(NamedTuple):
    tx_hash: str
    witness_hash: str


class TxRecord(NamedTuple):
    tx_id: int
    tx_hash: str
    block_id: int
    block_index: int
    out_sum: int
    fee: int
    deposit: int
    size: int
    invalid_before: Optional[int]
    invalid_hereafter: Optional[int]
    txins: List[clusterlib.UTXOData]
    txouts: List[UTxORecord]
    mint: List[UTxORecord]
    collaterals: List[clusterlib.UTXOData]
    collateral_outputs: List[clusterlib.UTXOData]
    reference_inputs: List[clusterlib.UTXOData]
    scripts: List[ScriptRecord]
    redeemers: List[RedeemerRecord]
    metadata: List[MetadataRecord]
    reserve: List[ADAStashRecord]
    treasury: List[ADAStashRecord]
    pot_transfers: List[PotTransferRecord]
    stake_registration: List[str]
    stake_deregistration: List[str]
    stake_delegation: List[DelegationRecord]
    withdrawals: List[clusterlib.TxOut]
    extra_key_witness: List[ExtraKeyWitnessRecord]

    def _convert_metadata(self) -> dict:
        """Convert list of `MetadataRecord`s to metadata dictionary."""
        metadata = {int(r.key): r.json for r in self.metadata}
        return metadata


class TxPrelimRecord(NamedTuple):
    utxo_out: List[UTxORecord]
    ma_utxo_out: List[UTxORecord]
    mint_utxo_out: List[UTxORecord]
    last_row: dbsync_queries.TxDBRow


def utxodata2txout(utxodata: Union[UTxORecord, clusterlib.UTXOData]) -> clusterlib.TxOut:
    """Convert `UTxORecord` or `UTxOData` to `clusterlib.TxOut`."""
    return clusterlib.TxOut(
        address=utxodata.address,
        amount=utxodata.amount,
        coin=utxodata.coin,
        datum_hash=utxodata.datum_hash,
    )


def get_address_reward(address: str, epoch_from: int = 0, epoch_to: int = 99999999) -> RewardRecord:
    """Get reward data for stake address from db-sync.

    The `epoch_from` and `epoch_to` are epochs where the reward can be spent.
    """
    rewards = []
    for db_row in dbsync_queries.query_address_reward(
        address=address, epoch_from=epoch_from, epoch_to=epoch_to
    ):
        rewards.append(
            RewardEpochRecord(
                amount=int(db_row.amount),
                earned_epoch=db_row.earned_epoch,
                spendable_epoch=db_row.spendable_epoch,
                type=db_row.type,
                pool_id=db_row.pool_id or "",
            )
        )
    if not rewards:
        return RewardRecord(address=address, reward_sum=0, rewards=[])

    reward_sum = functools.reduce(lambda x, y: x + y.amount, rewards, 0)
    # pylint: disable=undefined-loop-variable
    return RewardRecord(address=db_row.address, reward_sum=reward_sum, rewards=rewards)


def check_address_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> RewardRecord:
    """Check reward data for stake address in db-sync.

    The `epoch_from` and `epoch_to` are epochs where the reward can be spent.
    """
    reward = get_address_reward(address=address, epoch_from=epoch_from, epoch_to=epoch_to)
    if not reward:
        return reward

    errors = []
    for r in reward.rewards:
        if r.type in ("member", "leader"):
            if r.spendable_epoch != r.earned_epoch + 2:
                errors.append(f"type == {r.type} and {r.spendable_epoch} != {r.earned_epoch} + 2")
        # transfer from reserves or treasury
        elif r.spendable_epoch != r.earned_epoch + 1:
            errors.append(f"type == {r.type} and {r.spendable_epoch} != {r.earned_epoch} + 1")

    if errors:
        err_str = ", ".join(errors)
        raise AssertionError(f"The 'earned epoch' and 'spendable epoch' don't match: {err_str}.")

    return reward


def get_utxo(address: str) -> PaymentAddrRecord:
    """Return UTxO info for payment address from db-sync."""
    utxos = []
    for db_row in dbsync_queries.query_utxo(address=address):
        utxos.append(
            GetUTxORecord(
                utxo_hash=db_row.tx_hash.hex(),
                utxo_ix=db_row.utxo_ix,
                has_script=db_row.has_script,
                amount=int(db_row.value),
                data_hash=db_row.data_hash.hex() if db_row.data_hash else "",
            )
        )
    if not utxos:
        return PaymentAddrRecord(
            payment_address=address,
            stake_address=None,
            amount_sum=0,
            utxos=[],
        )

    amount_sum = functools.reduce(lambda x, y: x + y.amount, utxos, 0)
    # pylint: disable=undefined-loop-variable
    return PaymentAddrRecord(
        payment_address=db_row.payment_address,
        stake_address=db_row.stake_address,
        amount_sum=amount_sum,
        utxos=utxos,
    )


def get_pool_data(pool_id_bech32: str) -> Optional[PoolDataRecord]:
    """Get pool data from db-sync."""
    pools = list(dbsync_queries.query_pool_data(pool_id_bech32))
    if not pools:
        return None

    known_owners = set()
    single_host_names = []
    single_host_addresses = []

    latest_registered_tx_id = pools[-1].registered_tx_id
    latest_pools = [pool for pool in pools if pool.registered_tx_id == latest_registered_tx_id]

    for pool in latest_pools:
        if pool.owner:
            owner = pool.owner.hex()[2:]
            known_owners.add(owner)
        if pool.dns_name:
            host_name = {"single host name": {"dnsName": pool.dns_name, "port": pool.port}}
            if host_name not in single_host_names:
                single_host_names.append(host_name)
        if pool.ipv4:
            host_address = {
                "single host address": {"IPv4": pool.ipv4, "IPv6": pool.ipv6, "port": pool.port}
            }
            if host_address not in single_host_addresses:
                single_host_addresses.append(host_address)

    # pylint: disable=undefined-loop-variable
    pool_data = PoolDataRecord(
        id=pool.id,
        hash=pool.hash.hex(),
        view=pool.view,
        cert_index=pool.cert_index,
        vrf_key_hash=pool.vrf_key_hash.hex(),
        pledge=int(pool.pledge),
        reward_addr=pool.reward_addr.hex()[2:],
        active_epoch_no=pool.active_epoch_no,
        meta_id=pool.meta_id,
        margin=float(pool.margin),
        fixed_cost=int(pool.fixed_cost),
        registered_tx_id=pool.registered_tx_id,
        metadata_url=str(pool.metadata_url or ""),
        metadata_hash=pool.metadata_hash.hex() if pool.metadata_hash else "",
        owners=list(known_owners),
        relays=[*single_host_names, *single_host_addresses],
        retire_cert_index=pool.retire_cert_index,
        retire_announced_tx_id=pool.retire_announced_tx_id,
        retiring_epoch=pool.retiring_epoch,
    )

    return pool_data


def get_prelim_tx_record(txhash: str) -> TxPrelimRecord:
    """Get first batch of transaction data from db-sync."""
    utxo_out: List[UTxORecord] = []
    seen_tx_out_ids = set()
    ma_utxo_out: List[UTxORecord] = []
    seen_ma_tx_out_ids = set()
    mint_utxo_out: List[UTxORecord] = []
    seen_ma_tx_mint_ids = set()
    tx_id = -1

    for query_row in dbsync_queries.query_tx(txhash=txhash):
        if tx_id == -1:
            tx_id = query_row.tx_id
        if tx_id != query_row.tx_id:
            raise AssertionError("Transaction ID differs from the expected ID.")

        # Lovelace outputs
        if query_row.tx_out_id and query_row.tx_out_id not in seen_tx_out_ids:
            seen_tx_out_ids.add(query_row.tx_out_id)
            out_rec = UTxORecord(
                utxo_hash=str(txhash),
                utxo_ix=int(query_row.utxo_ix),
                amount=int(query_row.tx_out_value),
                address=str(query_row.tx_out_addr),
                datum_hash=query_row.tx_out_data_hash.hex() if query_row.tx_out_data_hash else "",
                inline_datum_hash=query_row.tx_out_inline_datum_hash.hex()
                if query_row.tx_out_inline_datum_hash
                else "",
                reference_script_hash=query_row.tx_out_reference_script_hash.hex()
                if query_row.tx_out_reference_script_hash
                else "",
            )
            utxo_out.append(out_rec)

        # MA outputs
        if query_row.ma_tx_out_id and query_row.ma_tx_out_id not in seen_ma_tx_out_ids:
            seen_ma_tx_out_ids.add(query_row.ma_tx_out_id)
            asset_name = query_row.ma_tx_out_name.hex() if query_row.ma_tx_out_name else None
            policyid = query_row.ma_tx_out_policy.hex() if query_row.ma_tx_out_policy else ""
            coin = f"{policyid}.{asset_name}" if asset_name else policyid
            ma_rec = UTxORecord(
                utxo_hash=str(txhash),
                utxo_ix=int(query_row.utxo_ix),
                amount=int(query_row.ma_tx_out_quantity or 0),
                address=str(query_row.tx_out_addr),
                coin=coin,
                datum_hash=query_row.tx_out_data_hash.hex() if query_row.tx_out_data_hash else "",
            )
            ma_utxo_out.append(ma_rec)

        # MA minting
        if query_row.ma_tx_mint_id and query_row.ma_tx_mint_id not in seen_ma_tx_mint_ids:
            seen_ma_tx_mint_ids.add(query_row.ma_tx_mint_id)
            asset_name = query_row.ma_tx_mint_name.hex() if query_row.ma_tx_mint_name else None
            policyid = query_row.ma_tx_mint_policy.hex() if query_row.ma_tx_mint_policy else ""
            coin = f"{policyid}.{asset_name}" if asset_name else policyid
            mint_rec = UTxORecord(
                utxo_hash=str(txhash),
                utxo_ix=int(query_row.utxo_ix),
                amount=int(query_row.ma_tx_mint_quantity or 0),
                address="",  # this is available only for MA outputs
                coin=coin,
            )
            mint_utxo_out.append(mint_rec)

    if tx_id == -1:
        raise RuntimeError("No results were returned by the TX SQL query.")

    # pylint: disable=undefined-loop-variable
    txdata = TxPrelimRecord(
        utxo_out=utxo_out,
        ma_utxo_out=ma_utxo_out,
        mint_utxo_out=mint_utxo_out,
        last_row=query_row,
    )

    return txdata


def get_txins(txhash: str) -> List[clusterlib.UTXOData]:
    """Get txins of a transaction from db-sync."""
    txins: List[clusterlib.UTXOData] = []
    seen_txins_out_ids = set()
    seen_txins_ma_ids = set()

    for txins_row in dbsync_queries.query_tx_ins(txhash=txhash):
        # Lovelace inputs
        if txins_row.tx_out_id and txins_row.tx_out_id not in seen_txins_out_ids:
            seen_txins_out_ids.add(txins_row.tx_out_id)
            txins.append(
                clusterlib.UTXOData(
                    utxo_hash=txins_row.tx_hash.hex(),
                    utxo_ix=int(txins_row.utxo_ix),
                    amount=int(txins_row.value),
                    address=str(txins_row.address),
                )
            )

        # MA inputs
        if txins_row.ma_tx_out_id and txins_row.ma_tx_out_id not in seen_txins_ma_ids:
            seen_txins_ma_ids.add(txins_row.ma_tx_out_id)
            asset_name = txins_row.ma_tx_out_name.hex() if txins_row.ma_tx_out_name else None
            policyid = txins_row.ma_tx_out_policy.hex() if txins_row.ma_tx_out_policy else ""
            coin = f"{policyid}.{asset_name}" if asset_name else policyid
            txins.append(
                clusterlib.UTXOData(
                    utxo_hash=txins_row.tx_hash.hex(),
                    utxo_ix=int(txins_row.utxo_ix),
                    amount=int(txins_row.ma_tx_out_quantity or 0),
                    address=str(txins_row.address),
                    coin=coin,
                )
            )

    return txins


def get_tx_record(txhash: str) -> TxRecord:  # noqa: C901
    """Get transaction data from db-sync.

    Compile data from multiple SQL queries to get as much information about the TX as possible.
    """
    # pylint: disable=too-many-branches
    txdata = get_prelim_tx_record(txhash)
    txins = get_txins(txhash)

    metadata = []
    if txdata.last_row.metadata_count:
        metadata = [
            MetadataRecord(key=int(r.key), json=r.json, bytes=r.bytes)
            for r in dbsync_queries.query_tx_metadata(txhash=txhash)
        ]

    reserve = []
    if txdata.last_row.reserve_count:
        reserve = [
            ADAStashRecord(
                address=str(r.addr_view), cert_index=int(r.cert_index), amount=int(r.amount)
            )
            for r in dbsync_queries.query_tx_reserve(txhash=txhash)
        ]

    treasury = []
    if txdata.last_row.treasury_count:
        treasury = [
            ADAStashRecord(
                address=str(r.addr_view), cert_index=int(r.cert_index), amount=int(r.amount)
            )
            for r in dbsync_queries.query_tx_treasury(txhash=txhash)
        ]

    pot_transfers = []
    if txdata.last_row.pot_transfer_count:
        pot_transfers = [
            PotTransferRecord(treasury=int(r.treasury), reserves=int(r.reserves))
            for r in dbsync_queries.query_tx_pot_transfers(txhash=txhash)
        ]

    stake_registration = []
    if txdata.last_row.stake_reg_count:
        stake_registration = [r.view for r in dbsync_queries.query_tx_stake_reg(txhash=txhash)]

    stake_deregistration = []
    if txdata.last_row.stake_dereg_count:
        stake_deregistration = [r.view for r in dbsync_queries.query_tx_stake_dereg(txhash=txhash)]

    stake_delegation = []
    if txdata.last_row.stake_deleg_count:
        stake_delegation = [
            DelegationRecord(
                address=r.address, pool_id=r.pool_id, active_epoch_no=r.active_epoch_no
            )
            for r in dbsync_queries.query_tx_stake_deleg(txhash=txhash)
            if (r.address and r.pool_id and r.active_epoch_no)
        ]

    withdrawals = []
    if txdata.last_row.withdrawal_count:
        withdrawals = [
            clusterlib.TxOut(address=r.address, amount=int(r.amount))
            for r in dbsync_queries.query_tx_withdrawal(txhash=txhash)
        ]

    collaterals = []
    if txdata.last_row.collateral_count:
        collaterals = [
            clusterlib.UTXOData(
                utxo_hash=r.tx_hash.hex(),
                utxo_ix=int(r.utxo_ix),
                amount=int(r.value),
                address=str(r.address),
            )
            for r in dbsync_queries.query_collateral_tx_ins(txhash=txhash)
        ]

    collateral_outputs = []
    if txdata.last_row.collateral_out_count:
        collateral_outputs = [
            clusterlib.UTXOData(
                utxo_hash=r.tx_hash.hex(),
                utxo_ix=int(r.utxo_ix),
                amount=int(r.value),
                address=str(r.address),
            )
            for r in dbsync_queries.query_collateral_tx_outs(txhash=txhash)
        ]

    reference_inputs = []
    if txdata.last_row.reference_input_count:
        reference_inputs = [
            clusterlib.UTXOData(
                utxo_hash=r.tx_hash.hex(),
                utxo_ix=int(r.utxo_ix),
                amount=int(r.value),
                address=str(r.address),
            )
            for r in dbsync_queries.query_reference_tx_ins(txhash=txhash)
        ]

    scripts = []
    if txdata.last_row.script_count:
        scripts = [
            ScriptRecord(
                hash=r.hash.hex(),
                type=str(r.type),
                serialised_size=int(r.serialised_size) if r.serialised_size else 0,
            )
            for r in dbsync_queries.query_scripts(txhash=txhash)
        ]

    redeemers = []
    if txdata.last_row.redeemer_count:
        redeemers = [
            RedeemerRecord(
                unit_mem=int(r.unit_mem),
                unit_steps=int(r.unit_steps),
                fee=int(r.fee),
                purpose=str(r.purpose),
                script_hash=r.script_hash.hex(),
                value=r.value,
            )
            for r in dbsync_queries.query_redeemers(txhash=txhash)
        ]

    extra_key_witness = []
    if txdata.last_row.extra_key_witness_count:
        extra_key_witness = [
            ExtraKeyWitnessRecord(tx_hash=r.tx_hash.hex(), witness_hash=r.witness_hash.hex())
            for r in dbsync_queries.query_extra_key_witness(txhash=txhash)
        ]

    record = TxRecord(
        tx_id=int(txdata.last_row.tx_id),
        tx_hash=txdata.last_row.tx_hash.hex(),
        block_id=int(txdata.last_row.block_id),
        block_index=int(txdata.last_row.block_index),
        out_sum=int(txdata.last_row.out_sum),
        fee=int(txdata.last_row.fee),
        deposit=int(txdata.last_row.deposit),
        size=int(txdata.last_row.size),
        invalid_before=int(txdata.last_row.invalid_before)
        if txdata.last_row.invalid_before
        else None,
        invalid_hereafter=int(txdata.last_row.invalid_hereafter)
        if txdata.last_row.invalid_hereafter
        else None,
        txins=txins,
        txouts=[*txdata.utxo_out, *txdata.ma_utxo_out],
        mint=txdata.mint_utxo_out,
        collaterals=collaterals,
        collateral_outputs=collateral_outputs,
        reference_inputs=reference_inputs,
        scripts=scripts,
        redeemers=redeemers,
        metadata=metadata,
        reserve=reserve,
        treasury=treasury,
        pot_transfers=pot_transfers,
        stake_registration=stake_registration,
        stake_deregistration=stake_deregistration,
        stake_delegation=stake_delegation,
        withdrawals=withdrawals,
        extra_key_witness=extra_key_witness,
    )

    return record


def retry_query(query_func: Callable, timeout: int = 20) -> Any:
    """Wait a bit and retry a query until response is returned.

    A generic function that can be used by any query/check that raises `AssertionError` with
    `NO_REPONSE_STR` until the expected data is returned.
    """
    end_time = time.time() + timeout
    repeat = 0

    while True:
        if repeat:
            sleep_time = 2 + repeat * repeat
            LOGGER.warning(f"Sleeping {sleep_time}s before repeating query for the {repeat} time.")
            time.sleep(sleep_time)
        try:
            response = query_func()
            break
        except AssertionError as exc:
            if NO_REPONSE_STR in str(exc) and time.time() < end_time:
                repeat += 1
                continue
            raise

    return response


def get_tx_record_retry(txhash: str, retry_num: int = 3) -> TxRecord:
    """Retry `get_tx_record` when data is anticipated and are not available yet.

    Under load it might be necessary to wait a bit and retry the query.
    """
    retry_num = retry_num if retry_num >= 0 else 0
    response = None

    # first try + number of retries
    for r in range(1 + retry_num):
        if r > 0:
            sleep_time = 2 + r * r
            LOGGER.warning(
                f"Sleeping {sleep_time}s before repeating TX SQL query for '{txhash}' "
                f"for the {r} time."
            )
            time.sleep(sleep_time)
        try:
            response = get_tx_record(txhash=txhash)
            break
        except RuntimeError:
            if r == retry_num:
                raise

    assert response is not None
    return response


def _sum_mint_txouts(txouts: clusterlib.OptionalTxOuts) -> List[clusterlib.TxOut]:
    """Calculate minting amount sum for records with the same token.

    Remove address information - minting tokens doesn't include address, only amount and asset ID,
    i.e. address information is not available in `ma_tx_mint` table.
    Remove also datum hash, which is not available as well.
    MA output is handled in Tx output checks.
    """
    mint_txouts: Dict[str, clusterlib.TxOut] = {}

    for mt in txouts:
        if mt.coin in mint_txouts:
            mt_stored = mint_txouts[mt.coin]
            mint_txouts[mt.coin] = mt_stored._replace(
                address="", amount=mt_stored.amount + mt.amount, datum_hash=""
            )
        else:
            mint_txouts[mt.coin] = mt._replace(address="", datum_hash="")

    return list(mint_txouts.values())


def _get_scripts_hashes(
    cluster_obj: clusterlib.ClusterLib,
    records: Union[clusterlib.OptionalScriptTxIn, clusterlib.OptionalMint],
) -> Dict[str, Union[clusterlib.OptionalScriptTxIn, clusterlib.OptionalMint]]:
    """Create a hash table of Tx Plutus data indexed by script hash."""
    hashes_db: dict = {}

    for r in records:
        if not r.script_file:
            continue
        shash = cluster_obj.g_transaction.get_policyid(script_file=r.script_file)
        shash_rec = hashes_db.get(shash)
        if shash_rec is None:
            hashes_db[shash] = [r]
            continue
        shash_rec.append(r)

    return hashes_db


def _db_redeemer_hashes(
    records: List[RedeemerRecord],
) -> Dict[str, List[RedeemerRecord]]:
    """Create a hash table of redeemers indexed by script hash."""
    hashes_db: dict = {}

    for r in records:
        shash = r.script_hash
        shash_rec = hashes_db.get(shash)
        if shash_rec is None:
            hashes_db[shash] = [r]
            continue
        shash_rec.append(r)

    return hashes_db


def _compare_redeemer_value(
    tx_rec: Union[clusterlib.ScriptTxIn, clusterlib.Mint], db_redeemer: dict
) -> bool:
    """Compare the value of the tx redeemer with the value stored on dbsync."""
    if not (tx_rec.redeemer_file or tx_rec.redeemer_value):
        return True

    redeemer_value = None

    if tx_rec.redeemer_file:
        with open(tx_rec.redeemer_file, encoding="utf-8") as r:
            redeemer_value = json.loads(r.read())
    elif tx_rec.redeemer_value and db_redeemer.get("int"):
        redeemer_value = {"int": int(tx_rec.redeemer_value)}
    elif tx_rec.redeemer_value and db_redeemer.get("bytes"):
        # we should ignore the first and last 2 chars because they represent
        # the double quotes
        tx_redeemer_bytes = tx_rec.redeemer_value.encode("utf-8").hex()[2:-2]
        redeemer_value = {"bytes": tx_redeemer_bytes}

    return bool(db_redeemer == redeemer_value) if redeemer_value else True


def _compare_redeemers(
    tx_data: Dict[str, Union[clusterlib.OptionalScriptTxIn, clusterlib.OptionalMint]],
    db_data: Dict[str, List[RedeemerRecord]],
    purpose: str,
) -> None:
    """Compare redeemers data available in Tx data with data in db-sync."""
    # pylint: disable=too-many-branches
    for script_hash, tx_recs in tx_data.items():
        if not tx_recs:
            return

        # if redeemer is not present, it is not plutus script
        if not (
            tx_recs[0].redeemer_file or tx_recs[0].redeemer_value or tx_recs[0].redeemer_cbor_file
        ):
            return

        # when minting with one Plutus script and two (or more) redeemers, only the last redeemer
        # is used
        if hasattr(tx_recs[0], "txouts"):  # check it is minting record
            # we'll check only the last redeemer
            tx_recs = tx_recs[-1:]  # noqa: PLW2901

        db_redeemer_recs = db_data.get(script_hash)
        assert db_redeemer_recs, f"No redeemer info in db-sync for script hash `{script_hash}`"

        len_tx_recs, len_db_redeemer_recs = len(tx_recs), len(db_redeemer_recs)
        assert (
            len_tx_recs == len_db_redeemer_recs
        ), f"Number of TX redeemers doesn't match ({len_tx_recs} != {db_redeemer_recs})"

        for tx_rec in tx_recs:
            tx_unit_steps = tx_rec.execution_units[0] if tx_rec.execution_units else None
            tx_unit_mem = tx_rec.execution_units[1] if tx_rec.execution_units else None

            missing_tx_unit_steps = not (tx_unit_steps and tx_unit_mem)

            for db_redeemer in db_redeemer_recs:
                if db_redeemer.purpose != purpose:
                    continue
                if not _compare_redeemer_value(tx_rec=tx_rec, db_redeemer=db_redeemer.value):
                    continue
                if missing_tx_unit_steps or (
                    tx_unit_steps == db_redeemer.unit_steps and tx_unit_mem == db_redeemer.unit_mem
                ):
                    break
            else:
                raise AssertionError(
                    f"Couldn't find matching redeemer info in db-sync for\n{tx_rec}"
                )


def _sanitize_txout(
    cluster_obj: clusterlib.ClusterLib, txout: clusterlib.TxOut
) -> clusterlib.TxOut:
    """Transform txout so it can be compared to data from db-sync."""
    datum_hash = clusterlib_utils.datum_hash_from_txout(cluster_obj=cluster_obj, txout=txout)

    new_txout = txout._replace(
        datum_hash=datum_hash,
        datum_hash_file="",
        datum_hash_cbor_file="",
        datum_hash_value="",
        datum_embed_file="",
        datum_embed_cbor_file="",
        datum_embed_value="",
        inline_datum_file="",
        inline_datum_cbor_file="",
        inline_datum_value="",
        reference_script_file="",
    )
    return new_txout


def _txout_has_inline_datum(txout: clusterlib.TxOut) -> bool:
    if txout.inline_datum_cbor_file or txout.inline_datum_file or txout.inline_datum_value:
        return True
    return False


def check_tx(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput, retry_num: int = 3
) -> Optional[TxRecord]:
    """Check a transaction in db-sync."""
    # pylint: disable=too-many-statements,too-many-locals
    if not configuration.HAS_DBSYNC:
        return None

    txhash = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
    response = get_tx_record_retry(txhash=txhash, retry_num=retry_num)

    tx_txouts = {_sanitize_txout(cluster_obj=cluster_obj, txout=r) for r in tx_raw_output.txouts}
    db_txouts = {utxodata2txout(r) for r in response.txouts}

    len_db_txouts, len_out_txouts = len(response.txouts), len(tx_raw_output.txouts)

    # we don't have complete info about the transaction when `build` command
    # was used (change txout, fee in older node versions), so we'll skip some of the checks
    if tx_raw_output.change_address:
        assert tx_txouts.issubset(db_txouts), f"TX outputs not subset: ({tx_txouts} vs {db_txouts})"
        assert (
            len_db_txouts >= len_out_txouts
        ), f"Number of TX outputs doesn't match ({len_db_txouts} < {len_out_txouts})"
    else:
        txouts_amount = clusterlib.calculate_utxos_balance(utxos=tx_raw_output.txouts)
        assert (
            response.out_sum == txouts_amount
        ), f"Sum of TX amounts doesn't match ({response.out_sum} != {txouts_amount})"

        assert (
            len_db_txouts == len_out_txouts
        ), f"Number of TX outputs doesn't match ({len_db_txouts} != {len_out_txouts})"

        assert tx_txouts == db_txouts, f"TX outputs don't match ({tx_txouts} != {db_txouts})"

    assert response.fee in (
        tx_raw_output.fee,
        -1,  # unknown fee is set to -1
    ), f"TX fee doesn't match ({response.fee} != {tx_raw_output.fee})"

    assert response.invalid_before == tx_raw_output.invalid_before, (
        "TX invalid_before doesn't match "
        f"({response.invalid_before} != {tx_raw_output.invalid_before})"
    )
    assert response.invalid_hereafter == tx_raw_output.invalid_hereafter, (
        "TX invalid_hereafter doesn't match "
        f"({response.invalid_hereafter} != {tx_raw_output.invalid_hereafter})"
    )

    combined_txins: List[clusterlib.UTXOData] = [
        *tx_raw_output.txins,
        *[p.txins[0] for p in tx_raw_output.script_txins if p.txins],
    ]
    txin_utxos = {f"{r.utxo_hash}#{r.utxo_ix}" for r in combined_txins}
    db_utxos = {f"{r.utxo_hash}#{r.utxo_ix}" for r in response.txins}
    assert (
        txin_utxos == db_utxos
    ), f"Not all TX inputs are present in the db ({txin_utxos} != {db_utxos})"

    tx_mint_txouts = list(itertools.chain.from_iterable(m.txouts for m in tx_raw_output.mint))
    tx_mint_by_token = sorted(_sum_mint_txouts(tx_mint_txouts))
    len_db_mint, len_out_mint = len(response.mint), len(tx_mint_by_token)
    assert (
        len_db_mint == len_out_mint
    ), f"Number of MA minting doesn't match ({len_db_mint} != {len_out_mint})"

    db_mint_txouts = sorted(utxodata2txout(r) for r in response.mint)
    assert (
        tx_mint_by_token == db_mint_txouts
    ), f"MA minting outputs don't match ({tx_mint_by_token} != {db_mint_txouts})"

    tx_withdrawals = sorted(
        [*tx_raw_output.withdrawals, *[s.txout for s in tx_raw_output.script_withdrawals]]
    )
    db_withdrawals = sorted(response.withdrawals)
    len_tx_withdrawals = len(tx_withdrawals)
    len_db_withdrawals = len(db_withdrawals)

    assert (
        len_db_withdrawals == len_tx_withdrawals
    ), f"Number of TX withdrawals doesn't match ({len_db_withdrawals} != {len_tx_withdrawals})"

    assert (
        tx_withdrawals == db_withdrawals
    ), f"TX withdrawals don't match ({tx_withdrawals} != {db_withdrawals})"

    tx_collaterals_nested = [
        r.collaterals
        for r in (
            *tx_raw_output.script_txins,
            *tx_raw_output.mint,
            *tx_raw_output.complex_certs,
            *tx_raw_output.script_withdrawals,
        )
    ]
    tx_collaterals = set(itertools.chain.from_iterable(tx_collaterals_nested))
    db_collaterals = set(response.collaterals)
    assert (
        tx_collaterals == db_collaterals
    ), f"TX collaterals don't match ({tx_collaterals} != {db_collaterals})"

    # test automatic return collateral only with `transaction build` command on node/dbsync versions
    # that support it
    if (
        tx_collaterals
        and tx_raw_output.change_address
        and response.collateral_outputs
        and not (tx_raw_output.total_collateral_amount or tx_raw_output.return_collateral_txouts)
    ):
        protocol_params = cluster_obj.g_query.get_protocol_params()
        tx_collaterals_amount = clusterlib.calculate_utxos_balance(utxos=list(tx_collaterals))
        tx_collateral_output_amount = int(
            tx_collaterals_amount
            - tx_raw_output.fee * protocol_params["collateralPercentage"] / 100
        )
        db_collateral_output_amount = clusterlib.calculate_utxos_balance(
            utxos=list(response.collateral_outputs)
        )

        assert db_collateral_output_amount == tx_collateral_output_amount, (
            "TX collateral output amount doesn't match "
            f"({db_collateral_output_amount} != {tx_collateral_output_amount})"
        )

    tx_in_script_hashes = _get_scripts_hashes(
        cluster_obj=cluster_obj, records=tx_raw_output.script_txins
    )
    tx_mint_script_hashes = _get_scripts_hashes(cluster_obj=cluster_obj, records=tx_raw_output.mint)

    # a script is added to `script` table only the first time it is seen, so the record
    # can be empty for the current transaction
    tx_script_hashes = {*tx_in_script_hashes, *tx_mint_script_hashes}
    if response.scripts and tx_script_hashes:
        db_script_hashes = {s.hash for s in response.scripts}

        assert db_script_hashes.issubset(
            tx_script_hashes
        ), f"Scripts hashes don't match: {db_script_hashes} is not subset of {tx_script_hashes}"

        # on plutus scripts we should also check the serialised_size
        db_plutus_scripts = {r for r in response.scripts if r.type.startswith("plutus")}

        if db_plutus_scripts:
            assert all(
                r.serialised_size > 0 for r in db_plutus_scripts
            ), f"The `serialised_size` <= 0 for some of the Plutus scripts:\n{db_plutus_scripts}"

    # compare redeemers data
    db_redeemer_hashes = _db_redeemer_hashes(records=response.redeemers)
    _compare_redeemers(tx_data=tx_in_script_hashes, db_data=db_redeemer_hashes, purpose="spend")
    _compare_redeemers(tx_data=tx_mint_script_hashes, db_data=db_redeemer_hashes, purpose="mint")

    redeemer_fees = functools.reduce(lambda x, y: x + y.fee, response.redeemers, 0)
    assert tx_raw_output.fee > redeemer_fees, "Combined redeemer fees are >= than total TX fee"

    # compare datum hash and inline datum hash in db-sync
    wrong_db_datum_hashes = [
        tx_out
        for tx_out in response.txouts
        if tx_out.inline_datum_hash and tx_out.inline_datum_hash != tx_out.datum_hash
    ]

    assert not wrong_db_datum_hashes, (
        "Datum hash and inline datum hash returned by dbsync don't match for following records:\n"
        f"{wrong_db_datum_hashes}"
    )

    # compare inline datums
    tx_txouts_inline_datums = {
        _sanitize_txout(cluster_obj=cluster_obj, txout=r)
        for r in tx_raw_output.txouts
        if _txout_has_inline_datum(r)
    }
    db_txouts_inline_datums = {utxodata2txout(r) for r in response.txouts if r.inline_datum_hash}
    assert (
        tx_txouts_inline_datums == db_txouts_inline_datums
    ), f"Inline datums don't match ({tx_txouts_inline_datums} != {db_txouts_inline_datums})"

    # compare readonly reference inputs
    txins_utxos_reference_inputs = {
        *[f"{r.utxo_hash}#{r.utxo_ix}" for r in tx_raw_output.readonly_reference_txins if r],
        *[
            f"{r.reference_txin.utxo_hash}#{r.reference_txin.utxo_ix}"
            for r in tx_raw_output.script_txins
            if r.reference_txin
        ],
        *[
            f"{r.reference_txin.utxo_hash}#{r.reference_txin.utxo_ix}"
            for r in tx_raw_output.complex_certs
            if r.reference_txin
        ],
    }
    db_utxos_reference_inputs = {
        f"{r.utxo_hash}#{r.utxo_ix}" for r in response.reference_inputs if r
    }
    assert txins_utxos_reference_inputs == db_utxos_reference_inputs, (
        "Reference inputs don't match "
        f"({txins_utxos_reference_inputs} != {db_utxos_reference_inputs})"
    )

    # check reference scripts
    tx_reference_script_hashes = {
        cluster_obj.g_transaction.get_policyid(script_file=r.reference_script_file)
        for r in tx_raw_output.txouts
        if r.reference_script_file
    }

    db_reference_script_hashes = {
        r.reference_script_hash for r in response.txouts if r.reference_script_hash
    }

    assert tx_reference_script_hashes == db_reference_script_hashes, (
        "Reference scripts don't match "
        f"({tx_reference_script_hashes} != {db_reference_script_hashes})"
    )

    # check required signers
    if tx_raw_output.required_signers:
        assert len(tx_raw_output.required_signers) == len(response.extra_key_witness), (
            "Number of required signers doesn't match "
            f"({len(tx_raw_output.required_signers)} != {len(response.extra_key_witness)})"
        )

    if tx_raw_output.required_signer_hashes:
        db_required_signer_hashes = [r.witness_hash for r in response.extra_key_witness]

        assert tx_raw_output.required_signer_hashes == db_required_signer_hashes, (
            "Required signer hashes don't match "
            f"({tx_raw_output.required_signer_hashes} != {db_required_signer_hashes})"
        )

    return response


def check_tx_phase_2_failure(
    cluster_obj: clusterlib.ClusterLib,
    tx_raw_output: clusterlib.TxRawOutput,
    collateral_charged: int,
    retry_num: int = 3,
) -> Optional[TxRecord]:
    """Check a transaction in db-sync when a phase 2 failure happens."""
    if not configuration.HAS_DBSYNC:
        return None

    txhash = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
    response = get_tx_record_retry(txhash=txhash, retry_num=retry_num)

    # In case of a phase 2 failure, the collateral output becomes the output of the tx.

    assert (
        not response.collateral_outputs
    ), "Collateral outputs are present in dbsync when the tx have a phase 2 failure"

    db_txouts = {utxodata2txout(r) for r in response.txouts}
    tx_out = {utxodata2txout(r) for r in cluster_obj.g_query.get_utxo(tx_raw_output=tx_raw_output)}

    assert db_txouts == tx_out, f"The TX outputs don't match ({db_txouts} != {tx_out})"

    # In case of a phase 2 failure, the fee charged is the collateral amount.

    if tx_raw_output.total_collateral_amount:
        expected_fee = round(tx_raw_output.total_collateral_amount)
    elif tx_raw_output.return_collateral_txouts and not tx_raw_output.total_collateral_amount:
        expected_fee = collateral_charged
    else:
        protocol_params = cluster_obj.g_query.get_protocol_params()
        expected_fee = round(tx_raw_output.fee * protocol_params["collateralPercentage"] / 100)

    assert response.fee == expected_fee, f"TX fee doesn't match ({response.fee} != {expected_fee})"

    return response


def check_pool_deregistration(pool_id: str, retiring_epoch: int) -> Optional[PoolDataRecord]:
    """Check pool retirement in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    db_pool_data = get_pool_data(pool_id)
    assert db_pool_data, f"No data returned from db-sync for pool {pool_id}"

    assert (
        db_pool_data.retire_announced_tx_id and db_pool_data.retiring_epoch
    ), f"Stake pool `{pool_id}` not retired"

    assert (
        retiring_epoch == db_pool_data.retiring_epoch
    ), f"Mismatch in epoch values: {retiring_epoch} vs {db_pool_data.retiring_epoch}"

    return db_pool_data


def check_pool_data(ledger_pool_data: dict, pool_id: str) -> Optional[PoolDataRecord]:  # noqa: C901
    """Check comparison for pool data between ledger and db-sync."""
    # pylint: disable=too-many-branches
    if not configuration.HAS_DBSYNC:
        return None

    db_pool_data = get_pool_data(pool_id)
    assert db_pool_data, f"No data returned from db-sync for pool {pool_id}"

    errors_list = []

    if ledger_pool_data["publicKey"] != db_pool_data.hash:
        errors_list.append(
            "'publicKey' value is different than expected; "
            f"Expected: {ledger_pool_data['publicKey']} vs Returned: {db_pool_data.hash}"
        )

    if ledger_pool_data["cost"] != db_pool_data.fixed_cost:
        errors_list.append(
            "'cost' value is different than expected; "
            f"Expected: {ledger_pool_data['cost']} vs Returned: {db_pool_data.fixed_cost}"
        )

    metadata = ledger_pool_data.get("metadata") or {}

    metadata_hash = metadata.get("hash") or ""
    if metadata_hash != db_pool_data.metadata_hash:
        errors_list.append(
            "'metadata hash' value is different than expected; "
            f"Expected: {metadata_hash} vs "
            f"Returned: {db_pool_data.metadata_hash}"
        )

    metadata_url = metadata.get("url") or ""
    if metadata_url != db_pool_data.metadata_url:
        errors_list.append(
            "'metadata url' value is different than expected; "
            f"Expected: {metadata_url} vs "
            f"Returned: {db_pool_data.metadata_url}"
        )

    if sorted(ledger_pool_data["owners"]) != sorted(db_pool_data.owners):
        errors_list.append(
            "'owners' value is different than expected; "
            f"Expected: {ledger_pool_data['owners']} vs Returned: {db_pool_data.owners}"
        )

    if ledger_pool_data["vrf"] != db_pool_data.vrf_key_hash:
        errors_list.append(
            "'vrf' value is different than expected; "
            f"Expected: {ledger_pool_data['vrf']} vs Returned: {db_pool_data.vrf_key_hash}"
        )

    if ledger_pool_data["pledge"] != db_pool_data.pledge:
        errors_list.append(
            "'pledge' value is different than expected; "
            f"Expected: {ledger_pool_data['pledge']} vs Returned: {db_pool_data.pledge}"
        )

    if ledger_pool_data["margin"] != db_pool_data.margin:
        errors_list.append(
            "'margin' value is different than expected; "
            f"Expected: {ledger_pool_data['margin']} vs Returned: {db_pool_data.margin}"
        )

    ledger_reward_address = ledger_pool_data["rewardAccount"]["credential"]["key hash"]
    if ledger_reward_address != db_pool_data.reward_addr:
        errors_list.append(
            "'reward address' value is different than expected; "
            f"Expected: {ledger_reward_address} vs Returned: {db_pool_data.reward_addr}"
        )

    if ledger_pool_data["relays"] and ledger_pool_data["relays"] != db_pool_data.relays:
        errors_list.append(
            "'relays' value is different than expected; "
            f"Expected: {ledger_pool_data['relays']} vs Returned: {db_pool_data.relays}"
        )

    if errors_list:
        errors_str = "\n\n".join(errors_list)
        raise AssertionError(f"{errors_str}\n\nStake Pool Details: \n{ledger_pool_data}")

    return db_pool_data


def check_pool_offline_data(
    ledger_pool_data: dict, pool_id: str
) -> dbsync_queries.PoolOfflineDataDBRow:
    """Check comparison for pool offline data between ledger and db-sync."""
    db_pool_offline_data = list(dbsync_queries.query_pool_offline_data(pool_id))
    assert db_pool_offline_data, f"{NO_REPONSE_STR} no offline data for pool {pool_id}"

    metadata_hash = (ledger_pool_data.get("metadata") or {}).get("hash") or ""
    db_metadata_hash = db_pool_offline_data[0].hash.hex()

    assert metadata_hash == db_metadata_hash, (
        "'metadata hash' value is different than expected; "
        f"Expected: {metadata_hash} vs Returned: {db_metadata_hash}"
    )

    return db_pool_offline_data[0]


def check_pool_offline_fetch_error(
    ledger_pool_data: dict, pool_id: str
) -> dbsync_queries.PoolOfflineFetchErrorDBRow:
    """Check expected error on `PoolOfflineFetchError`."""
    db_pool_offline_fetch_error = list(dbsync_queries.query_pool_offline_fetch_error(pool_id))
    assert (
        db_pool_offline_fetch_error
    ), f"{NO_REPONSE_STR} no offline fetch error for pool {pool_id}"

    fetch_error_str = db_pool_offline_fetch_error[0].fetch_error or ""
    metadata_url = (ledger_pool_data.get("metadata") or {}).get("url") or ""

    assert (
        f"Connection failure when fetching metadata from {metadata_url}" in fetch_error_str
    ), f"The error is not the expected one: {fetch_error_str}"

    return db_pool_offline_fetch_error[0]


def check_plutus_cost(redeemer_record: RedeemerRecord, cost_record: Dict[str, Any]) -> None:
    """Compare cost of Plutus script with data from db-sync."""
    errors = []
    if redeemer_record.unit_steps != cost_record["executionUnits"]["steps"]:
        errors.append(
            f'time: {redeemer_record.unit_steps} vs {cost_record["executionUnits"]["steps"]}'
        )
    if redeemer_record.unit_mem != cost_record["executionUnits"]["memory"]:
        errors.append(
            f'space: {redeemer_record.unit_mem} vs {cost_record["executionUnits"]["memory"]}'
        )
    if redeemer_record.fee != cost_record["lovelaceCost"]:
        errors.append(f'fixed cost: {redeemer_record.fee} vs {cost_record["lovelaceCost"]}')
    if redeemer_record.script_hash != cost_record["scriptHash"]:
        errors.append(f'script hash: {redeemer_record.script_hash} vs {cost_record["scriptHash"]}')

    if errors:
        raise AssertionError("\n".join(errors))


def check_plutus_costs(
    redeemer_records: List[RedeemerRecord], cost_records: List[Dict[str, Any]]
) -> None:
    """Compare cost of multiple Plutus scripts with data from db-sync."""
    # sort records first by total cost, second by hash
    sorted_costs = sorted(
        cost_records,
        key=lambda x: (
            x["executionUnits"]["memory"]  # type: ignore
            + x["executionUnits"]["steps"]
            + x["lovelaceCost"],
            x["scriptHash"],
        ),
    )
    sorted_db = sorted(
        redeemer_records, key=lambda x: (x.unit_mem + x.unit_steps + x.fee, x.script_hash)
    )

    if len(sorted_costs) != len(sorted_db):
        raise AssertionError(
            f"Number of cost records is different:\n{sorted_costs}\nvs\n{sorted_db}"
        )

    errors = []
    for db_record, cost_record in zip(sorted_db, sorted_costs):
        try:
            check_plutus_cost(redeemer_record=db_record, cost_record=cost_record)
        except AssertionError as err:
            errors.append(f"{db_record.script_hash}:\n{err}")

    if errors:
        raise AssertionError("\n".join(errors))


def check_param_proposal(protocol_params: dict) -> Optional[dbsync_queries.ParamProposalDBRow]:
    """Check expected values in the `param_proposal` table in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    param_proposal_db = dbsync_queries.query_param_proposal()

    params_mapping = {
        "coins_per_utxo_word": protocol_params["utxoCostPerByte"],
        "collateral_percent": protocol_params["collateralPercentage"],
        "influence": protocol_params["poolPledgeInfluence"],
        "key_deposit": protocol_params["stakeAddressDeposit"],
        "max_bh_size": protocol_params["maxBlockHeaderSize"],
        "max_block_ex_mem": protocol_params["maxBlockExecutionUnits"]["memory"],
        "max_block_ex_steps": protocol_params["maxBlockExecutionUnits"]["steps"],
        "max_block_size": protocol_params["maxBlockBodySize"],
        "max_collateral_inputs": protocol_params["maxCollateralInputs"],
        "max_epoch": protocol_params["poolRetireMaxEpoch"],
        "max_tx_ex_mem": protocol_params["maxTxExecutionUnits"]["memory"],
        "max_tx_ex_steps": protocol_params["maxTxExecutionUnits"]["steps"],
        "max_tx_size": protocol_params["maxTxSize"],
        "max_val_size": protocol_params["maxValueSize"],
        "min_fee_a": protocol_params["txFeePerByte"],
        "min_fee_b": protocol_params["txFeeFixed"],
        "min_pool_cost": protocol_params["minPoolCost"],
        "min_utxo_value": protocol_params["minUTxOValue"],
        "optimal_pool_count": protocol_params["stakePoolTargetNum"],
        "pool_deposit": protocol_params["stakePoolDeposit"],
    }

    failures = []

    for param_db, protocol_value in params_mapping.items():
        db_value = getattr(param_proposal_db, param_db)
        if db_value and (db_value != protocol_value):
            failures.append(f"Param value for {param_db}: {db_value}. Expected: {protocol_value}")

    if failures:
        failures_str = "\n".join(failures)
        raise AssertionError(f"Unexpected parameter proposal values in db-sync:\n{failures_str}")

    return param_proposal_db
