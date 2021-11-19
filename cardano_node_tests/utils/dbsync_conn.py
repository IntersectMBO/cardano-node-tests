"""Functionality for interacting with db-sync database in postgres."""
import logging
from typing import Dict
from typing import Optional

import psycopg2

from cardano_node_tests.utils import cluster_nodes

LOGGER = logging.getLogger(__name__)


DBSYNC_DB = "dbsync"


class DBSyncCache:
    """Cache connection to db-sync database for each cluster instance."""

    conns: Dict[int, Optional[psycopg2.extensions.connection]] = {0: None}


def _conn(instance_num: int) -> psycopg2.extensions.connection:
    conn = psycopg2.connect(f"dbname={DBSYNC_DB}{instance_num}")
    DBSyncCache.conns[instance_num] = conn
    return conn


def _close(instance_num: int, conn: Optional[psycopg2.extensions.connection]) -> None:
    if conn is None or conn.closed == 1:
        return

    LOGGER.info(f"Closing connection to db-sync database {DBSYNC_DB}{instance_num}.")
    try:
        conn.close()
    except psycopg2.Error as err:
        LOGGER.warning(
            f"Unable to close connection to db-sync database {DBSYNC_DB}{instance_num}: {err}"
        )


def conn() -> psycopg2.extensions.connection:
    instance_num = cluster_nodes.get_instance_num()
    conn = DBSyncCache.conns.get(instance_num)

    if conn is None or conn.closed == 1:
        return _conn(instance_num=instance_num)

    return conn


def reconn() -> psycopg2.extensions.connection:
    instance_num = cluster_nodes.get_instance_num()
    conn = DBSyncCache.conns.get(instance_num)
    _close(instance_num=instance_num, conn=conn)
    conn = _conn(instance_num=instance_num)
    return conn


def close_all() -> None:
    for instance_num, conn in DBSyncCache.conns.items():
        _close(instance_num=instance_num, conn=conn)
