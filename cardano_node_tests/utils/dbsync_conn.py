"""Functionality for interacting with db-sync database in postgres."""
import logging
from typing import Dict
from typing import Optional

import psycopg2

from cardano_node_tests.utils import cluster_nodes

LOGGER = logging.getLogger(__name__)


DBSYNC_DB = "dbsync"


class DBSync:
    """Manage connection to db-sync database for each cluster instance."""

    conn_cache: Dict[int, Optional[psycopg2.extensions.connection]] = {0: None}

    @classmethod
    def _conn(cls, instance_num: int) -> psycopg2.extensions.connection:
        conn = psycopg2.connect(f"dbname={DBSYNC_DB}{instance_num}")
        cls.conn_cache[instance_num] = conn
        return conn

    @classmethod
    def _close(cls, instance_num: int, conn: Optional[psycopg2.extensions.connection]) -> None:
        if conn is None or conn.closed == 1:
            return

        LOGGER.info(f"Closing connection to db-sync database {DBSYNC_DB}{instance_num}.")
        try:
            conn.close()
        except psycopg2.Error as err:
            LOGGER.warning(
                f"Unable to close connection to db-sync database {DBSYNC_DB}{instance_num}: {err}"
            )

    @classmethod
    def conn(cls) -> psycopg2.extensions.connection:
        instance_num = cluster_nodes.get_instance_num()
        conn = cls.conn_cache.get(instance_num)

        if conn is None or conn.closed == 1:
            return cls._conn(instance_num=instance_num)

        return conn

    @classmethod
    def reconn(cls) -> psycopg2.extensions.connection:
        instance_num = cluster_nodes.get_instance_num()
        conn = cls.conn_cache.get(instance_num)
        cls._close(instance_num=instance_num, conn=conn)
        conn = cls._conn(instance_num=instance_num)
        return conn

    @classmethod
    def close_all(cls) -> None:
        for instance_num, conn in cls.conn_cache.items():
            cls._close(instance_num=instance_num, conn=conn)
