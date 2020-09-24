import logging
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import parallel_run

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir and change to it."""
    tmp_path = Path(tmp_path_factory.mktemp(helpers.get_id_for_mktemp(__file__)))
    with helpers.change_cwd(tmp_path):
        yield tmp_path


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


@allure.link(helpers.get_vcs_link())
def test_update_proposal(cluster_manager: parallel_run.ClusterManager):
    """Submit update proposal."""
    cluster = cluster_manager.get(singleton=True, cleanup=True)

    helpers.update_params(
        cluster_obj=cluster,
        cli_arg="--decentralization-parameter",
        param_name="decentralisationParam",
        param_value=0.5,
    )
