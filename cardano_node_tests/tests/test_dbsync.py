"""Tests for db-sync."""
import logging
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from packaging import version

from cardano_node_tests.tests import common
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    p = Path(tmp_path_factory.getbasetemp()).joinpath(helpers.get_id_for_mktemp(__file__)).resolve()
    p.mkdir(exist_ok=True, parents=True)
    return p


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


# use the "temp_dir" fixture for all tests automatically; all tests in this module need dbsync
pytestmark = [pytest.mark.usefixtures("temp_dir"), pytest.mark.needs_dbsync]


class TestDBSync:
    """General db-sync tests."""

    DBSYNC_TABLES = {
        "ada_pots",
        "admin_user",
        "block",
        "collateral_tx_in",
        "cost_model",
        "datum",
        "delegation",
        "delisted_pool",
        "epoch",
        "epoch_param",
        "epoch_reward_total_received",
        "epoch_stake",
        "epoch_sync_time",
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
        VERSIONS.dbsync < version.parse("12.0.0"),
        reason="needs db-sync version >= 12.0.0",
    )
    @pytest.mark.testnets
    def test_table_names(self, cluster):
        """Check that all the expected tables are present in db-sync."""
        # pylint: disable=unused-argument
        common.get_test_id(cluster)
        assert self.DBSYNC_TABLES.issubset(dbsync_queries.query_table_names())

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(-1)
    @pytest.mark.testnets
    def test_blocks(self, cluster):  # noqa: C901
        """Check expected values in the `block` table in db-sync."""
        # pylint: disable=too-many-branches
        common.get_test_id(cluster)

        tip = cluster.get_tip()
        block_no = int(tip["block"])
        epoch = int(tip["epoch"])

        # check records for last 50 epochs
        epoch_from = epoch - 50
        epoch_from = epoch_from if epoch_from >= 0 else 0

        rec = None
        prev_rec = None
        errors = []
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

            if rec.epoch_no < prev_rec.epoch_no:
                errors.append(
                    "'epoch_no' value is different than expected; "
                    f"Expected: value >= {prev_rec.epoch_no} vs Returned: {rec.epoch_no}"
                )

            if rec.epoch_no > prev_rec.epoch_no + 1:
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

            if rec.slot_no < prev_rec.slot_no:
                errors.append(
                    "'slot_no' value is different than expected; "
                    f"Expected: value >= {prev_rec.slot_no} vs Returned: {rec.slot_no}"
                )

            if rec.epoch_slot_no is None or rec.epoch_slot_no < 0:
                errors.append(
                    "'epoch_slot_no' value is different than expected; "
                    f"Expected: positive int vs Returned: {rec.epoch_slot_no}"
                )

            if rec.epoch_slot_no <= prev_rec.epoch_slot_no and rec.epoch_no == prev_rec.epoch_no:
                errors.append(
                    "'epoch_slot_no' value is different than expected; "
                    f"Expected: value > {prev_rec.epoch_slot_no} vs Returned: {rec.epoch_slot_no}"
                )

            if rec.block_no is None or rec.block_no != prev_rec.block_no + 1:
                errors.append(
                    "'block_no' value is different than expected; "
                    f"Expected: {prev_rec.block_no + 1} vs Returned: {rec.block_no}"
                )

            if rec.previous_id is None or rec.previous_id != prev_rec.id:
                errors.append(
                    "'previous_id' value is different than expected; "
                    f"Expected: {prev_rec.id} vs Returned: {rec.previous_id}"
                )

            prev_rec = rec

        if errors:
            errors_str = "\n".join(errors)
            raise AssertionError(errors_str)

        # db-sync can be max 1 block behind or ahead
        if rec and block_no not in (rec.block_no, rec.block_no - 1, rec.block_no + 1):
            raise AssertionError(
                "last `block_no` value is different than expected; "
                f"{block_no} not in ({rec.block_no}, {rec.block_no - 1}, {rec.block_no + 1})"
            )
