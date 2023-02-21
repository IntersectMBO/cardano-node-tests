"""Tests for protocol state and protocol parameters."""
import json
import logging
from typing import FrozenSet

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
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
)
PROTOCOL_PARAM_KEYS_1_35_2 = frozenset(("utxoCostPerByte",))


@common.SKIPIF_WRONG_ERA
@pytest.mark.testnets
@pytest.mark.smoke
class TestProtocol:
    """Basic tests for protocol."""

    @allure.link(helpers.get_vcs_link())
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
        except json.decoder.JSONDecodeError as err:
            pytest.xfail(f"expected JSON, got CBOR - see node issue #3859: {err}")

        assert set(protocol_state) == PROTOCOL_STATE_KEYS

    @allure.link(helpers.get_vcs_link())
    def test_protocol_state_outfile(self, cluster: clusterlib.ClusterLib):
        """Check output file produced by `query protocol-state`."""
        common.get_test_id(cluster)
        try:
            protocol_state: dict = json.loads(
                cluster.g_query.query_cli(["protocol-state", "--out-file", "/dev/stdout"])
            )
        except UnicodeDecodeError as err:
            if "invalid start byte" in str(err):
                pytest.xfail(
                    "`query protocol-state --out-file` dumps binary data - cardano-node issue #2461"
                )
            raise
        assert set(protocol_state) == PROTOCOL_STATE_KEYS

    @allure.link(helpers.get_vcs_link())
    def test_protocol_params(self, cluster: clusterlib.ClusterLib):
        """Check output of `query protocol-parameters`."""
        common.get_test_id(cluster)
        protocol_params = cluster.g_query.get_protocol_params()

        union_with: FrozenSet[str] = frozenset()
        if clusterlib_utils.cli_has("governance create-update-proposal --utxo-cost-per-byte"):
            union_with = PROTOCOL_PARAM_KEYS_1_35_2

        assert set(protocol_params) == PROTOCOL_PARAM_KEYS.union(union_with)
