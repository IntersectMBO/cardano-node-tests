"""Tests for protocol state and protocol parameters."""
import json
import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


PROTOCOL_STATE_KEYS = ("csLabNonce", "csProtocol", "csTickn")
PROTOCOL_PARAM_KEYS = (
    "collateralPercentage",
    "costModels",
    "decentralization",
    "executionUnitPrices",
    "extraPraosEntropy",
    "maxBlockBodySize",
    "maxBlockExecutionUnits",
    "maxBlockHeaderSize",
    "maxCollateralInputs",
    "maxTxExecutionUnits",
    "maxTxSize",
    "maxValueSize",
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
    "utxoCostPerWord",
)


@pytest.mark.testnets
@pytest.mark.smoke
@pytest.mark.skipif(
    VERSIONS.transaction_era != VERSIONS.DEFAULT_TX_ERA,
    reason="different TX eras doesn't affect this test, pointless to run",
)
class TestProtocol:
    """Basic tests for protocol."""

    @allure.link(helpers.get_vcs_link())
    def test_protocol_state_keys(self, cluster: clusterlib.ClusterLib):
        """Check output of `query protocol-state`."""
        common.get_test_id(cluster)
        protocol_state = cluster.get_protocol_state()
        assert tuple(sorted(protocol_state)) == PROTOCOL_STATE_KEYS

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.xfail
    def test_protocol_state_outfile(self, cluster: clusterlib.ClusterLib):
        """Check output file produced by `query protocol-state`."""
        common.get_test_id(cluster)
        protocol_state: dict = json.loads(
            cluster.query_cli(["protocol-state", "--out-file", "/dev/stdout"])
        )
        assert tuple(sorted(protocol_state)) == PROTOCOL_STATE_KEYS

    @allure.link(helpers.get_vcs_link())
    def test_protocol_params(self, cluster: clusterlib.ClusterLib):
        """Check output of `query protocol-parameters`."""
        common.get_test_id(cluster)
        protocol_params = cluster.get_protocol_params()
        assert tuple(sorted(protocol_params.keys())) == PROTOCOL_PARAM_KEYS
