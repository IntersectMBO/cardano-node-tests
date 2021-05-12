"""Tests for Node Tip Query."""
import logging
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers

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


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


NODE_TIP_DATA = {
    "epoch": 21,
    "hash": "ba84636689deed56f8a4455e55484eaae7328e6f7404ade3d0184a7852beeeb4",
    "slot": 2654576,
    "block": 2653061
}


class TestNodeTipQuery:
    """Basic tests for node tip."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        bool(configuration.TX_ERA),
        reason="different TX eras doesn't affect this test, pointless to run",
    )
    def test_node_tip_query(self, cluster: clusterlib.ClusterLib):
        """Check output of `cardano-cli query tip`."""
        node_tip = clusterlib_utils.get_node_tip(cluster_obj=cluster)
        assert dict(node_tip) == NODE_TIP_DATA
