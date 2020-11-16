"""Tests for update proposal."""
import logging
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import parallel_run

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir and change to it."""
    tmp_path = Path(tmp_path_factory.mktemp(helpers.get_id_for_mktemp(__file__)))
    with helpers.change_cwd(tmp_path):
        yield tmp_path


@pytest.fixture
def cluster_update_proposal(cluster_manager: parallel_run.ClusterManager) -> clusterlib.ClusterLib:
    return cluster_manager.get(singleton=True, cleanup=True)


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


class TestBasic:
    """Basic tests for update proposal."""

    @allure.link(helpers.get_vcs_link())
    def test_update_proposal(self, cluster_update_proposal: clusterlib.ClusterLib):
        """Test changing *decentralisationParam* using update proposal ."""
        clusterlib_utils.update_params(
            cluster_obj=cluster_update_proposal,
            cli_arg="--decentralization-parameter",
            param_name="decentralisationParam",
            param_value=0.5,
        )
