"""Tests for protocol state and protocol parameters."""
import logging
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    return Path(tmp_path_factory.mktemp(helpers.get_id_for_mktemp(__file__))).resolve()


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


@pytest.mark.testnets
class TestProtocol:
    """Basic tests for protocol."""

    @allure.link(helpers.get_vcs_link())
    def test_protocol_state(self, cluster: clusterlib.ClusterLib):
        """Check output of `query protocol-state`."""
        protocol_state = cluster.get_protocol_state()
        assert sorted(protocol_state) == ["csLabNonce", "csProtocol", "csTickn"]

    @allure.link(helpers.get_vcs_link())
    def test_protocol_params(self, cluster: clusterlib.ClusterLib):
        """Check output of `query protocol-parameters`."""
        protocol_params = cluster.get_protocol_params()
        assert sorted(protocol_params.keys()) == [
            "a0",
            "decentralisationParam",
            "eMax",
            "extraEntropy",
            "keyDeposit",
            "maxBlockBodySize",
            "maxBlockHeaderSize",
            "maxTxSize",
            "minFeeA",
            "minFeeB",
            "minPoolCost",
            "minUTxOValue",
            "nOpt",
            "poolDeposit",
            "protocolVersion",
            "rho",
            "tau",
        ]
