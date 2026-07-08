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
    NOT_ZERO = "column_condition:!= 0"
    IS_NULL = "column_condition:IS NULL"
    IS_NOT_NULL = "column_condition:IS NOT NULL"


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

        yield from self._subtests_phase1()
        yield from self._subtests_phase2()
        yield from self._subtests_phase3()
        yield from self._subtests_phase4()

    def _subtests_phase1(self) -> tp.Generator[tp.Callable]:
        """Phase 1 subtests: config-presence / empties (no extra on-chain activity needed)."""

        def plutus_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `plutus` option.

            With Plutus disabled, db-sync must not insert script-execution data, so the
            redeemer / redeemer_data / datum tables stay empty even though the chain
            contains a Plutus transaction.
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

        def metadata_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `metadata` option.

            With metadata disabled, the tx_metadata table stays empty even though the
            chain contains a transaction carrying metadata.
            """
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(custom_config=db_config.with_metadata(enable=False))
            check_dbsync_state(expected_state={db_sync.Table.TX_METADATA: TableCondition.EMPTY})

        yield metadata_disable

        def tx_out_consumed(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test `tx_out` in `consumed` mode (without `force_tx_in`).

            In `consumed` mode db-sync records consumption via the new
            `tx_out.consumed_by_tx_id` column instead of populating `tx_in`, so `tx_in`
            stays empty while `tx_out` / `ma_tx_out` are populated.
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
            """Test `tx_out` in `consumed` mode with `force_tx_in=True`.

            `force_tx_in` re-enables population of the `tx_in` table on top of `consumed`
            mode, so both `tx_out` and `tx_in` are populated.
            """
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
            """Test `tx_out` with `use_address_table=True`.

            With the address table enabled, db-sync normalizes addresses into a separate
            `address` table (which otherwise does not exist).
            """
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

    def _subtests_phase2(self) -> tp.Generator[tp.Callable]:
        """Phase 2 subtests: enable-side population (depends on prior on-chain activity)."""

        def plutus_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `plutus` option.

            With Plutus enabled, script-execution data is inserted: the script, redeemer,
            redeemer_data and datum tables are populated (the chain must contain a Plutus
            transaction that locks/spends with a datum).
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

        def metadata_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `metadata` option.

            With metadata enabled, the tx_metadata table is populated from transactions
            carrying metadata.
            """
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(custom_config=db_config.with_metadata(enable=True))
            check_dbsync_state(expected_state={db_sync.Table.TX_METADATA: TableCondition.NOT_EMPTY})

        yield metadata_enable

        def metadata_keys_filter(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test the `metadata.keys` filter.

            When `metadata.keys` lists specific metadata keys, db-sync stores only metadata
            with those keys; every tx_metadata row must therefore have the configured key.
            """
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

        def shelley_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `shelley` option.

            Shelley-era data (certificates, etc.) is inserted: the stake_registration and
            pool_update tables are populated.
            """
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
            """Test disabled `shelley` option and its independence from `ledger`.

            With Shelley disabled, ledger-derived data (epoch_stake) is still populated
            because it is controlled by the `ledger` option, not `shelley`.

            Note: certificate tables are deliberately NOT asserted empty here. Genesis pool
            registrations are inserted via an ungated path (Shelley/Genesis.hs), so
            `pool_update` stays populated regardless of the `shelley` flag, and legacy
            stake-registration certs are likewise ungated (covered separately by
            ``shelley_disable_stake_registration``). `epoch_stake` is therefore the clean
            signal for shelley/ledger independence.
            """
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(custom_config=db_config.with_shelley(enable=False))
            check_dbsync_state(
                expected_state={
                    db_sync.Table.EPOCH_STAKE: TableCondition.NOT_EMPTY,
                }
            )

        yield shelley_disable_independence

        def shelley_disable_stake_registration(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test that `shelley=disable` suppresses stake_registration (per the docs).

            doc/configuration.md states `shelley.enable` disables "all certificates", which
            includes stake registrations. The assertion below expresses that documented
            behavior.

            KNOWN db-sync discrepancy: legacy ``ShelleyRegCert`` stake registrations are
            inserted regardless of the flag, because
            ``Cardano.DbSync.Era.Universal.Insert.Certificate.insertDelegCert`` calls
            ``insertStakeRegistration`` without a ``when (ioShelley iopts)`` guard, unlike
            the Conway ``insertConwayDelegCert`` path which is guarded. The assertion is kept
            as the documented expectation and marked xfail until db-sync gates the legacy
            path (pending cardano-db-sync issue; convert to ``issues.dbsync_<n>.finish_test``
            once filed).
            """
            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(custom_config=db_config.with_shelley(enable=False))
            if not dbsync_utils.table_empty(table=db_sync.Table.STAKE_REGISTRATION):
                pytest.xfail(
                    "db-sync inserts legacy ShelleyRegCert stake registrations regardless of "
                    "shelley=disable (Certificate.insertDelegCert is unguarded); docs say "
                    "shelley disables all certificates."
                )
            check_dbsync_state(
                expected_state={db_sync.Table.STAKE_REGISTRATION: TableCondition.EMPTY}
            )

        yield shelley_disable_stake_registration

    def _subtests_phase3(self) -> tp.Generator[tp.Callable]:
        """Phase 3 subtests: `ledger` modes and `pool_stat` (ledger-derived data)."""
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
            """Test enabled `ledger` option.

            With ledger enabled, db-sync maintains ledger state and populates the
            ledger-derived tables (reward, epoch_stake, ada_pots, epoch_param), computes
            redeemer fees, and records ledger-derived deposits.
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
            # Ledger state lets db-sync compute deposits: at least one tx carries a positive
            # deposit (e.g. a registration deposit). Contrast with the ledger=disable case.
            assert (
                dbsync_queries.query_rows_count(table="tx", column="deposit", condition="> 0") > 0
            ), "ledger=enable should record ledger-derived (positive) tx deposits"

        yield ledger_enable

        def ledger_disable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test disabled `ledger` option.

            With ledger disabled, db-sync does not maintain ledger state: the ledger-derived
            tables stay empty and ledger-derived columns are null (redeemer.fee, tx.deposit).
            """
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
            # Ledger-derived deposits are dropped without ledger state: no tx has a positive
            # deposit. (Note: tx.deposit is not uniformly NULL - db-sync still records 0 for
            # some txs - so we assert the meaningful effect: no positive deposit remains.)
            assert (
                dbsync_queries.query_rows_count(table="tx", column="deposit", condition="> 0") == 0
            ), "ledger=disable should drop ledger-derived (positive) tx deposits"

        yield ledger_disable

        def ledger_ignore(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test `ledger` in `ignore` mode.

            In `ignore` mode db-sync maintains ledger state but does not use any of its data
            (except UTxO for bootstrap), so the ledger-derived tables stay empty, like
            `disable`.
            """
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
            """Test enabled `pool_stat` option.

            With pool_stat enabled, per-epoch pool statistics are stored in the pool_stat
            table once an epoch boundary has been crossed.
            """
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

    def _subtests_phase4(self) -> tp.Generator[tp.Callable]:
        """Phase 4 subtests: off-chain pool metadata (needs --allow-private-offchain-urls)."""

        def offchain_pool_data_enable(
            db_sync_manager: db_sync.DBSyncManager,
        ):
            """Test enabled `offchain_pool_data` option.

            With off-chain pool data enabled (and db-sync allowed to fetch private/localhost
            URLs), db-sync fetches the cluster pools' registered metadata and stores it in
            off_chain_pool_data, with no fetch errors. The fetch is asynchronous (the fetch
            loop sleeps ~300s between passes), so off_chain_pool_data is polled.
            """
            if not dbsync_utils.allow_private_offchain_urls_enabled():
                pytest.skip("requires db-sync started with --allow-private-offchain-urls")

            db_config = db_sync_manager.get_config_builder()

            db_sync_manager.restart_with_config(
                custom_config=db_config.with_offchain_pool_data(value=db_sync.SettingState.ENABLE)
            )
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
            """Test disabled `offchain_pool_data` option.

            With off-chain pool data disabled, db-sync does not fetch pool metadata, so both
            off_chain_pool_data and off_chain_pool_fetch_error stay empty. The on-chain
            metadata reference (pool_metadata_ref) is still recorded.
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

    @allure.link(helpers.get_vcs_link())
    def test_dbsync_config(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        db_sync_manager: db_sync.DBSyncManager,
        subtests: pytest_subtests.SubTests,
    ):
        """Test DB-Sync configuration options using multiple subtests.

        Verifies that different DB-Sync configuration settings correctly control table population
        and data insertion behavior. Each subtest modifies the configuration, restarts DB-Sync,
        and validates the expected database state.

        * Test `tx_out` option (enable/disable modes with various settings)
        * Verify address, tx_in, tx_out, and ma_tx_out tables respond to tx_out configuration
        * Test `governance` option (enable/disable)
        * Verify all governance-related tables populate when enabled and clear when disabled
        * Test `tx_cbor` option (enable/disable)
        * Verify tx_cbor table populates when enabled and clears when disabled
        * Test `multi_asset` option (enable/disable)
        * Verify multi_asset table populates when enabled and clears when disabled
        * Restore original DB-Sync configuration after all subtests complete
        """
        cluster = cluster_singleton
        common.get_test_id(cluster)

        for subt in self.get_subtests():
            with subtests.test(scenario=getattr(subt, "__name__", "")):
                subt(db_sync_manager)
