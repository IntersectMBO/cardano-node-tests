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
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_queries
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


class TableCondition(enum.StrEnum):
    """Enum for table-level db-sync state conditions."""

    EMPTY = "empty"
    NOT_EMPTY = "not_empty"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


class ColumnCondition(enum.StrEnum):
    """Enum for column-level db-sync condition checks."""

    ZERO = "column_condition:=0"
    IS_NULL = "column_condition:IS NULL"
    IS_NOT_NULL = "column_condition:IS NOT NULL"


# On-chain governance tables. Off-chain vote metadata is controlled by `offchain_vote_data`
# (and needs --allow-private-offchain-urls), so it is covered separately by the
# offchain_vote_data subtests rather than bundled here with on-chain governance data.
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
    db_sync.Table.VOTING_ANCHOR,
    db_sync.Table.VOTING_PROCEDURE,
    db_sync.Table.TREASURY_WITHDRAWAL,
)

# Off-chain vote metadata tables, populated only when `offchain_vote_data` is enabled and
# db-sync is allowed to fetch the (private/localhost) anchor URLs.
OFFCHAIN_VOTE_TABLES = (
    db_sync.Table.OFF_CHAIN_VOTE_DATA,
    db_sync.Table.OFF_CHAIN_VOTE_DREP_DATA,
    db_sync.Table.OFF_CHAIN_VOTE_EXTERNAL_UPDATE,
    db_sync.Table.OFF_CHAIN_VOTE_FETCH_ERROR,
    db_sync.Table.OFF_CHAIN_VOTE_GOV_ACTION_DATA,
    db_sync.Table.OFF_CHAIN_VOTE_REFERENCE,
    db_sync.Table.OFF_CHAIN_VOTE_AUTHOR,
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


def wait_for_tables_not_empty(
    tables: tp.Iterable[str | db_sync.Table],
    *,
    timeout: int = 600,
) -> None:
    """Wait until all given db-sync tables have data.

    Off-chain data (pool/vote metadata) is fetched asynchronously and can appear up to
    several minutes after db-sync starts (the fetch loop sleeps ~300s between passes), so
    such tables must be polled rather than checked once. Raises ``TimeoutError`` (via
    ``retry_query``) if any table is still empty after ``timeout`` seconds.
    """
    # Materialize once so a generator argument is not exhausted by the first (failing)
    # poll, which would make every subsequent retry see an empty list and pass falsely.
    tables = list(tables)

    def _query_func() -> bool:
        empty_tables = [table for table in tables if dbsync_utils.table_empty(table=table)]
        if empty_tables:
            msg = f"Following tables are still empty: {empty_tables}"
            raise dbsync_utils.DbSyncNoResponseError(msg)
        return True

    dbsync_utils.retry_query(query_func=_query_func, timeout=timeout)


@pytest.fixture
def db_sync_manager(
    cluster_singleton: clusterlib.ClusterLib,  # noqa: ARG001
) -> tp.Generator[db_sync.DBSyncManager]:
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

    def get_subtests(self) -> tp.Generator[tp.Callable]:
        """Get the DB-Sync Config scenarios.

        The scenarios are executed as subtests in the `test_dbsync_config` test,
        grouped by the db-sync config option each set exercises.
        """
        yield from self._subtests_tx_out()
        yield from self._subtests_governance()
        yield from self._subtests_tx_cbor()
        yield from self._subtests_multi_asset()
        yield from self._subtests_plutus()
        yield from self._subtests_metadata()
        yield from self._subtests_shelley()
        yield from self._subtests_ledger()
        yield from self._subtests_offchain_pool()
        yield from self._subtests_offchain_vote()
        yield from self._subtests_remove_jsonb()
        yield from self._subtests_presets()

    def _subtests_tx_out(self) -> tp.Generator[tp.Callable]:
        """Subtests for the `tx_out` option (modes, force_tx_in, use_address_table)."""

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

        def tx_out_consumed(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test `tx_out=consumed` (no force_tx_in).

            Consumption is tracked via `tx_out.consumed_by_tx_id`, so `tx_in` stays empty
            while `tx_out` / `ma_tx_out` are populated.
            """
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_tx_out(
                    value=db_sync.TxOutMode.CONSUMED, force_tx_in=False
                )
            )
            check_dbsync_state(
                expected_state={
                    db_sync.Table.TX_OUT: TableCondition.NOT_EMPTY,
                    db_sync.Table.MA_TX_OUT: TableCondition.NOT_EMPTY,
                    db_sync.Table.TX_IN: TableCondition.EMPTY,
                }
            )
            assert dbsync_utils.column_exists(
                table=db_sync.Table.TX_OUT, column="consumed_by_tx_id"
            ), "`consumed` mode should add the `tx_out.consumed_by_tx_id` column"

        yield tx_out_consumed

        def tx_out_consumed_force_tx_in(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test `tx_out=consumed` with force_tx_in: `tx_in` is populated alongside `tx_out`."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_tx_out(
                    value=db_sync.TxOutMode.CONSUMED, force_tx_in=True
                )
            )
            check_dbsync_state(
                expected_state={
                    db_sync.Table.TX_OUT: TableCondition.NOT_EMPTY,
                    db_sync.Table.TX_IN: TableCondition.NOT_EMPTY,
                }
            )

        yield tx_out_consumed_force_tx_in

        def tx_out_use_address_table(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test `tx_out` with use_address_table: addresses go to a separate `address` table."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_tx_out(
                    value=db_sync.TxOutMode.ENABLE, use_address_table=True
                )
            )
            check_dbsync_state(
                expected_state={
                    db_sync.Table.ADDRESS: TableCondition.EXISTS,
                    db_sync.Table.TX_OUT: TableCondition.NOT_EMPTY,
                }
            )
            assert not dbsync_utils.table_empty(table=db_sync.Table.ADDRESS), (
                "`use_address_table` should populate the `address` table"
            )

        yield tx_out_use_address_table

    def _subtests_governance(self) -> tp.Generator[tp.Callable]:
        """Subtests for the `governance` option."""

        def governance(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test `governance` option."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_governance(value=db_sync.SettingState.ENABLE)
            )

            # Off-chain data is inserted into the DB a few minutes after the restart of db-sync
            wait_for_tables_not_empty(GOVERNANCE_TABLES, timeout=600)

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

    def _subtests_tx_cbor(self) -> tp.Generator[tp.Callable]:
        """Subtests for the `tx_cbor` option."""

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

    def _subtests_multi_asset(self) -> tp.Generator[tp.Callable]:
        """Subtests for the `multi_asset` option."""

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

    def _subtests_plutus(self) -> tp.Generator[tp.Callable]:
        """Subtests for the `plutus` option."""

        def plutus_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `plutus`: script / redeemer / redeemer_data / datum are populated.

            Needs a Plutus tx that locks/spends with a datum on chain.
            """
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(custom_config=db_config.with_plutus(enable=True))
            check_dbsync_state(
                expected_state={
                    db_sync.Table.SCRIPT: TableCondition.NOT_EMPTY,
                    db_sync.Table.REDEEMER: TableCondition.NOT_EMPTY,
                    db_sync.Table.REDEEMER_DATA: TableCondition.NOT_EMPTY,
                    db_sync.Table.DATUM: TableCondition.NOT_EMPTY,
                }
            )

        yield plutus_enable

        def plutus_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `plutus`: no script-execution data.

            redeemer / redeemer_data / datum stay empty despite a Plutus tx on chain.
            """
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(custom_config=db_config.with_plutus(enable=False))
            check_dbsync_state(
                expected_state={
                    db_sync.Table.REDEEMER: TableCondition.EMPTY,
                    db_sync.Table.REDEEMER_DATA: TableCondition.EMPTY,
                    db_sync.Table.DATUM: TableCondition.EMPTY,
                }
            )

        yield plutus_disable

    def _subtests_metadata(self) -> tp.Generator[tp.Callable]:
        """Subtests for the `metadata` option (enable/disable and keys filter)."""

        def metadata_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `metadata`: tx_metadata is populated from txs carrying metadata."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(custom_config=db_config.with_metadata(enable=True))
            check_dbsync_state(expected_state={db_sync.Table.TX_METADATA: TableCondition.NOT_EMPTY})

        yield metadata_enable

        def metadata_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `metadata`: tx_metadata stays empty despite a tx with metadata."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(custom_config=db_config.with_metadata(enable=False))
            check_dbsync_state(expected_state={db_sync.Table.TX_METADATA: TableCondition.EMPTY})

        yield metadata_disable

        def metadata_keys_filter(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test the `metadata.keys` filter: only metadata with the listed key is stored."""
            keep_key = 2
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_metadata(enable=True, keys=[keep_key])
            )
            check_dbsync_state(expected_state={db_sync.Table.TX_METADATA: TableCondition.NOT_EMPTY})
            # Every stored metadata row must have the single kept key.
            dbsync_utils.check_column_condition(
                table=db_sync.Table.TX_METADATA, column="key", condition=f"= {keep_key}"
            )

        yield metadata_keys_filter

    def _subtests_shelley(self) -> tp.Generator[tp.Callable]:
        """Subtests for the `shelley` option (enable/disable side effects)."""

        def shelley_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `shelley`: certificate data (stake_registration, pool_update) is set."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(custom_config=db_config.with_shelley(enable=True))
            check_dbsync_state(
                expected_state={
                    db_sync.Table.STAKE_REGISTRATION: TableCondition.NOT_EMPTY,
                    db_sync.Table.POOL_UPDATE: TableCondition.NOT_EMPTY,
                }
            )

        yield shelley_enable

        def shelley_disable_independence(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `shelley` is independent of `ledger`.

            `epoch_stake` is ledger-controlled, so it stays populated with shelley off.
            Certificate tables are not asserted empty: tx-era certs are gated by `shelley`,
            but genesis pool/stake registrations are inserted unconditionally
            (Shelley/Genesis.hs), so `pool_update` / `stake_registration` keep genesis rows.
            """
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(custom_config=db_config.with_shelley(enable=False))
            check_dbsync_state(
                expected_state={
                    db_sync.Table.EPOCH_STAKE: TableCondition.NOT_EMPTY,
                }
            )

        yield shelley_disable_independence

    def _subtests_ledger(self) -> tp.Generator[tp.Callable]:
        """Subtests for the `ledger` modes and `pool_stat` (ledger-derived data)."""
        # Tables populated only from ledger state; empty unless `ledger` maintains and uses it.
        ledger_derived_tables = (
            db_sync.Table.REWARD,
            db_sync.Table.EPOCH_STAKE,
            db_sync.Table.ADA_POTS,
            db_sync.Table.EPOCH_PARAM,
        )

        def ledger_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `ledger`: ledger-derived tables and columns are populated.

            reward / epoch_stake / ada_pots / epoch_param, redeemer.fee and tx deposits.
            """
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_ledger(value=db_sync.LedgerMode.ENABLE)
            )
            check_dbsync_state(
                expected_state={
                    **dict.fromkeys(ledger_derived_tables, TableCondition.NOT_EMPTY),
                    db_sync.Column.Redeemer.FEE: ColumnCondition.IS_NOT_NULL,
                }
            )
            # With ledger state, deposits are computed: at least one tx has a positive deposit.
            assert (
                dbsync_queries.query_rows_count(table="tx", column="deposit", condition="> 0") > 0
            ), "ledger=enable should record ledger-derived (positive) tx deposits"

        yield ledger_enable

        def ledger_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `ledger`: derived tables empty, redeemer.fee null, no deposits."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_ledger(value=db_sync.LedgerMode.DISABLE)
            )
            check_dbsync_state(
                expected_state={
                    **dict.fromkeys(ledger_derived_tables, TableCondition.EMPTY),
                    db_sync.Column.Redeemer.FEE: ColumnCondition.IS_NULL,
                }
            )
            # No ledger state -> no positive deposits (tx.deposit isn't uniformly NULL; some
            # txs keep 0, so assert the meaningful effect: no positive deposit remains).
            assert (
                dbsync_queries.query_rows_count(table="tx", column="deposit", condition="> 0") == 0
            ), "ledger=disable should drop ledger-derived (positive) tx deposits"

        yield ledger_disable

        def ledger_ignore(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test `ledger=ignore`: state is kept but unused, so derived tables stay empty."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_ledger(value=db_sync.LedgerMode.IGNORE)
            )
            check_dbsync_state(
                expected_state=dict.fromkeys(ledger_derived_tables, TableCondition.EMPTY)
            )

        yield ledger_ignore

        def pool_stat_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `pool_stat`: per-epoch pool stats stored after an epoch boundary."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_pool_stat(value=db_sync.SettingState.ENABLE)
            )
            check_dbsync_state(expected_state={db_sync.Table.POOL_STAT: TableCondition.NOT_EMPTY})

        yield pool_stat_enable

        def pool_stat_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `pool_stat` option."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_pool_stat(value=db_sync.SettingState.DISABLE)
            )
            check_dbsync_state(expected_state={db_sync.Table.POOL_STAT: TableCondition.EMPTY})

        yield pool_stat_disable

    def _subtests_offchain_pool(self) -> tp.Generator[tp.Callable]:
        """Subtests for `offchain_pool_data` (needs --allow-private-offchain-urls)."""

        def offchain_pool_data_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `offchain_pool_data`: pool metadata is fetched into off_chain_pool_data.

            Fetch is async (~300s loop) so the table is polled. Skipped without private URLs
            allowed or when no pool metadata is registered on chain.
            """
            if not dbsync_utils.allow_private_offchain_urls_enabled():
                pytest.skip("requires db-sync started with --allow-private-offchain-urls")

            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_offchain_pool_data(value=db_sync.SettingState.ENABLE)
            )
            if dbsync_utils.table_empty(table=db_sync.Table.POOL_METADATA_REF):
                pytest.skip("no pool metadata registered on chain to fetch")
            wait_for_tables_not_empty([db_sync.Table.OFF_CHAIN_POOL_DATA], timeout=600)
            check_dbsync_state(
                expected_state={
                    db_sync.Table.OFF_CHAIN_POOL_DATA: TableCondition.NOT_EMPTY,
                    db_sync.Table.OFF_CHAIN_POOL_FETCH_ERROR: TableCondition.EMPTY,
                }
            )

        yield offchain_pool_data_enable

        def offchain_pool_data_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `offchain_pool_data`: no fetch.

            off_chain_pool_data / off_chain_pool_fetch_error stay empty; the on-chain
            pool_metadata_ref is still recorded.
            """
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_offchain_pool_data(value=db_sync.SettingState.DISABLE)
            )
            check_dbsync_state(
                expected_state={
                    db_sync.Table.OFF_CHAIN_POOL_DATA: TableCondition.EMPTY,
                    db_sync.Table.OFF_CHAIN_POOL_FETCH_ERROR: TableCondition.EMPTY,
                    db_sync.Table.POOL_METADATA_REF: TableCondition.NOT_EMPTY,
                }
            )

        yield offchain_pool_data_disable

    def _subtests_offchain_vote(self) -> tp.Generator[tp.Callable]:
        """Subtests for `offchain_vote_data` (needs --allow-private-offchain-urls)."""

        def offchain_vote_data_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test `offchain_vote_data=disable` gates the fetch independently of `governance`.

            With governance on but vote data off, voting_anchor is recorded but no anchor
            metadata is fetched, so all off_chain_vote_* tables stay empty.
            """
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_governance(
                    value=db_sync.SettingState.ENABLE
                ).with_offchain_vote_data(value=db_sync.SettingState.DISABLE)
            )
            check_dbsync_state(
                expected_state={
                    db_sync.Table.VOTING_ANCHOR: TableCondition.NOT_EMPTY,
                    **dict.fromkeys(OFFCHAIN_VOTE_TABLES, TableCondition.EMPTY),
                }
            )

        yield offchain_vote_data_disable

        def offchain_vote_data_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `offchain_vote_data`: anchor metadata is fetched.

            The fetch result lands in off_chain_vote_data (or off_chain_vote_fetch_error).
            Skipped without private URLs allowed or with no fetchable vote anchor on chain.
            is_valid and the CIP sub-tables need conformant anchors; asserting those (using
            the #3497 anchor vectors) is left to a dedicated off-chain test.
            """
            if not dbsync_utils.allow_private_offchain_urls_enabled():
                pytest.skip("requires db-sync started with --allow-private-offchain-urls")
            if (
                dbsync_queries.query_rows_count(
                    table="voting_anchor", column="url", condition="!= ''"
                )
                == 0
            ):
                pytest.skip("no fetchable governance vote anchors on chain")

            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_governance(
                    value=db_sync.SettingState.ENABLE
                ).with_offchain_vote_data(value=db_sync.SettingState.ENABLE)
            )

            # The fetch is asynchronous; wait until db-sync has recorded the result either as
            # fetched data or as a fetch error.
            def _query_func() -> bool:
                data = not dbsync_utils.table_empty(table=db_sync.Table.OFF_CHAIN_VOTE_DATA)
                err = not dbsync_utils.table_empty(table=db_sync.Table.OFF_CHAIN_VOTE_FETCH_ERROR)
                if not (data or err):
                    msg = "off_chain_vote_data / off_chain_vote_fetch_error still empty"
                    raise dbsync_utils.DbSyncNoResponseError(msg)
                return True

            dbsync_utils.retry_query(query_func=_query_func, timeout=600)

        yield offchain_vote_data_enable

    def _subtests_remove_jsonb(self) -> tp.Generator[tp.Callable]:
        """Subtests for `remove_jsonb_from_schema` column-type effects."""
        # jsonb columns controlled by remove_jsonb_from_schema (excluding *.json columns, which
        # `json_type` also governs). Column types are schema-level, so row counts don't matter.
        jsonb_columns = (
            (db_sync.Table.DATUM, "value"),
            (db_sync.Table.COST_MODEL, "costs"),
            (db_sync.Table.GOV_ACTION_PROPOSAL, "description"),
        )

        def remove_jsonb_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `remove_jsonb_from_schema`: jsonb columns keep the jsonb type."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_remove_jsonb_from_schema(
                    value=db_sync.SettingState.DISABLE
                )
            )
            for table, column in jsonb_columns:
                assert dbsync_utils.column_data_type(table=table, column=column) == "jsonb", (
                    f"{table}.{column} should be jsonb when remove_jsonb_from_schema is disabled"
                )

        yield remove_jsonb_disable

        def remove_jsonb_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `remove_jsonb_from_schema`: jsonb columns drop the jsonb type."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_remove_jsonb_from_schema(
                    value=db_sync.SettingState.ENABLE
                )
            )
            for table, column in jsonb_columns:
                dtype = dbsync_utils.column_data_type(table=table, column=column)
                assert dtype is not None and dtype != "jsonb", (
                    f"{table}.{column} should not be jsonb when remove_jsonb_from_schema is "
                    f"enabled (got {dtype})"
                )

        yield remove_jsonb_enable

    def _subtests_presets(self) -> tp.Generator[tp.Callable]:
        """Subtests for insert-option presets (exercise db-sync's own preset expansion)."""

        def preset_full(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test the `full` preset: all insert options on except tx_cbor and off-chain."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_preset(preset=db_sync.Preset.FULL)
            )
            check_dbsync_state(
                expected_state={
                    db_sync.Table.TX_OUT: TableCondition.NOT_EMPTY,
                    db_sync.Table.MA_TX_OUT: TableCondition.NOT_EMPTY,
                    db_sync.Table.TX_METADATA: TableCondition.NOT_EMPTY,
                    db_sync.Table.REDEEMER: TableCondition.NOT_EMPTY,
                    db_sync.Table.DREP_REGISTRATION: TableCondition.NOT_EMPTY,
                    db_sync.Table.TX_CBOR: TableCondition.EMPTY,
                }
            )

        yield preset_full

        def preset_only_utxo(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test the `only_utxo` preset (docs: block/tx/tx_out/ma_tx_out only).

            tx_out/ma_tx_out are not asserted: bootstrap bulk-loads them only at tip, which
            is flaky on a dev cluster. db-sync also crashes syncing under this preset
            (dbsync #2150), so the restart is xfailed while that is open. If it does sync, the
            preset still enables governance contrary to the docs (dbsync #2151).
            """
            db_config = db_sync_manager.get_config_builder()

            try:
                db_sync_manager.restart_with_config(
                    custom_config=db_config.with_preset(preset=db_sync.Preset.ONLY_UTXO)
                )
            except Exception:
                # db-sync bootstrap crash under only_utxo (dbsync #2150).
                issues.dbsync_2150.finish_test()

            check_dbsync_state(
                expected_state={
                    db_sync.Table.TX_METADATA: TableCondition.EMPTY,
                    db_sync.Table.REDEEMER: TableCondition.EMPTY,
                }
            )
            if not dbsync_utils.table_empty(table=db_sync.Table.DREP_REGISTRATION):
                issues.dbsync_2151.finish_test()
            check_dbsync_state(
                expected_state={db_sync.Table.DREP_REGISTRATION: TableCondition.EMPTY}
            )

        yield preset_only_utxo

        def preset_only_governance(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test the `only_governance` preset: governance data, no tx_out / multi_asset."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_preset(preset=db_sync.Preset.ONLY_GOVERNANCE)
            )
            check_dbsync_state(
                expected_state={
                    db_sync.Table.TX_OUT: TableCondition.EMPTY,
                    db_sync.Table.MA_TX_OUT: TableCondition.EMPTY,
                    db_sync.Table.DREP_REGISTRATION: TableCondition.NOT_EMPTY,
                }
            )

        yield preset_only_governance

        def preset_disable_all(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test the `disable_all` preset: only block/tx and ledger-related data."""
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_preset(preset=db_sync.Preset.DISABLE_ALL)
            )
            check_dbsync_state(
                expected_state={
                    db_sync.Table.TX_OUT: TableCondition.EMPTY,
                    db_sync.Table.MA_TX_OUT: TableCondition.EMPTY,
                    db_sync.Table.REDEEMER: TableCondition.EMPTY,
                    db_sync.Table.DREP_REGISTRATION: TableCondition.EMPTY,
                }
            )

        yield preset_disable_all

    @allure.link(helpers.get_vcs_link())
    def test_dbsync_config(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        db_sync_manager: db_sync.DBSyncManager,
        subtests: pytest_subtests.SubTests,
    ):
        """Test DB-Sync configuration options using multiple subtests.

        Verifies that different DB-Sync configuration settings correctly control table population
        and data insertion behavior. Each subtest modifies the configuration, restarts DB-Sync
        (recreating and re-syncing the database), and validates the expected database state.

        Covers, grouped by config option:

        * `tx_out` (enable/disable/consumed modes, force_tx_in, use_address_table)
        * `governance`, `tx_cbor`, `multi_asset` (enable/disable)
        * `plutus`, `metadata` (+ keys filter), `shelley` (enable/disable side effects)
        * `ledger` (enable/disable/ignore) and `pool_stat`
        * `offchain_pool_data` and `offchain_vote_data` (need --allow-private-offchain-urls)
        * `remove_jsonb_from_schema` (column-type effects)
        * insert-option presets (`full`, `only_utxo`, `only_governance`, `disable_all`)
        * Restore original DB-Sync configuration after all subtests complete
        """
        cluster = cluster_singleton
        common.get_test_id(cluster)

        for subt in self.get_subtests():
            with subtests.test(scenario=getattr(subt, "__name__", "")):
                subt(db_sync_manager)
