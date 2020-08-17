import logging
from pathlib import Path

import pytest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir and change to it."""
    tmp_path = Path(tmp_path_factory.mktemp("test_update_proposal"))
    with helpers.change_cwd(tmp_path):
        yield tmp_path


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


@pytest.mark.first
def test_update_proposal(cluster_func: clusterlib.ClusterLib):
    """Submit update proposal."""
    helpers.update_params(
        cluster_obj=cluster_func,
        cli_arg="--decentralization-parameter",
        param_name="decentralisationParam",
        param_value=0.5,
    )
