"""Tests for ledger state."""
import logging
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    return Path(
        tmp_path_factory.mktemp(helpers.get_id_for_mktemp(__file__), numbered=False)
    ).resolve()


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


LEDGER_STATE_KEYS = (
    "blocksBefore",
    "blocksCurrent",
    "lastEpoch",
    "possibleRewardUpdate",
    "stakeDistrib",
    "stateBefore",
)


@pytest.mark.testnets
class TestLedgerState:
    """Basic tests for ledger state."""

    @allure.link(helpers.get_vcs_link())
    def test_ledger_state_keys(self, cluster: clusterlib.ClusterLib):
        """Check output of `query ledger-state`."""
        ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
        assert tuple(sorted(ledger_state)) == LEDGER_STATE_KEYS
