"""Tests for cardano-cli that doesn't fit into any other test file."""
import logging
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

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


@pytest.mark.testnets
class TestCLI:
    """Tests for cardano-cli."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.MARY,
        reason="runs on Mary+",
    )
    def test_default_era(self, cluster: clusterlib.ClusterLib):
        """Check the default era - command works even without specifying era."""
        cluster.cli(["query", "utxo", *cluster.magic_args, f"--{cluster.protocol}-mode"])

    @allure.link(helpers.get_vcs_link())
    def test_protocol_mode(self, cluster: clusterlib.ClusterLib):
        """Check the default protocol mode - command works even without specifying protocol mode."""
        if not cluster.protocol != clusterlib.Protocols.CARDANO:
            pytest.skip("runs on cluster in full cardano mode")
        cluster.cli(["query", "utxo", *cluster.magic_args, *cluster.era_arg])
