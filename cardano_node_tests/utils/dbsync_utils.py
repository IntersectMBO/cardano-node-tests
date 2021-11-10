"""Functionality for interacting with db-sync."""
import functools
import itertools
import logging
import time
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Set
from typing import Union

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_queries

LOGGER = logging.getLogger(__name__)


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


class RewardRecord(NamedTuple):
    address: str
    pool_id: str
    rewards: List[RewardEpochRecord]
    reward_sum: int


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
    txouts: List[clusterlib.UTXOData]
    mint: List[clusterlib.UTXOData]
    collaterals: List[clusterlib.UTXOData]
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

    def _convert_metadata(self) -> dict:
        """Convert list of `MetadataRecord`s to metadata dictionary."""
        metadata = {int(r.key): r.json for r in self.metadata}
        return metadata


class TxPrelimRecord(NamedTuple):
    utxo_out: List[clusterlib.UTXOData]
    ma_utxo_out: List[clusterlib.UTXOData]
    mint_utxo_out: List[clusterlib.UTXOData]
    last_row: dbsync_queries.TxDBRow


def get_address_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> Optional[RewardRecord]:
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
            )
        )
    if not rewards:
        return None

    reward_sum = functools.reduce(lambda x, y: x + y.amount, rewards, 0)
    # pylint: disable=undefined-loop-variable
    return RewardRecord(
        address=db_row.address, pool_id=db_row.pool_id, rewards=rewards, reward_sum=reward_sum
    )


def check_address_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> Optional[RewardRecord]:
    """Check reward data for stake address in db-sync.

    The `epoch_from` and `epoch_to` are epochs where the reward can be spent.
    """
    reward = get_address_reward(address=address, epoch_from=epoch_from, epoch_to=epoch_to)
    if reward is None:
        return None

    errors = []
    for r in reward.rewards:
        if r.spendable_epoch != r.earned_epoch + 2:
            errors.append(f"{r.earned_epoch} != {r.spendable_epoch} + 2")

    if errors:
        err_str = ", ".join(errors)
        raise AssertionError(f"The 'earned epoch' and 'spendable epoch' don't match: {err_str}")

    return reward


def get_address_orphaned_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> Optional[RewardRecord]:
    """Get data about orphaned rewards for stake address from db-sync.

    The `epoch_from` and `epoch_to` are epochs where the reward was earned, not epochs
    where the reward can be spent.
    """
    rewards = []
    for db_row in dbsync_queries.query_address_orphaned_reward(
        address=address, epoch_from=epoch_from, epoch_to=epoch_to
    ):
        rewards.append(
            RewardEpochRecord(
                amount=int(db_row.amount),
                earned_epoch=db_row.epoch_no,
                spendable_epoch=-1,
            )
        )
    if not rewards:
        return None

    reward_sum = functools.reduce(lambda x, y: x + y.amount, rewards, 0)
    # pylint: disable=undefined-loop-variable
    return RewardRecord(
        address=db_row.address, pool_id=db_row.pool_id, rewards=rewards, reward_sum=reward_sum
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
    utxo_out: List[clusterlib.UTXOData] = []
    seen_tx_out_ids = set()
    ma_utxo_out: List[clusterlib.UTXOData] = []
    seen_ma_tx_out_ids = set()
    mint_utxo_out: List[clusterlib.UTXOData] = []
    seen_ma_tx_mint_ids = set()
    tx_id = -1

    for query_row in dbsync_queries.query_tx(txhash=txhash):
        if tx_id == -1:
            tx_id = query_row.tx_id
        if tx_id != query_row.tx_id:
            raise AssertionError("Transaction ID differs from the expected ID")

        # Lovelace outputs
        if query_row.tx_out_id and query_row.tx_out_id not in seen_tx_out_ids:
            seen_tx_out_ids.add(query_row.tx_out_id)
            out_rec = clusterlib.UTXOData(
                utxo_hash=str(txhash),
                utxo_ix=int(query_row.utxo_ix),
                amount=int(query_row.tx_out_value),
                address=str(query_row.tx_out_addr),
            )
            utxo_out.append(out_rec)

        # MA outputs
        if query_row.ma_tx_out_id and query_row.ma_tx_out_id not in seen_ma_tx_out_ids:
            seen_ma_tx_out_ids.add(query_row.ma_tx_out_id)
            asset_name = (
                bytearray.fromhex(query_row.ma_tx_out_name.hex()).decode()
                if query_row.ma_tx_out_name
                else None
            )
            policyid = query_row.ma_tx_out_policy.hex() if query_row.ma_tx_out_policy else ""
            coin = f"{policyid}.{asset_name}" if asset_name else policyid
            ma_rec = clusterlib.UTXOData(
                utxo_hash=str(txhash),
                utxo_ix=int(query_row.utxo_ix),
                amount=int(query_row.ma_tx_out_quantity or 0),
                address=str(query_row.tx_out_addr),
                coin=coin,
            )
            ma_utxo_out.append(ma_rec)

        # MA minting
        if query_row.ma_tx_mint_id and query_row.ma_tx_mint_id not in seen_ma_tx_mint_ids:
            seen_ma_tx_mint_ids.add(query_row.ma_tx_mint_id)
            asset_name = (
                bytearray.fromhex(query_row.ma_tx_mint_name.hex()).decode()
                if query_row.ma_tx_mint_name
                else None
            )
            policyid = query_row.ma_tx_mint_policy.hex() if query_row.ma_tx_mint_policy else ""
            coin = f"{policyid}.{asset_name}" if asset_name else policyid
            mint_rec = clusterlib.UTXOData(
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
            asset_name = (
                bytearray.fromhex(txins_row.ma_tx_out_name.hex()).decode()
                if txins_row.ma_tx_out_name
                else None
            )
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

    scripts = []
    if txdata.last_row.script_count:
        scripts = [
            ScriptRecord(
                hash=r.hash.hex(),
                type=str(r.type),
                serialised_size=int(r.serialised_size) if r.serialised_size else 0,
            )
            for r in dbsync_queries.query_plutus_scripts(txhash=txhash)
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
            )
            for r in dbsync_queries.query_redeemers(txhash=txhash)
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
    )

    return record


def get_tx_record_retry(txhash: str, retry_num: int = 3) -> TxRecord:
    """Retry `get_tx_record` when data is anticipated and are not available yet.

    Under load it might be necessary to wait a bit and retry the query.
    """
    retry_num = retry_num if retry_num >= 0 else 0

    # first try + number of retries
    for r in range(1 + retry_num):
        if r > 0:
            LOGGER.warning(f"Repeating TX SQL query for '{txhash}' for the {r} time.")
            time.sleep(2 + r * r)
        try:
            response = get_tx_record(txhash=txhash)
            break
        except RuntimeError:
            if r == retry_num:
                raise

    return response


def _sum_mint_txouts(txouts: clusterlib.OptionalTxOuts) -> List[clusterlib.TxOut]:
    """Calculate minting amount sum for records with the same token.

    Remove address information - minting tokens doesn't include address, only amount and asset ID,
    i.e. address information is not available in `ma_tx_mint` table.
    MA output is handled in Tx output checks.
    """
    mint_txouts: Dict[str, clusterlib.TxOut] = {}

    for mt in txouts:
        if mt.coin in mint_txouts:
            mt_stored = mint_txouts[mt.coin]
            mint_txouts[mt.coin] = mt_stored._replace(
                address="", amount=mt_stored.amount + mt.amount
            )
        else:
            mint_txouts[mt.coin] = mt._replace(address="")

    return list(mint_txouts.values())


def _tx_scripts_hashes(
    cluster_obj: clusterlib.ClusterLib,
    records: Union[clusterlib.OptionalPlutusTxIns, clusterlib.OptionalPlutusMintData],
) -> Dict[str, Union[clusterlib.OptionalPlutusTxIns, clusterlib.OptionalPlutusMintData]]:
    """Create a hash table of Tx Plutus data indexed by script hash."""
    hashes_db: dict = {}

    for r in records:
        shash = cluster_obj.get_policyid(script_file=r.script_file)
        if shash not in hashes_db:
            hashes_db[shash] = []
        hashes_db[shash].append(r)

    return hashes_db


def _db_redeemer_hashes(
    records: List[RedeemerRecord],
) -> Dict[str, List[RedeemerRecord]]:
    """Create a hash table of redeemers indexed by script hash."""
    hashes_db: dict = {}

    for r in records:
        shash = r.script_hash
        if shash not in hashes_db:
            hashes_db[shash] = []
        hashes_db[shash].append(r)

    return hashes_db


def _compare_redeemers(
    tx_data: Dict[str, Union[clusterlib.OptionalPlutusTxIns, clusterlib.OptionalPlutusMintData]],
    db_data: Dict[str, List[RedeemerRecord]],
    purpose: str,
) -> None:
    """Compare redeemers data available in Tx data with data in db-sync."""
    for script_hash, tx_recs in tx_data.items():
        db_redeemer_recs = db_data.get(script_hash)
        assert db_redeemer_recs, f"No redeemer info in db-sync for script hash `{script_hash}`"

        len_tx_recs, len_db_redeemer_recs = len(tx_recs), len(db_redeemer_recs)
        assert (
            len_tx_recs == len_db_redeemer_recs
        ), f"Number of TX redeemers doesn't match ({len_tx_recs} != {db_redeemer_recs})"

        for tx_rec in tx_recs:
            if not tx_rec.execution_units:
                continue

            tx_unit_steps = tx_rec.execution_units[0]
            tx_unit_mem = tx_rec.execution_units[1]
            for db_redeemer in db_redeemer_recs:
                if db_redeemer.purpose != purpose:
                    continue
                if tx_unit_steps == db_redeemer.unit_steps and tx_unit_mem == db_redeemer.unit_mem:
                    break
            else:
                raise AssertionError(
                    f"Couldn't find matching redeemer info in db-sync for\n{tx_rec}"
                )


def check_tx(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput, retry_num: int = 3
) -> Optional[TxRecord]:
    """Check a transaction in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    txhash = cluster_obj.get_txid(tx_body_file=tx_raw_output.out_file)
    response = get_tx_record_retry(txhash=txhash, retry_num=retry_num)

    # we don't have complete info about the transaction when `build` command
    # was used, so we'll skip some of the checks
    if not tx_raw_output.change_address:
        txouts_amount = clusterlib_utils.get_amount(tx_raw_output.txouts)
        assert (
            response.out_sum == txouts_amount
        ), f"Sum of TX amounts doesn't match ({response.out_sum} != {txouts_amount})"

        assert (
            response.fee == tx_raw_output.fee
        ), f"TX fee doesn't match ({response.fee} != {tx_raw_output.fee})"

        len_db_txouts, len_out_txouts = len(response.txouts), len(tx_raw_output.txouts)
        assert (
            len_db_txouts == len_out_txouts
        ), f"Number of TX outputs doesn't match ({len_db_txouts} != {len_out_txouts})"

        # cannot get datum hash from db-sync, we need to strip it from tx data as well
        tx_txouts = sorted(r._replace(datum_hash="") for r in tx_raw_output.txouts)
        db_txouts = sorted(clusterlib_utils.utxodata2txout(r) for r in response.txouts)
        assert tx_txouts == db_txouts, f"TX outputs don't match ({tx_txouts} != {db_txouts})"

    assert response.invalid_before == tx_raw_output.invalid_before, (
        "TX invalid_before doesn't match "
        f"({response.invalid_before} != {tx_raw_output.invalid_before})"
    )
    assert response.invalid_hereafter == tx_raw_output.invalid_hereafter, (
        "TX invalid_hereafter doesn't match "
        f"({response.invalid_hereafter} != {tx_raw_output.invalid_hereafter})"
    )

    combined_txins: Set[clusterlib.UTXOData] = {
        *tx_raw_output.txins,
        *[p.txins[0] for p in tx_raw_output.plutus_txins],
        *[p.txins[0] for p in tx_raw_output.plutus_mint],
    }
    txin_utxos = {f"{r.utxo_hash}#{r.utxo_ix}" for r in combined_txins}
    db_utxos = {f"{r.utxo_hash}#{r.utxo_ix}" for r in response.txins}
    assert (
        txin_utxos == db_utxos
    ), f"Not all TX inputs are present in the db ({txin_utxos} != {db_utxos})"

    tx_mint_txouts = sorted(_sum_mint_txouts(tx_raw_output.mint))
    len_db_mint, len_out_mint = len(response.mint), len(tx_mint_txouts)
    assert (
        len_db_mint == len_out_mint
    ), f"Number of MA minting doesn't match ({len_db_mint} != {len_out_mint})"

    db_mint_txouts = sorted(clusterlib_utils.utxodata2txout(r) for r in response.mint)
    assert (
        tx_mint_txouts == db_mint_txouts
    ), f"MA minting outputs don't match ({tx_mint_txouts} != {db_mint_txouts})"

    len_db_withdrawals = len(response.withdrawals)
    len_out_withdrawals = len(tx_raw_output.withdrawals)
    assert (
        len_db_withdrawals == len_out_withdrawals
    ), f"Number of TX withdrawals doesn't match ({len_db_withdrawals} != {len_out_withdrawals})"

    tx_withdrawals = sorted(tx_raw_output.withdrawals)
    db_withdrawals = sorted(response.withdrawals)
    assert (
        tx_withdrawals == db_withdrawals
    ), f"TX withdrawals don't match ({tx_withdrawals} != {db_withdrawals})"

    tx_collaterals_nested = [
        r.collaterals for r in (*tx_raw_output.plutus_txins, *tx_raw_output.plutus_mint)
    ]
    tx_collaterals = set(itertools.chain.from_iterable(tx_collaterals_nested))
    db_collaterals = set(response.collaterals)
    assert (
        tx_collaterals == db_collaterals
    ), f"TX collaterals don't match ({tx_collaterals} != {db_collaterals})"

    db_plutus_scripts = {r for r in response.scripts if r.type == "plutus"}
    # a script is added to `script` table only the first time it is seen, so the record
    # can be empty for the current transaction
    if db_plutus_scripts:
        assert all(
            r.serialised_size > 0 for r in db_plutus_scripts
        ), f"The `serialised_size` <= 0 for some of the Plutus scripts:\n{db_plutus_scripts}"

    # compare redeemers data
    tx_plutus_in_hashes = _tx_scripts_hashes(
        cluster_obj=cluster_obj, records=tx_raw_output.plutus_txins
    )
    tx_plutus_mint_hashes = _tx_scripts_hashes(
        cluster_obj=cluster_obj, records=tx_raw_output.plutus_mint
    )
    db_redeemer_hashes = _db_redeemer_hashes(records=response.redeemers)
    _compare_redeemers(tx_data=tx_plutus_in_hashes, db_data=db_redeemer_hashes, purpose="spend")
    _compare_redeemers(tx_data=tx_plutus_mint_hashes, db_data=db_redeemer_hashes, purpose="mint")

    redeemer_fees = functools.reduce(lambda x, y: x + y.fee, response.redeemers, 0)
    assert tx_raw_output.fee > redeemer_fees, "Combined redeemer fees are >= than total TX fee"

    return response


def check_pool_deregistration(pool_id: str, retiring_epoch: int) -> Optional[PoolDataRecord]:
    """Check pool retirement in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    db_pool_data = get_pool_data(pool_id)
    assert db_pool_data, f"No data returned from db-sync for pool {pool_id}"

    if db_pool_data.retire_announced_tx_id and db_pool_data.retiring_epoch:
        assert (
            retiring_epoch == db_pool_data.retiring_epoch
        ), f"Mismatch in epoch values: {retiring_epoch} VS {db_pool_data.retiring_epoch}"
    else:
        raise AssertionError(f"Stake pool `{pool_id}` not retired")

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

    if ledger_pool_data["relays"]:
        if ledger_pool_data["relays"] != db_pool_data.relays:
            errors_list.append(
                "'relays' value is different than expected; "
                f"Expected: {ledger_pool_data['relays']} vs Returned: {db_pool_data.relays}"
            )

    if errors_list:
        errors_str = "\n\n".join(errors_list)
        raise AssertionError(f"{errors_str}\n\nStake Pool Details: \n{ledger_pool_data}")

    return db_pool_data
