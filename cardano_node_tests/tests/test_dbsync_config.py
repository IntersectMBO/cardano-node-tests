"""Tests for basic DB-Sync configuration options."""

import logging
import random
import re
import json
import logging
import os
import pathlib as pl
import shutil
import typing as tp
import yaml
import logging
import time

import pytest
import requests
from cardano_clusterlib import clusterlib



from typing import Optional, List, Literal, Dict, Any

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_types
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools
from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import smash_utils

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def db() -> dbsync_utils.DBSyncAssertions:
    return dbsync_utils.DBSyncAssertions()


@pytest.fixture
def db_sync_manager(
    request: pytest.FixtureRequest,
    cluster_singleton: cluster_management.ClusterManager
) -> dbsync_utils.DBSyncManager:
    """
    Provides db-sync manager on a singleton cluster with exclusive access.
    """
    return dbsync_utils.DBSyncManager(request)
    

class TestDBSyncConfig:
    """Basic tests for DB-Sync Config."""

    def test_basic_tx_out(
        self,
        db: dbsync_utils.DBSyncAssertions,
        db_sync_manager: dbsync_utils.DBSyncManager,
    ):
        """Test tx_out option."""
        db_config = db_sync_manager.get_config_builder()

        # Test tx_out : enable
        db_sync_manager.restart_with_config(
            db_config.with_tx_out(
                value="enable", force_tx_in=False, use_address_table=False
            )    
        )
        db.assert_table_not_exists(table="address")
        db.assert_table_not_empty("tx_in")
        db.assert_table_not_empty("tx_out")
        db.assert_table_not_empty("ma_tx_out")

        # Test tx_out : disable
        db_sync_manager.restart_with_config(
            db_config.with_tx_out(
                value="disable", force_tx_in=True, use_address_table=True
            )     
        )
        db.assert_table_not_exists(table="address")
        db.assert_table_empty("tx_in")
        db.assert_table_empty("tx_out")
        db.assert_table_empty("ma_tx_out")
        db.assert_column_condition(table="tx", column="fee", condition="=0")
        db.assert_column_condition(table="redeemer", column="script_hash", condition="IS NULL")

        # Test tx_out : update disable to bootstrap
        # Error SNErrDefault: "Shelley.validateGenesisDistribution: Expected initial block to have 5 but got 1"
        db_sync_manager.restart_with_config(
            db_config.with_tx_out(value="disable")     
        )
        db_sync_manager.stop_db_sync()
        db_sync_manager.update_config(
            db_config.with_tx_out(value="bootstrap")
        )
        db_sync_manager.start_db_sync()
        dbsync_utils.wait_for_db_sync_completion(expected_progress=10)
        db.assert_table_not_empty("tx_in")
        db.assert_table_not_empty("tx_out")
        db.assert_table_not_empty("ma_tx_out")


    def test_tx_out_consumed(
        self,
        db: dbsync_utils.DBSyncAssertions,
        db_sync_manager: dbsync_utils.DBSyncManager,
    ):
        """Test tx_out 'consumed' option."""
        db_config = db_sync_manager.get_config_builder()

        # Test tx_out: consumed
        db_sync_manager.restart_with_config(
            db_config.with_tx_out(value="consumed")
        )
        db.assert_table_empty("tx_in") # Should be empty when tx_out value is "consumed"
        db.assert_table_not_empty("tx_out")
        db.assert_table_not_empty("ma_tx_out")


        # Test tx_out: consumed with force_tx_in
        db_sync_manager.restart_with_config(
            db_config.with_tx_out(value="consumed", force_tx_in=True)
        )
        db.assert_table_not_empty("tx_in") # Should NOT be empty when force_tx_in is True
        db.assert_table_not_empty("tx_out")
        db.assert_table_not_empty("ma_tx_out")

        # Test tx_out: update from consumed to prune
        db_sync_manager.restart_with_config(
            db_config.with_tx_out(value="consumed", force_tx_in=True)
        )
        db_sync_manager.stop_db_sync()
        db_sync_manager.update_config(
            db_config.with_tx_out(value="prune")
        )
        db_sync_manager.start_db_sync()
        dbsync_utils.wait_for_db_sync_completion()


    def test_cbor(
        self,
        db: dbsync_utils.DBSyncAssertions,
        db_sync_manager: dbsync_utils.DBSyncManager,
    ):
        """Test tx_cbor option."""
        db_config = db_sync_manager.get_config_builder()

        # Test tx_cbor : enable
        db_sync_manager.restart_with_config(
            db_config.with_tx_cbor("enable")
        )

        db.assert_table_exists(table="tx_cbor")
        db.assert_table_not_empty(table="tx_cbor")


        # Test tx_cbor : disable
        db_sync_manager.restart_with_config(
            db_config.with_tx_cbor("disable")
        )
        db.assert_table_exists(table="tx_cbor")
        db.assert_table_empty(table="tx_cbor")


    def test_multi_asset(
        self,
        db: dbsync_utils.DBSyncAssertions,
        db_sync_manager: dbsync_utils.DBSyncManager,
    ):
        """Test multi_asset option."""
        db_config = db_sync_manager.get_config_builder()

        # Test multi_asset : enable
        db_sync_manager.restart_with_config(
             db_config.with_multi_asset(enable=True)
        )           
        db.assert_table_not_empty(table="multi_asset")
        
        # Test multi_asset : disable
        db_sync_manager.restart_with_config(
             db_config.with_multi_asset(enable=False)
        )  
        db.assert_table_empty(table="multi_asset")