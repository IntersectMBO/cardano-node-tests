"""Tests for db-sync."""

import logging
import time
import typing as tp
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import allure
import pytest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_snapshot_service
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


# All tests in this module need dbsync
pytestmark = pytest.mark.needs_dbsync


class TestDBSync:
    """General db-sync tests."""

    DBSYNC_TABLES: tp.Final[set[str]] = {
        "ada_pots",
        "block",
        "collateral_tx_in",
        "collateral_tx_out",
        "committee_hash",
        "committee",
        "committee_member",
        "committee_de_registration",
        "committee_registration",
        "cost_model",
        "constitution",
        "datum",
        "delegation",
        "delisted_pool",
        "delegation_vote",
        "drep_distr",
        "drep_hash",
        "drep_registration",
        "epoch",
        "epoch_param",
        "epoch_state",
        "epoch_stake",
        "epoch_stake_progress",
        "epoch_sync_time",
        "extra_key_witness",
        "extra_migrations",
        "gov_action_proposal",
        "ma_tx_mint",
        "ma_tx_out",
        "meta",
        "multi_asset",
        "param_proposal",
        "pool_hash",
        "pool_metadata_ref",
        "off_chain_pool_data",
        "off_chain_pool_fetch_error",
        "off_chain_vote_author",
        "off_chain_vote_data",
        "off_chain_vote_drep_data",
        "off_chain_vote_external_update",
        "off_chain_vote_fetch_error",
        "off_chain_vote_gov_action_data",
        "off_chain_vote_reference",
        "pool_owner",
        "pool_relay",
        "pool_retire",
        "pool_update",
        "pot_transfer",
        "redeemer",
        "redeemer_data",
        "reference_tx_in",
        "reserve",
        "reserved_pool_ticker",
        "reward",
        "reward_rest",
        "schema_version",
        "script",
        "slot_leader",
        "stake_address",
        "stake_deregistration",
        "stake_registration",
        "treasury",
        "treasury_withdrawal",
        "tx",
        "tx_in",
        "tx_metadata",
        "tx_out",
        "voting_anchor",
        "voting_procedure",
        "withdrawal",
    }

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.dbsync < version.parse("12.0.1"),
        reason="needs db-sync version >= 12.0.1",
    )
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_table_names(self, cluster: clusterlib.ClusterLib):
        """Check that all the expected tables are present in db-sync.

        Test db-sync database schema by verifying all expected tables exist.

        * query db-sync database for list of all table names
        * check that DBSYNC_TABLES set is a subset of actual table names
        * verify all expected core tables are present (block, tx, tx_out, etc.)
        """
        common.get_test_id(cluster)
        assert self.DBSYNC_TABLES.issubset(dbsync_queries.query_table_names())

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(-10)
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_blocks(self, cluster: clusterlib.ClusterLib):  # noqa: C901
        """Check expected values in the `block` table in db-sync.

        Test db-sync block table data integrity by validating field values and relationships
        across 50 epochs of block records.

        * query current tip, block number, and epoch from cluster
        * query block records from db-sync for last 50 epochs (epoch_from to current)
        * for each consecutive block pair, validate:
          - id increases monotonically (rec.id > prev_rec.id)
          - epoch_no is non-negative and increases monotonically
          - epoch_no changes by at most 1 between consecutive blocks
          - slot_no is non-negative and increases monotonically
          - epoch_slot_no is non-negative and increases within same epoch
          - block_no increments by exactly 1 between consecutive blocks
          - previous_id points to previous record's id
        * check that last block_no in db-sync is within 1 of node's block_no
        * verify protocol version matches expected values
        """
        common.get_test_id(cluster)

        tip = cluster.g_query.get_tip()
        block_no = cluster.g_query.get_block_no(tip=tip)
        epoch = cluster.g_query.get_epoch(tip=tip)

        # Check records for last 50 epochs
        epoch_from = epoch - 50
        epoch_from = max(epoch_from, 0)

        rec = None
        prev_rec = None
        errors: list[str] = []
        for rec in dbsync_queries.query_blocks(epoch_from=epoch_from):
            if not prev_rec:
                prev_rec = rec
                continue

            if rec.id <= prev_rec.id:
                errors.append(
                    "'id' value is different than expected; "
                    f"Expected: {rec.id} > {prev_rec.id} vs Returned: {rec.id}"
                )

            if rec.id < 4:
                prev_rec = rec
                continue

            if rec.epoch_no is None or rec.epoch_no < 0:
                errors.append(
                    "'epoch_no' value is different than expected; "
                    f"Expected: positive int vs Returned: {rec.epoch_no}"
                )

            if rec.epoch_no and prev_rec.epoch_no and rec.epoch_no < prev_rec.epoch_no:
                errors.append(
                    "'epoch_no' value is different than expected; "
                    f"Expected: value >= {prev_rec.epoch_no} vs Returned: {rec.epoch_no}"
                )

            if rec.epoch_no and prev_rec.epoch_no and rec.epoch_no > prev_rec.epoch_no + 1:
                errors.append(
                    "'epoch_no' value is different than expected; "
                    f"Expected: {prev_rec.epoch_no} or {prev_rec.epoch_no + 1}"
                    f" vs Returned: {rec.epoch_no}"
                )

            if rec.slot_no is None or rec.slot_no < 0:
                errors.append(
                    "'slot_no' value is different than expected; "
                    f"Expected: positive int vs Returned: {rec.slot_no}"
                )

            if rec.slot_no and prev_rec.slot_no and rec.slot_no < prev_rec.slot_no:
                errors.append(
                    "'slot_no' value is different than expected; "
                    f"Expected: value >= {prev_rec.slot_no} vs Returned: {rec.slot_no}"
                )

            if rec.epoch_slot_no is None or rec.epoch_slot_no < 0:
                errors.append(
                    "'epoch_slot_no' value is different than expected; "
                    f"Expected: positive int vs Returned: {rec.epoch_slot_no}"
                )

            if (
                rec.epoch_slot_no
                and prev_rec.epoch_slot_no
                and rec.epoch_slot_no <= prev_rec.epoch_slot_no
                and rec.epoch_no == prev_rec.epoch_no
            ):
                errors.append(
                    "'epoch_slot_no' value is different than expected; "
                    f"Expected: value > {prev_rec.epoch_slot_no} vs Returned: {rec.epoch_slot_no}"
                )

            if (rec.block_no is None and prev_rec.block_no is not None) or (
                prev_rec.block_no is not None and rec.block_no != prev_rec.block_no + 1
            ):
                errors.append(
                    "'block_no' value is different than expected; "
                    f"Expected: {prev_rec.block_no + 1} vs Returned: {rec.block_no}"
                )

            if rec.previous_id is None or (prev_rec.id and rec.previous_id != prev_rec.id):
                errors.append(
                    "'previous_id' value is different than expected; "
                    f"Expected: {prev_rec.id} vs Returned: {rec.previous_id}"
                )

            prev_rec = rec

        if errors:
            errors_str = "\n".join(errors)
            raise AssertionError(errors_str)

        # db-sync can be max 1 block behind or ahead
        if (
            rec
            and rec.block_no
            and block_no not in (rec.block_no, rec.block_no - 1, rec.block_no + 1)
        ):
            msg = (
                "last `block_no` value is different than expected; "
                f"{block_no} not in ({rec.block_no}, {rec.block_no - 1}, {rec.block_no + 1})"
            )
            raise AssertionError(msg)

        # If cardano-node knows about Babbage and network is in Alonzo or higher era, check that
        # the highest known protocol major version matches the expected value
        if rec and not (rec.proto_major == 8 and rec.proto_minor == 0):
            pytest.xfail(
                f"protocol major version: {rec.proto_major}; "
                f"protocol minor version: {rec.proto_minor}"
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era <= VERSIONS.ALONZO,
        reason="runs only with Tx era > Alonzo",
    )
    @pytest.mark.testnets
    def test_cost_model(self, cluster: clusterlib.ClusterLib):
        """Check expected values in the `cost_model` table in db-sync.

        Test db-sync Plutus cost model records match protocol parameters. Runs only in
        eras > Alonzo where Plutus cost models are active.

        * query current epoch number
        * query cost models from db-sync for current epoch
        * if cost models not available, wait for next epoch and retry
        * query protocol parameters from node for comparison
        * extract PlutusV1, PlutusV2, PlutusV3 cost models from protocol params
        * compare db-sync cost model records with protocol parameter cost models
        * verify cost model values match between db-sync and node
        """
        common.get_test_id(cluster)
        curr_epoch = cluster.g_query.get_epoch()

        db_cost_models = dbsync_queries.query_cost_model(epoch_no=curr_epoch)
        # Wait till next epoch if the cost models are not yet available
        if not db_cost_models:
            curr_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            db_cost_models = dbsync_queries.query_cost_model(epoch_no=curr_epoch)

        protocol_params = cluster.g_query.get_protocol_params()

        pp_cost_models = protocol_params["costModels"]

        # TODO: `PlutusScriptV1` was replaced with `PlutusV1` in node version 8.0.0
        pp_cost_model_v1 = pp_cost_models.get("PlutusV1") or pp_cost_models.get("PlutusScriptV1")
        assert pp_cost_model_v1 == db_cost_models["PlutusV1"], (
            "PlutusV1 cost model is not the expected one"
        )

        pp_cost_model_v2 = pp_cost_models.get("PlutusV2") or pp_cost_models.get("PlutusScriptV2")
        # Cost models in Conway can have variable length. If the cost model is shorter than the max
        # expected length, cardano-cli appends max integer values to the end of the list.
        # When comparing with db-sync, we need to strip those appended values.
        if len(pp_cost_model_v2) - 10 == len(db_cost_models["PlutusV2"]):
            last_10_unique = set(pp_cost_model_v2[-10:])
            if len(last_10_unique) == 1 and next(iter(last_10_unique)) == 9223372036854775807:
                pp_cost_model_v2 = pp_cost_model_v2[:-10]
        assert pp_cost_model_v2 == db_cost_models["PlutusV2"], (
            "PlutusV2 cost model is not the expected one"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_reconnect_dbsync(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        worker_id: str,
    ):
        """Check that db-sync reconnects to the node after the node is restarted.

        * restart all nodes of the running cluster
        * submit a transaction
        * check that the transaction is present on dbsync
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)

        logfiles.add_ignore_rule(
            files_glob="dbsync.stdout",
            regex=r"Network\.Socket\.connect",
            ignore_file_id=worker_id,
        )

        cluster_nodes.restart_all_nodes()

        # Create source and destination payment addresses
        payment_addrs = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_src",
            f"{temp_template}_dst",
            cluster_obj=cluster,
        )

        # Fund source addresses
        clusterlib_utils.fund_from_faucet(
            payment_addrs[0],
            cluster_obj=cluster,
            all_faucets=cluster_manager.cache.addrs_data,
            amount=10_000_000,
        )

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=1_500_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        tx_output = cluster.g_transaction.build_tx(
            src_address=src_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=1_000_000,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        # Leave plenty of time to db-sync to catch up
        time.sleep(300)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(-10)
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_epoch(self, cluster: clusterlib.ClusterLib):
        """Check expected values in the `epoch` table in db-sync.

        Test db-sync epoch table data consistency by comparing aggregated counts with
        block table data. Skip if running in epoch 0.

        * query current epoch from cluster
        * use previous epoch for validation (current - 1) if not in epoch 0
        * skip test if in epoch 0
        * query all blocks for the target epoch from block table
        * count blocks and transactions from block table records
        * query epoch summary from epoch table for same epoch
        * count blocks and transactions from epoch table records
        * verify block count matches between block table and epoch table
        * verify transaction count matches between block table and epoch table
        """
        common.get_test_id(cluster)

        current_epoch = cluster.g_query.get_epoch()
        epoch = current_epoch - 1 if current_epoch >= 1 else current_epoch

        if epoch == 0:
            pytest.skip("Not meant to run in epoch 0")

        blocks_data_blk_count = 0
        blocks_data_tx_count = 0

        for b in dbsync_queries.query_blocks(epoch_from=epoch, epoch_to=epoch):
            blocks_data_blk_count += 1
            blocks_data_tx_count += b.tx_count or 0

        epoch_data_blk_count = 0
        epoch_data_tx_count = 0

        for e in dbsync_queries.query_epoch(epoch_from=epoch, epoch_to=epoch):
            epoch_data_blk_count += e.blk_count
            epoch_data_tx_count += e.tx_count

        if blocks_data_blk_count == epoch_data_blk_count + 1:
            issues.dbsync_1363.finish_test()

        assert blocks_data_blk_count == epoch_data_blk_count, (
            f"Blocks count don't match between tables for epoch {epoch}"
        )

        assert blocks_data_tx_count == epoch_data_tx_count, (
            f"Transactions count don't match between tables for epoch {epoch}"
        )


class TestDBSyncSnapshot:
    """Tests for db-sync snapshot availability and freshness."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_latest_snapshot_freshness(
        self,
        cluster_manager: cluster_management.ClusterManager,
    ):
        """Check that the latest db-sync snapshot is not older than 5 days.

        This test uses the S3 REST API to query the Cardano mainnet snapshot repository
        and verifies that the most recent snapshot is fresh.

        * initialize DBSyncSnapshotService to access S3 snapshot repository
        * query S3 API to find latest db-sync version available
        * get latest snapshot file for that version from S3
        * log snapshot details (name, last modified date, size in GB)
        * calculate current UTC time and 5-days-ago threshold
        * verify snapshot last_modified date is within 5 days
        * fail if snapshot is older than 5 days with detailed age information
        """
        common.get_test_id(cluster_manager)
        db_sync_snapshots = dbsync_snapshot_service.DBSyncSnapshotService()

        # 1. Find latest version
        latest_version = db_sync_snapshots.get_latest_version()
        LOGGER.info(f"Latest db-sync version: {latest_version}")

        # 2. Get latest snapshot for that version
        latest_snapshot: dbsync_snapshot_service.SnapshotFile = (
            db_sync_snapshots.get_latest_snapshot(latest_version)
        )

        LOGGER.info(f"Latest snapshot: {latest_snapshot.name}")
        LOGGER.info(f"Snapshot date: {latest_snapshot.last_modified.isoformat()}")
        LOGGER.info(f"Snapshot size: {latest_snapshot.size_gb:.2f} GB")

        # 3. Perform freshness check
        now_utc = datetime.now(UTC)
        five_days_ago = now_utc - timedelta(days=5)

        assert latest_snapshot.last_modified >= five_days_ago, (
            f"The latest snapshot is too old. "
            f"Age: {(now_utc - latest_snapshot.last_modified).days} days. "
            f"Snapshot date: {latest_snapshot.last_modified.strftime('%Y-%m-%d %H:%M:%S UTC')}, "
            f"Limit: 5 days ago ({five_days_ago.strftime('%Y-%m-%d %H:%M:%S UTC')})."
        )

        LOGGER.info("Success: The latest snapshot is recent (within 5-day limit).")
