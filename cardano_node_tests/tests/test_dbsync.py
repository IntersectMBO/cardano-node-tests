"""Tests for db-sync."""
import logging
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from packaging import version

from cardano_node_tests.utils import dbsync_utils
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
        "delegation",
        "epoch",
        "epoch_param",
        "epoch_stake",
        "epoch_sync_time",
        "ma_tx_mint",
        "ma_tx_out",
        "meta",
        "orphaned_reward",
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
        "reserve",
        "reserved_pool_ticker",
        "reward",
        "schema_version",
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
        VERSIONS.dbsync < version.parse("10.0.0"),
        reason="needs db-sync version >= 10.0.0",
    )
    def test_table_names(self, cluster):
        """Check that all the expected tables are present in db-sync."""
        # pylint: disable=unused-argument
        assert self.DBSYNC_TABLES.issubset(dbsync_utils.query_table_names())
