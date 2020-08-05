import logging

import pytest

from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory):
    """Create a temporary dir and change to it."""
    tmp_path = tmp_path_factory.mktemp("test_update_proposal")
    with helpers.change_cwd(tmp_path):
        yield tmp_path


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


@pytest.mark.clean_cluster
def test_update_proposal(cluster):
    """Submit update proposal."""
    helpers.update_params(
        cluster_obj=cluster,
        cli_arg="--decentralization-parameter",
        param_name="decentralisationParam",
        param_value=0.5,
    )
