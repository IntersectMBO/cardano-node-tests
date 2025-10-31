"""Tests for basic DB-Sync configuration options."""

import enum
import logging
import shutil
import typing as tp

import allure
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_service_manager as db_sync
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.skipif(
        configuration.CLUSTERS_COUNT != 1,
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


GOVERNANCE_TABLES = (
    db_sync.Table.COMMITTEE_DE_REGISTRATION,
    db_sync.Table.COMMITTEE_MEMBER,
    db_sync.Table.COMMITTEE_REGISTRATION,
    db_sync.Table.COMMITTEE,
    db_sync.Table.CONSTITUTION,
    db_sync.Table.DELEGATION_VOTE,
    db_sync.Table.DREP_DISTR,
    db_sync.Table.DREP_REGISTRATION,
    db_sync.Table.EPOCH_STATE,
    db_sync.Table.GOV_ACTION_PROPOSAL,
    db_sync.Table.OFF_CHAIN_VOTE_DATA,
    db_sync.Table.OFF_CHAIN_VOTE_DREP_DATA,
    db_sync.Table.OFF_CHAIN_VOTE_EXTERNAL_UPDATE,
    db_sync.Table.OFF_CHAIN_VOTE_FETCH_ERROR,
    db_sync.Table.OFF_CHAIN_VOTE_GOV_ACTION_DATA,
    db_sync.Table.OFF_CHAIN_VOTE_REFERENCE,
    db_sync.Table.VOTING_ANCHOR,
    db_sync.Table.VOTING_PROCEDURE,
    db_sync.Table.TREASURY_WITHDRAWAL,
)


def check_dbsync_state(
    expected_state: dict[str | db_sync.Table, TableCondition | ColumnCondition],
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
            dbsync_utils.check_column_condition(
                table=table, column=column, condition=column_condition
            )
        else:  # Table-level check
            assert isinstance(condition, TableCondition), (
                f"Invalid table condition format: {condition}"
            )
            match condition:
                case TableCondition.EMPTY:
                    assert dbsync_utils.table_empty(table=key), (
                        f"Expected {key} to be empty, but it is not."
                    )
                case TableCondition.NOT_EMPTY:
                    assert not dbsync_utils.table_empty(table=key), (
                        f"Expected {key} to have data, but it is empty."
                    )
                case TableCondition.EXISTS:
                    assert dbsync_utils.table_exists(table=key), (
                        f"Expected {key} to exist, but it does not."
                    )
                case TableCondition.NOT_EXISTS:
                    assert not dbsync_utils.table_exists(table=key), (
                        f"Expected {key} to NOT exist, but it does."
                    )
                case _:
                    error_msg = f"Unknown table condition '{condition}' for table '{key}'"
                    raise ValueError(error_msg)


@pytest.fixture
def db_sync_manager(
    cluster_singleton: clusterlib.ClusterLib,  # noqa: ARG001
) -> tp.Generator[db_sync.DBSyncManager, None, None]:
    """Provide db-sync manager on a singleton cluster.

    Creates and returns a DBSyncManager instance with locked cluster resources
    to ensure exclusive access during testing.

    Returns db-sync configuration to its original state after testing is finished.
    """
    cluster_dir = cluster_nodes.get_cluster_env().state_dir
    config_file = cluster_dir / "dbsync-config.yaml"

    # Backup the config file
    orig_config_file = cluster_dir / "dbsync-config.yaml.original"
    if not orig_config_file.exists():
        shutil.copy(config_file, orig_config_file)

    manager = db_sync.DBSyncManager()
    yield manager

    # Restore the original config file
    shutil.copy(orig_config_file, config_file)
    manager.restart_with_config()


@pytest.mark.order(-1)
class TestDBSyncConfig:
    """Tests for DB-Sync Config."""

    def get_subtests(self) -> tp.Generator[tp.Callable, None, None]:
        """Get the DB-Sync Config scenarios.

        The scenarios are executed as subtests in the `test_dbsync_config` test.
        """

        def basic_tx_out(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test `tx_out` option."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_tx_out(
                    value=db_sync.TxOutMode.ENABLE, force_tx_in=False, use_address_table=False
                )
            )
            check_dbsync_state(
                expected_state={
                    db_sync.Table.ADDRESS: TableCondition.NOT_EXISTS,
                    db_sync.Table.TX_IN: TableCondition.NOT_EMPTY,
                    db_sync.Table.TX_OUT: TableCondition.NOT_EMPTY,
                    db_sync.Table.MA_TX_OUT: TableCondition.NOT_EMPTY,
                }
            )

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_tx_out(
                    value=db_sync.TxOutMode.DISABLE, force_tx_in=True, use_address_table=True
                )
            )
            check_dbsync_state(
                expected_state={
                    db_sync.Table.ADDRESS: TableCondition.NOT_EXISTS,
                    db_sync.Table.TX_IN: TableCondition.EMPTY,
                    db_sync.Table.TX_OUT: TableCondition.EMPTY,
                    db_sync.Table.MA_TX_OUT: TableCondition.EMPTY,
                    db_sync.Column.Tx.FEE: ColumnCondition.ZERO,
                    db_sync.Column.Redeemer.SCRIPT_HASH: ColumnCondition.IS_NULL,
                }
            )

        yield basic_tx_out

        def governance(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test `governance` option."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_governance(value=db_sync.SettingState.ENABLE)
            )

            # Off-chain data is inserted into the DB a few minutes after the restart of db-sync
            def _query_func():
                empty_tables = [
                    table for table in GOVERNANCE_TABLES if dbsync_utils.table_empty(table=table)
                ]

                if empty_tables:
                    msg = f"Following tables are still empty: {empty_tables}"
                    raise dbsync_utils.DbSyncNoResponseError(msg)

                return True

            dbsync_utils.retry_query(query_func=_query_func, timeout=600)

            check_dbsync_state(
                expected_state={t: TableCondition.NOT_EMPTY for t in GOVERNANCE_TABLES}  # noqa: C420
            )

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_governance(value=db_sync.SettingState.DISABLE)
            )
            check_dbsync_state(
                expected_state={t: TableCondition.EMPTY for t in GOVERNANCE_TABLES}  # noqa: C420
            )

        yield governance

        def tx_cbor_value_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `tx_cbor` option."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_tx_cbor(value=db_sync.SettingState.ENABLE)
            )
            check_dbsync_state(expected_state={db_sync.Table.TX_CBOR: TableCondition.NOT_EMPTY})

        yield tx_cbor_value_enable

        def tx_cbor_value_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `tx_cbor` option."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_tx_cbor(value=db_sync.SettingState.DISABLE)
            )
            check_dbsync_state(expected_state={db_sync.Table.TX_CBOR: TableCondition.EMPTY})

        yield tx_cbor_value_disable

        def multi_asset_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test elabled `multi_asset` option."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_multi_asset(enable=True)
            )
            check_dbsync_state({db_sync.Table.MULTI_ASSET: TableCondition.NOT_EMPTY})

        yield multi_asset_enable

        def multi_asset_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `multi_asset` option."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_multi_asset(enable=False)
            )
            check_dbsync_state({db_sync.Table.MULTI_ASSET: TableCondition.EMPTY})

        yield multi_asset_disable

    @allure.link(helpers.get_vcs_link())
    def test_dbsync_config(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        db_sync_manager: db_sync.DBSyncManager,
        subtests: pytest_subtests.SubTests,
    ):
        """Run db-sync config subtests."""
        cluster = cluster_singleton
        common.get_test_id(cluster)

        for subt in self.get_subtests():
            with subtests.test(scenario=subt.__name__):
                subt(db_sync_manager)
