"""Tests for protocol state and protocol parameters."""
import json
import logging
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

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


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


PROTOCOL_STATE_KEYS = ("csLabNonce", "csProtocol", "csTickn")
PROTOCOL_PARAM_KEYS = (
    "decentralization",
    "extraPraosEntropy",
    "maxBlockBodySize",
    "maxBlockHeaderSize",
    "maxTxSize",
    "minPoolCost",
    "minUTxOValue",
    "monetaryExpansion",
    "poolPledgeInfluence",
    "poolRetireMaxEpoch",
    "protocolVersion",
    "stakeAddressDeposit",
    "stakePoolDeposit",
    "stakePoolTargetNum",
    "treasuryCut",
    "txFeeFixed",
    "txFeePerByte",
)


@pytest.mark.testnets
class TestProtocol:
    """Basic tests for protocol."""

    @allure.link(helpers.get_vcs_link())
    def test_protocol_state_keys(self, cluster: clusterlib.ClusterLib):
        """Check output of `query protocol-state`."""
        protocol_state = cluster.get_protocol_state()
        assert tuple(sorted(protocol_state)) == PROTOCOL_STATE_KEYS

    @allure.link(helpers.get_vcs_link())
    def test_protocol_state_outfile(self, cluster: clusterlib.ClusterLib):
        """Check output file produced by `query protocol-state`."""
        protocol_state: dict = json.loads(
            cluster.query_cli(["protocol-state", *cluster.era_arg, "--out-file", "/dev/stdout"])
        )
        assert tuple(sorted(protocol_state)) == PROTOCOL_STATE_KEYS

    @allure.link(helpers.get_vcs_link())
    def test_protocol_params(self, cluster: clusterlib.ClusterLib):
        """Check output of `query protocol-parameters`."""
        protocol_params = cluster.get_protocol_params()
        assert tuple(sorted(protocol_params.keys())) == PROTOCOL_PARAM_KEYS
