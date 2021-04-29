"""Functionality for interacting with db-sync."""
import logging
from typing import List
from typing import NamedTuple
from typing import Optional

import psycopg2
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration

LOGGER = logging.getLogger(__name__)


DBSYNC_DB = "dbsync"


class TxDBRecord(NamedTuple):
    tx_id: int
    out_sum: int
    fee: int
    deposit: int
    invalid_before: Optional[int]
    invalid_hereafter: Optional[int]
    tx_out_addresses: List[str]


class DBSync:
    conn_cache: Optional[psycopg2.extensions.connection] = None

    @classmethod
    def conn(cls) -> psycopg2.extensions.connection:
        if cls.conn_cache is None or cls.conn_cache.closed == 1:
            cls.conn_cache = psycopg2.connect(f"dbname={DBSYNC_DB}")
        return cls.conn_cache


def query_tx(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput
) -> TxDBRecord:
    """Query a transaction in db-sync."""
    txid = cluster_obj.get_txid_body(tx_raw_output.out_file)

    with DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx.id, tx.out_sum, tx.fee, tx.deposit, tx.invalid_before,"
            " tx.invalid_hereafter, tx_out.address "
            "FROM tx "
            "INNER JOIN tx_out "
            "ON tx.id = tx_out.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txid}",),
        )
        results = cur.fetchall()

    if not results:
        raise RuntimeError("No results were returned by the SQL query.")

    tx_id, out_sum, fee, deposit, invalid_before, invalid_hereafter, *__ = results[0]
    tx_out_addresses = [str(r[-1]) for r in results]

    record = TxDBRecord(
        tx_id=int(tx_id),
        out_sum=int(out_sum),
        fee=int(fee),
        deposit=int(deposit),
        invalid_before=int(invalid_before) if invalid_before else None,
        invalid_hereafter=int(invalid_hereafter) if invalid_hereafter else None,
        tx_out_addresses=tx_out_addresses,
    )

    return record


def check_tx(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput
) -> Optional[TxDBRecord]:
    """Check a transaction in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    response = query_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)
    assert response.out_sum == clusterlib_utils.get_amount(
        tx_raw_output.txouts
    ), "Sum of Tx amounts doesn't match"
    assert response.fee == tx_raw_output.fee, "TX fee doesn't match"
    assert (
        response.invalid_before == tx_raw_output.invalid_before
    ), "Tx `invalid_before` doesn't match"
    assert (
        response.invalid_hereafter == tx_raw_output.invalid_hereafter
    ), "Tx `invalid_hereafter` doesn't match"

    out_addrs_lovelace = [
        t.address for t in tx_raw_output.txouts if t.coin == clusterlib.DEFAULT_COIN
    ]
    assert len(response.tx_out_addresses) == len(
        out_addrs_lovelace
    ), "Number of Tx outputs don't match"

    return response
