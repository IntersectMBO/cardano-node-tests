"""Tests for protocol state and protocol parameters."""

import json
import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


PROTOCOL_STATE_KEYS = frozenset(
    (
        "candidateNonce",
        "epochNonce",
        "evolvingNonce",
        "labNonce",
        "lastEpochBlockNonce",
        "lastSlot",
        "oCertCounters",
    )
)
PROTOCOL_PARAM_KEYS = frozenset(
    (
        "collateralPercentage",
        "committeeMaxTermLength",
        "committeeMinSize",
        "costModels",
        "dRepActivity",
        "dRepDeposit",
        "dRepVotingThresholds",
        "executionUnitPrices",
        "govActionDeposit",
        "govActionLifetime",
        "maxBlockBodySize",
        "maxBlockExecutionUnits",
        "maxBlockHeaderSize",
        "maxCollateralInputs",
        "maxTxExecutionUnits",
        "maxTxSize",
        "maxValueSize",
        "minFeeRefScriptCostPerByte",
        "minPoolCost",
        "monetaryExpansion",
        "poolPledgeInfluence",
        "poolRetireMaxEpoch",
        "poolVotingThresholds",
        "protocolVersion",
        "stakeAddressDeposit",
        "stakePoolDeposit",
        "stakePoolTargetNum",
        "treasuryCut",
        "txFeeFixed",
        "txFeePerByte",
        "utxoCostPerByte",
    )
)


@common.SKIPIF_WRONG_ERA
class TestProtocol:
    """Basic tests for protocol."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_protocol_state_keys(self, cluster: clusterlib.ClusterLib):
        """Check output of `query protocol-state`."""
        temp_template = common.get_test_id(cluster)

        # The query dumps CBOR instead of JSON in some circumstances. We'll save the output
        # for later.
        protocol_state_raw = cluster.g_query.query_cli(["protocol-state"])

        with open(f"{temp_template}_protocol_state.out", "w", encoding="utf-8") as fp_out:
            fp_out.write(protocol_state_raw)

        try:
            protocol_state: dict = json.loads(protocol_state_raw)
        except json.decoder.JSONDecodeError:
            issues.node_3859.finish_test()
            raise

        assert set(protocol_state) == PROTOCOL_STATE_KEYS

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_protocol_state_outfile(self, cluster: clusterlib.ClusterLib):
        """Check output file produced by `query protocol-state`."""
        common.get_test_id(cluster)
        try:
            protocol_state: dict = json.loads(
                cluster.g_query.query_cli(["protocol-state", "--out-file", "/dev/stdout"])
            )
        except UnicodeDecodeError as err:
            if "invalid start byte" in str(err):
                issues.node_2461.finish_test()
            raise
        assert set(protocol_state) == PROTOCOL_STATE_KEYS

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_protocol_params(self, cluster: clusterlib.ClusterLib):
        """Check output of `query protocol-parameters`."""
        common.get_test_id(cluster)
        protocol_params = cluster.g_query.get_protocol_params()

        # The sets were updated for Conway, so there's nothing to add or remove at the moment.
        union_with: frozenset[str] = frozenset()
        rem: frozenset[str] = frozenset()

        assert set(protocol_params) == PROTOCOL_PARAM_KEYS.union(union_with).difference(rem)
