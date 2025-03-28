"""Tests for basic DB-Sync configuration options."""

import logging
import typing as tp

import pytest

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils

LOGGER = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.skipif(
        configuration.CLUSTERS_COUNT != 1 and not configuration.HAS_DBSYNC,
        reason="db-sync config tests can run on a single cluster only",
    ),
    pytest.mark.dbsync_config,
]


def check_dbsync_state(expected_state: dict) -> None:
    """Check the state of db-sync tables and columns against expected conditions.

    Args:
        expected_state: Dictionary specifying conditions to verify where:
            - Key format:
                * "table" for table-level checks
                * "table.column" for column-level checks
            - Value format:
                * "empty" - verify table is empty
                * "not_empty" - verify table has rows
                * "exists" - verify table/column exists
                * "not_exists" - verify table/column doesn't exist
                * "column_condition:=0" - custom SQL condition
                * "column_condition:IS NULL" - NULL check condition

    Returns:
        bool: True if all conditions match, False otherwise
    """
    for key, condition in expected_state.items():
        if "." in key:  # Column-level check
            table, column = key.split(".", 1)
            assert condition.startswith("column_condition:"), (
                f"Invalid column condition format: {condition}"
            )
            column_condition = condition.split(":", 1)[1]
            dbsync_utils.check_column_condition(table, column, column_condition)
        else:  # Table-level check
            match condition:
                case "empty":
                    assert dbsync_utils.table_empty(key), (
                        f"Expected {key} to be empty, but it is not."
                    )
                case "not_empty":
                    assert not dbsync_utils.table_empty(key), (
                        f"Expected {key} to have data, but it is empty."
                    )
                case "exists":
                    assert dbsync_utils.table_exists(key), (
                        f"Expected {key} to exist, but it does not."
                    )
                case "not_exists":
                    assert not dbsync_utils.table_exists(key), (
                        f"Expected {key} to NOT exist, but it does."
                    )
                case _:
                    error_msg = f"Unknown table condition '{condition}' for table '{key}'"
                    raise ValueError(error_msg)


@pytest.fixture
def db_sync_manager(
    request: pytest.FixtureRequest, cluster_manager: cluster_management.ClusterManager
) -> dbsync_utils.DBSyncManager:
    """Provide db-sync manager on a singleton cluster.

    Creates and returns a DBSyncManager instance with locked cluster resources
    to ensure exclusive access during testing.
    """
    cluster_manager.get(lock_resources=[cluster_management.Resources.CLUSTER])
    return dbsync_utils.DBSyncManager(request)


@pytest.mark.order(-1)
class TestDBSyncConfig:
    """Basic tests for DB-Sync Config."""

    def test_basic_tx_out(
        self,
        db_sync_manager: dbsync_utils.DBSyncManager,
    ):
        """Test tx_out option."""
        db_config = db_sync_manager.get_config_builder()

        # Test tx_out : enable
        db_sync_manager.restart_with_config(
            db_config.with_tx_out(value="enable", force_tx_in=False, use_address_table=False)
        )
        check_dbsync_state(
            {
                "address": "not_exists",
                "tx_in": "not_empty",
                "tx_out": "not_empty",
                "ma_tx_out": "not_empty",
            }
        )

        # Test tx_out : disable
        db_sync_manager.restart_with_config(
            db_config.with_tx_out(value="disable", force_tx_in=True, use_address_table=True)
        )
        check_dbsync_state(
            {
                "address": "not_exists",
                "tx_in": "empty",
                "tx_out": "empty",
                "ma_tx_out": "empty",
                "tx.fee": "column_condition:=0",
                "redeemer.script_hash": "column_condition:IS NULL",
            }
        )

    @pytest.mark.parametrize(
        ("tx_cbor_value", "expected_state"), [("enable", "not_empty"), ("disable", "empty")]
    )
    def test_cbor(
        self,
        db_sync_manager: dbsync_utils.DBSyncManager,
        tx_cbor_value: tp.Literal["enable", "disable"],
        expected_state: str,
    ):
        """Test tx_cbor option with parametrization."""
        db_config = db_sync_manager.get_config_builder()

        db_sync_manager.restart_with_config(db_config.with_tx_cbor(tx_cbor_value))
        check_dbsync_state({"tx_cbor": expected_state})

    @pytest.mark.parametrize(
        ("multi_asset_enable", "expected_state"), [(True, "not_empty"), (False, "empty")]
    )
    def test_multi_asset(
        self,
        db_sync_manager: dbsync_utils.DBSyncManager,
        multi_asset_enable: bool,
        expected_state: str,
    ):
        """Test multi_asset option with parametrization."""
        db_config = db_sync_manager.get_config_builder()

        db_sync_manager.restart_with_config(db_config.with_multi_asset(enable=multi_asset_enable))
        check_dbsync_state({"multi_asset": expected_state})
