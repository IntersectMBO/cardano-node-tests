"""Tests for db-sync."""
import logging
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


# all tests in this module need dbsync
pytestmark = pytest.mark.needs_dbsync


class TestDBSync:
    """General db-sync tests."""

    DBSYNC_TABLES = {
        "ada_pots",
        "block",
        "collateral_tx_in",
        "collateral_tx_out",
        "cost_model",
        "datum",
        "delegation",
        "delisted_pool",
        "epoch",
        "epoch_param",
        "epoch_stake",
        "epoch_sync_time",
        "extra_key_witness",
        "ma_tx_mint",
        "ma_tx_out",
        "meta",
        "multi_asset",
        "param_proposal",
        "pool_hash",
        "pool_metadata_ref",
        "pool_offline_data",
        "pool_offline_fetch_error",
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
        "schema_version",
        "script",
        "slot_leader",
        "stake_address",
        "stake_deregistration",
        "stake_registration",
        "treasury",
        "tx",
        "tx_in",
        "tx_metadata",
        "tx_out",
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
        """Check that all the expected tables are present in db-sync."""
        common.get_test_id(cluster)
        assert self.DBSYNC_TABLES.issubset(dbsync_queries.query_table_names())

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(-10)
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_blocks(self, cluster: clusterlib.ClusterLib):  # noqa: C901
        """Check expected values in the `block` table in db-sync."""
        # pylint: disable=too-many-branches
        common.get_test_id(cluster)

        tip = cluster.g_query.get_tip()
        block_no = int(tip["block"])
        epoch = int(tip["epoch"])

        # check records for last 50 epochs
        epoch_from = epoch - 50
        epoch_from = epoch_from if epoch_from >= 0 else 0

        rec = None
        prev_rec = None
        errors: List[str] = []
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

            if rec.block_no is None or (
                prev_rec.block_no and rec.block_no != prev_rec.block_no + 1
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
            raise AssertionError(
                "last `block_no` value is different than expected; "
                f"{block_no} not in ({rec.block_no}, {rec.block_no - 1}, {rec.block_no + 1})"
            )

        # if cardano-node knows about Babbage and network is in Alonzo or higher era, check that
        # the highest known protocol major version matches the expected value
        if (
            rec
            and VERSIONS.cluster_era >= VERSIONS.ALONZO
            and clusterlib_utils.cli_has("transaction build --babbage-era")
            and not (rec.proto_major == 7 and rec.proto_minor == 2)
        ):
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
        """Check expected values in the `cost_model` table in db-sync."""
        common.get_test_id(cluster)

        db_cost_models = dbsync_queries.query_cost_model()
        # wait till next epoch if the cost models are not yet available
        if not db_cost_models:
            cluster.wait_for_new_epoch(padding_seconds=5)
            db_cost_models = dbsync_queries.query_cost_model()

        protocol_params = cluster.g_query.get_protocol_params()
        pp_cost_models = protocol_params["costModels"]

        assert (
            pp_cost_models["PlutusScriptV1"] == db_cost_models["PlutusV1"]
        ), "PlutusV1 cost model is not the expected"
        assert (
            pp_cost_models["PlutusScriptV2"] == db_cost_models["PlutusV2"]
        ), "PlutusV2 cost model is not the expected"
