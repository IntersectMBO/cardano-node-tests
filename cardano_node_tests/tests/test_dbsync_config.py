"""Tests for basic DB-Sync configuration options."""

import enum
import logging
import typing as tp

import allure
import pytest

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_service_manager as db_sync
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.skipif(
        configuration.CLUSTERS_COUNT != 1 or not configuration.HAS_DBSYNC,
        reason="db-sync config tests can run on a single cluster only",
    ),
    pytest.mark.dbsync_config,
    pytest.mark.needs_dbsync,
]


class TableCondition(str, enum.Enum):
    """Enum for table-level db-sync state conditions."""

    EMPTY = "empty"
    NOT_EMPTY = "not_empty"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


class ColumnCondition(str, enum.Enum):
    """Enum for column-level db-sync condition checks."""

    ZERO = "column_condition:=0"
    IS_NULL = "column_condition:IS NULL"


def check_dbsync_state(
    expected_state: dict[tp.Union[str, db_sync.Table], TableCondition | ColumnCondition],
) -> None:
    """Check the state of db-sync tables and columns against expected conditions.

    Args:
        expected_state: Dictionary specifying conditions to verify where:
            - Key format:
                * "table" for table-level checks
                * "table.column" for column-level checks
            - Value format:
                * TableCondition enum values for table-level checks
                * ColumnCondition enum values for column-level checks

    Returns:
        bool: True if all conditions match, False otherwise
    """
    for key, condition in expected_state.items():
        if "." in key:  # Column-level check
            table, column = key.split(".", 1)
            assert isinstance(condition, ColumnCondition), (
                f"Invalid column condition format: {condition}"
            )
            column_condition = condition.value.split(":", 1)[1]
            dbsync_utils.check_column_condition(table, column, column_condition)
        else:  # Table-level check
            assert isinstance(condition, TableCondition), (
                f"Invalid table condition format: {condition}"
            )
            match condition:
                case TableCondition.EMPTY:
                    assert dbsync_utils.table_empty(key), (
                        f"Expected {key} to be empty, but it is not."
                    )
                case TableCondition.NOT_EMPTY:
                    assert not dbsync_utils.table_empty(key), (
                        f"Expected {key} to have data, but it is empty."
                    )
                case TableCondition.EXISTS:
                    assert dbsync_utils.table_exists(key), (
                        f"Expected {key} to exist, but it does not."
                    )
                case TableCondition.NOT_EXISTS:
                    assert not dbsync_utils.table_exists(key), (
                        f"Expected {key} to NOT exist, but it does."
                    )
                case _:
                    error_msg = f"Unknown table condition '{condition}' for table '{key}'"
                    raise ValueError(error_msg)


@pytest.fixture
def db_sync_manager(
    request: pytest.FixtureRequest, cluster_manager: cluster_management.ClusterManager
) -> db_sync.DBSyncManager:
    """Provide db-sync manager on a singleton cluster.

    Creates and returns a DBSyncManager instance with locked cluster resources
    to ensure exclusive access during testing.
    """
    cluster_manager.get(lock_resources=[cluster_management.Resources.CLUSTER])
    return db_sync.DBSyncManager(request)


@pytest.mark.order(-1)
class TestDBSyncConfig:
    """Basic tests for DB-Sync Config."""

    @allure.link(helpers.get_vcs_link())
    def test_basic_tx_out(
        self,
        db_sync_manager: db_sync.DBSyncManager,
    ):
        """Test tx_out option."""
        db_config = db_sync_manager.get_config_builder()

        # Test tx_out : enable
        db_sync_manager.restart_with_config(
            db_config.with_tx_out(
                value=db_sync.TxOutMode.ENABLE, force_tx_in=False, use_address_table=False
            )
        )
        check_dbsync_state(
            {
                db_sync.Table.ADDRESS: TableCondition.NOT_EXISTS,
                db_sync.Table.TX_IN: TableCondition.NOT_EMPTY,
                db_sync.Table.TX_OUT: TableCondition.NOT_EMPTY,
                db_sync.Table.MA_TX_OUT: TableCondition.NOT_EMPTY,
            }
        )

        # Test tx_out : disable
        db_sync_manager.restart_with_config(
            db_config.with_tx_out(
                value=db_sync.TxOutMode.DISABLE, force_tx_in=True, use_address_table=True
            )
        )
        check_dbsync_state(
            {
                db_sync.Table.ADDRESS: TableCondition.NOT_EXISTS,
                db_sync.Table.TX_IN: TableCondition.EMPTY,
                db_sync.Table.TX_OUT: TableCondition.EMPTY,
                db_sync.Table.MA_TX_OUT: TableCondition.EMPTY,
                db_sync.Column.Tx.FEE: ColumnCondition.ZERO,
                db_sync.Column.Redeemer.SCRIPT_HASH: ColumnCondition.IS_NULL,
            }
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        ("tx_cbor_value", "expected_state"),
        [
            (db_sync.SettingState.ENABLE, TableCondition.NOT_EMPTY),
            (db_sync.SettingState.DISABLE, TableCondition.EMPTY),
        ],
    )
    def test_cbor(
        self,
        db_sync_manager: db_sync.DBSyncManager,
        tx_cbor_value: db_sync.SettingState,
        expected_state: TableCondition,
    ):
        """Test tx_cbor option with parametrization."""
        db_config = db_sync_manager.get_config_builder()

        db_sync_manager.restart_with_config(db_config.with_tx_cbor(tx_cbor_value))
        check_dbsync_state({db_sync.Table.TX_CBOR: expected_state})

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        ("multi_asset_enable", "expected_state"),
        [(True, TableCondition.NOT_EMPTY), (False, TableCondition.EMPTY)],
    )
    def test_multi_asset(
        self,
        db_sync_manager: db_sync.DBSyncManager,
        multi_asset_enable: bool,
        expected_state: TableCondition,
    ):
        """Test multi_asset option with parametrization."""
        db_config = db_sync_manager.get_config_builder()

        db_sync_manager.restart_with_config(db_config.with_multi_asset(enable=multi_asset_enable))
        check_dbsync_state({db_sync.Table.MULTI_ASSET: expected_state})
