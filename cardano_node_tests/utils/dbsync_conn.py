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
    def conn(cls) -> psycopg2.extensions.connection:
        instance_num = cluster_nodes.get_cluster_env().instance_num
        conn = cls.conn_cache.get(instance_num)
        if conn is None or conn.closed == 1:
            conn = psycopg2.connect(f"dbname={DBSYNC_DB}{instance_num}")
            cls.conn_cache[instance_num] = conn
        return conn

    @classmethod
    def close_all(cls) -> None:
        for idx, conn in cls.conn_cache.items():
            if conn is None or conn.closed == 1:
                return
            LOGGER.info(f"Closing connection to db-sync database {DBSYNC_DB}{idx}.")
            conn.close()
