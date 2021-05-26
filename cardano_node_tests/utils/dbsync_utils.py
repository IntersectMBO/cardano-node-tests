"""Functionality for interacting with db-sync."""
import decimal
import logging
import time
from typing import Any
from typing import Dict
from typing import Generator
from typing import List
from typing import NamedTuple
from typing import Optional

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_conn

LOGGER = logging.getLogger(__name__)


DBSYNC_DB = "dbsync"


class MetadataRecord(NamedTuple):
    key: int
    json: Any
    bytes: memoryview


class ADAStashRecord(NamedTuple):
    addr_id: int
    cert_index: int
    amount: int


class DelegationRecord(NamedTuple):
    address: str
    pool_id: str
    active_epoch_no: int


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
    metadata: List[MetadataRecord]
    reserve: List[ADAStashRecord]
    treasury: List[ADAStashRecord]
    stake_registration: List[str]
    stake_deregistration: List[str]
    stake_delegation: List[DelegationRecord]
    withdrawals: List[clusterlib.TxOut]

    def _convert_metadata(self) -> dict:
        """Convert list of `MetadataRecord`s to metadata dictionary."""
        metadata = {int(r.key): r.json for r in self.metadata}
        return metadata


class TxDBRow(NamedTuple):
    tx_id: int
    tx_hash: memoryview
    block_id: int
    block_index: int
    out_sum: decimal.Decimal
    fee: decimal.Decimal
    deposit: int
    size: int
    invalid_before: Optional[decimal.Decimal]
    invalid_hereafter: Optional[decimal.Decimal]
    tx_out_id: int
    tx_out_tx_id: int
    utxo_ix: int
    tx_out_addr: str
    tx_out_value: decimal.Decimal
    metadata_count: int
    reserve_count: int
    treasury_count: int
    stake_reg_count: int
    stake_dereg_count: int
    stake_deleg_count: int
    withdrawal_count: int
    ma_tx_out_id: Optional[int]
    ma_tx_out_policy: Optional[memoryview]
    ma_tx_out_name: Optional[memoryview]
    ma_tx_out_quantity: Optional[decimal.Decimal]
    ma_tx_mint_id: Optional[int]
    ma_tx_mint_policy: Optional[memoryview]
    ma_tx_mint_name: Optional[memoryview]
    ma_tx_mint_quantity: Optional[decimal.Decimal]


class TxPrelimRecord(NamedTuple):
    utxo_out: List[clusterlib.UTXOData]
    ma_utxo_out: List[clusterlib.UTXOData]
    mint_utxo_out: List[clusterlib.UTXOData]
    last_row: TxDBRow


class MetadataDBRow(NamedTuple):
    id: int
    key: decimal.Decimal
    json: Any
    bytes: memoryview
    tx_id: int


class ADAStashDBRow(NamedTuple):
    id: int
    addr_id: int
    cert_index: int
    amount: decimal.Decimal
    tx_id: int


class StakeAddrDBRow(NamedTuple):
    id: int
    view: str
    tx_id: int


class StakeDelegDBRow(NamedTuple):
    tx_id: int
    active_epoch_no: Optional[int]
    pool_id: Optional[str]
    address: Optional[str]


class WithdrawalDBRow(NamedTuple):
    tx_id: int
    address: str
    amount: int


class TxInDBRow(NamedTuple):
    tx_out_id: int
    utxo_ix: int
    address: str
    value: decimal.Decimal
    tx_hash: memoryview
    ma_tx_out_id: Optional[int]
    ma_tx_out_policy: Optional[memoryview]
    ma_tx_out_name: Optional[memoryview]
    ma_tx_out_quantity: Optional[decimal.Decimal]


class ADAPotsDBRow(NamedTuple):
    id: int
    slot_no: int
    epoch_no: int
    treasury: decimal.Decimal
    reserves: decimal.Decimal
    rewards: decimal.Decimal
    utxo: decimal.Decimal
    deposits: decimal.Decimal
    fees: decimal.Decimal
    block_id: int


def query_tx(txhash: str) -> Generator[TxDBRow, None, None]:
    """Query a transaction in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx.id, tx.hash, tx.block_id, tx.block_index, tx.out_sum, tx.fee, tx.deposit, tx.size,"
            " tx.invalid_before, tx.invalid_hereafter,"
            " tx_out.id, tx_out.tx_id, tx_out.index, tx_out.address, tx_out.value,"
            " (SELECT COUNT(id) FROM tx_metadata WHERE tx_id=tx.id) AS metadata_count,"
            " (SELECT COUNT(id) FROM reserve WHERE tx_id=tx.id) AS reserve_count,"
            " (SELECT COUNT(id) FROM treasury WHERE tx_id=tx.id) AS treasury_count,"
            " (SELECT COUNT(id) FROM stake_registration WHERE tx_id=tx.id) AS reg_count,"
            " (SELECT COUNT(id) FROM stake_deregistration WHERE tx_id=tx.id) AS dereg_count,"
            " (SELECT COUNT(id) FROM delegation WHERE tx_id=tx.id) AS deleg_count,"
            " (SELECT COUNT(id) FROM withdrawal WHERE tx_id=tx.id) AS withdrawal_count,"
            " ma_tx_out.id, ma_tx_out.policy, ma_tx_out.name, ma_tx_out.quantity,"
            " ma_tx_mint.id, ma_tx_mint.policy, ma_tx_mint.name, ma_tx_mint.quantity "
            "FROM tx "
            "LEFT JOIN tx_out ON tx.id = tx_out.tx_id "
            "LEFT JOIN ma_tx_out ON tx_out.id = ma_tx_out.tx_out_id "
            "LEFT JOIN ma_tx_mint ON tx.id = ma_tx_mint.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield TxDBRow(*result)


def query_tx_ins(txhash: str) -> Generator[TxInDBRow, None, None]:
    """Query transaction txins in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx_out.id, tx_out.index, tx_out.address, tx_out.value, "
            " (SELECT hash FROM tx WHERE id = tx_out.tx_id) AS tx_hash, "
            " ma_tx_out.id, ma_tx_out.policy, ma_tx_out.name, ma_tx_out.quantity "
            "FROM tx_in "
            "LEFT JOIN tx_out "
            "ON (tx_out.tx_id = tx_in.tx_out_id AND tx_out.index = tx_in.tx_out_index) "
            "LEFT JOIN tx ON tx.id = tx_in.tx_in_id "
            "LEFT JOIN ma_tx_out ON tx_out.id = ma_tx_out.tx_out_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield TxInDBRow(*result)


def query_tx_metadata(txhash: str) -> Generator[MetadataDBRow, None, None]:
    """Query transaction metadata in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx_metadata.id, tx_metadata.key, tx_metadata.json, tx_metadata.bytes,"
            " tx_metadata.tx_id "
            "FROM tx_metadata "
            "INNER JOIN tx ON tx.id = tx_metadata.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield MetadataDBRow(*result)


def query_tx_reserve(txhash: str) -> Generator[ADAStashDBRow, None, None]:
    """Query transaction reserve record in db-sync."""
    # TODO: return address instead of addr_id
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " reserve.id, reserve.addr_id, reserve.cert_index, reserve.amount, reserve.tx_id "
            "FROM reserve "
            "INNER JOIN tx ON tx.id = reserve.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield ADAStashDBRow(*result)


def query_tx_treasury(txhash: str) -> Generator[ADAStashDBRow, None, None]:
    """Query transaction treasury record in db-sync."""
    # TODO: return address instead of addr_id
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " treasury.id, treasury.addr_id, treasury.cert_index, treasury.amount, treasury.tx_id "
            "FROM treasury "
            "INNER JOIN tx ON tx.id = treasury.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield ADAStashDBRow(*result)


def query_tx_stake_reg(txhash: str) -> Generator[StakeAddrDBRow, None, None]:
    """Query stake registration record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " stake_registration.addr_id, stake_address.view, stake_registration.tx_id "
            "FROM stake_registration "
            "INNER JOIN stake_address ON stake_registration.addr_id = stake_address.id "
            "INNER JOIN tx ON tx.id = stake_registration.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield StakeAddrDBRow(*result)


def query_tx_stake_dereg(txhash: str) -> Generator[StakeAddrDBRow, None, None]:
    """Query stake deregistration record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " stake_deregistration.addr_id, stake_address.view, stake_deregistration.tx_id "
            "FROM stake_deregistration "
            "INNER JOIN stake_address ON stake_deregistration.addr_id = stake_address.id "
            "INNER JOIN tx ON tx.id = stake_deregistration.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield StakeAddrDBRow(*result)


def query_tx_stake_deleg(txhash: str) -> Generator[StakeDelegDBRow, None, None]:
    """Query stake registration record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx.id, delegation.active_epoch_no, pool_hash.view AS pool_view,"
            " stake_address.view AS address_view "
            "FROM delegation "
            "INNER JOIN stake_address ON delegation.addr_id = stake_address.id "
            "INNER JOIN tx ON tx.id = delegation.tx_id "
            "INNER JOIN pool_hash ON pool_hash.id = delegation.pool_hash_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield StakeDelegDBRow(*result)


def query_tx_withdrawal(txhash: str) -> Generator[WithdrawalDBRow, None, None]:
    """Query reward withdrawal record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx.id, stake_address.view, amount "
            "FROM withdrawal "
            "INNER JOIN stake_address ON withdrawal.addr_id = stake_address.id "
            "INNER JOIN tx ON tx.id = withdrawal.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield WithdrawalDBRow(*result)


def query_ada_pots(
    epoch_from: int = 0, epoch_to: int = 99999999
) -> Generator[ADAPotsDBRow, None, None]:
    """Query ADA pots record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " id, slot_no, epoch_no, treasury, reserves, rewards, utxo, deposits, fees, block_id "
            "FROM ada_pots "
            "WHERE epoch_no BETWEEN %s AND %s;",
            (epoch_from, epoch_to),
        )

        while (result := cur.fetchone()) is not None:
            yield ADAPotsDBRow(*result)


def get_prelim_tx_record(txhash: str) -> TxPrelimRecord:
    """Get first batch of transaction data from db-sync."""
    utxo_out: List[clusterlib.UTXOData] = []
    seen_tx_out_ids = set()
    ma_utxo_out: List[clusterlib.UTXOData] = []
    seen_ma_tx_out_ids = set()
    mint_utxo_out: List[clusterlib.UTXOData] = []
    seen_ma_tx_mint_ids = set()
    tx_id = -1

    for query_row in query_tx(txhash=txhash):
        if tx_id == -1:
            tx_id = query_row.tx_id
        if tx_id != query_row.tx_id:
            raise AssertionError("Transaction ID differs from the expected ID.")

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
                address=str(query_row.tx_out_addr),
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

    for txins_row in query_tx_ins(txhash=txhash):
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


def get_tx_record(txhash: str) -> TxRecord:
    """Get transaction data from db-sync.

    Compile data from multiple SQL queries to get as much information about the TX as possible.
    """
    txdata = get_prelim_tx_record(txhash)
    txins = get_txins(txhash)

    metadata = []
    if txdata.last_row.metadata_count:
        metadata = [
            MetadataRecord(key=int(r.key), json=r.json, bytes=r.bytes)
            for r in query_tx_metadata(txhash=txhash)
        ]

    reserve = []
    if txdata.last_row.reserve_count:
        reserve = [
            ADAStashRecord(
                addr_id=int(r.addr_id), cert_index=int(r.cert_index), amount=int(r.amount)
            )
            for r in query_tx_reserve(txhash=txhash)
        ]

    treasury = []
    if txdata.last_row.treasury_count:
        treasury = [
            ADAStashRecord(
                addr_id=int(r.addr_id), cert_index=int(r.cert_index), amount=int(r.amount)
            )
            for r in query_tx_treasury(txhash=txhash)
        ]

    stake_registration = []
    if txdata.last_row.stake_reg_count:
        stake_registration = [r.view for r in query_tx_stake_reg(txhash=txhash)]

    stake_deregistration = []
    if txdata.last_row.stake_dereg_count:
        stake_deregistration = [r.view for r in query_tx_stake_dereg(txhash=txhash)]

    stake_delegation = []
    if txdata.last_row.stake_deleg_count:
        stake_delegation = [
            DelegationRecord(
                address=r.address, pool_id=r.pool_id, active_epoch_no=r.active_epoch_no
            )
            for r in query_tx_stake_deleg(txhash=txhash)
            if (r.address and r.pool_id and r.active_epoch_no)
        ]

    withdrawals = []
    if txdata.last_row.withdrawal_count:
        withdrawals = [
            clusterlib.TxOut(address=r.address, amount=int(r.amount))
            for r in query_tx_withdrawal(txhash=txhash)
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
        metadata=metadata,
        reserve=reserve,
        treasury=treasury,
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
    retry_num = retry_num if retry_num > 1 else 1
    last_retry_idx = retry_num - 1

    for r in range(retry_num):
        if r > 0:
            LOGGER.warning(f"Repeating TX SQL query for '{txhash}' for the {r} time.")
            time.sleep(2)
        try:
            response = get_tx_record(txhash=txhash)
            break
        except RuntimeError:
            if r == last_retry_idx:
                raise

    return response


def _sum_mint_txouts(txouts: clusterlib.OptionalTxOuts) -> List[clusterlib.TxOut]:
    """Calculate minting amount sum for records with same address and token."""
    mint_txouts: Dict[str, clusterlib.TxOut] = {}

    for mt in txouts:
        mt_id = f"{mt.address}_{mt.coin}"
        if mt_id in mint_txouts:
            mt_stored = mint_txouts[mt_id]
            mint_txouts[mt_id] = mt_stored._replace(amount=mt_stored.amount + mt.amount)
        else:
            mint_txouts[mt_id] = mt

    return list(mint_txouts.values())


def check_tx(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput, retry_num: int = 3
) -> Optional[TxRecord]:
    """Check a transaction in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    txhash = cluster_obj.get_txid(tx_body_file=tx_raw_output.out_file)
    response = get_tx_record_retry(txhash=txhash, retry_num=retry_num)

    txouts_amount = clusterlib_utils.get_amount(tx_raw_output.txouts)
    assert (
        response.out_sum == txouts_amount
    ), f"Sum of TX amounts doesn't match ({response.out_sum} != {txouts_amount})"

    assert (
        response.fee == tx_raw_output.fee
    ), f"TX fee doesn't match ({response.fee} != {tx_raw_output.fee})"

    assert response.invalid_before == tx_raw_output.invalid_before, (
        "TX invalid_before doesn't match "
        f"({response.invalid_before} != {tx_raw_output.invalid_before})"
    )
    assert response.invalid_hereafter == tx_raw_output.invalid_hereafter, (
        "TX invalid_hereafter doesn't match "
        f"({response.invalid_hereafter} != {tx_raw_output.invalid_hereafter})"
    )

    len_db_txins, len_out_txins = len(response.txins), len(tx_raw_output.txins)
    assert (
        len_db_txins == len_out_txins
    ), f"Number of TX inputs doesn't match ({len_db_txins} != {len_out_txins})"

    tx_txins = sorted(tx_raw_output.txins)
    db_txins = sorted(response.txins)
    assert tx_txins == db_txins, f"TX inputs don't match ({tx_txins} != {db_txins})"

    len_db_txouts, len_out_txouts = len(response.txouts), len(tx_raw_output.txouts)
    assert (
        len_db_txouts == len_out_txouts
    ), f"Number of TX outputs doesn't match ({len_db_txouts} != {len_out_txouts})"

    tx_txouts = sorted(tx_raw_output.txouts)
    db_txouts = sorted(clusterlib_utils.utxodata2txout(r) for r in response.txouts)
    assert tx_txouts == db_txouts, f"TX outputs don't match ({tx_txouts} != {db_txouts})"

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

    return response
