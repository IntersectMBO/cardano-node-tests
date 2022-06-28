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


PROTOCOL_STATE_KEYS_ALONZO = frozenset(("chainDepState", "lastSlot"))
PROTOCOL_STATE_KEYS_ALONZO_DEP_STATE = frozenset(("csLabNonce", "csProtocol", "csTickn"))
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


@pytest.mark.skipif(not common.SAME_ERAS, reason=common.ERAS_SKIP_MSG)
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
        protocol_state_raw = cluster.query_cli(["protocol-state"])
        with open(f"{temp_template}_protocol_state.out", "w", encoding="utf-8") as fp_out:
            fp_out.write(protocol_state_raw)

        # TODO: the query is broken on 1.35.0-rc4
        try:
            protocol_state: dict = json.loads(protocol_state_raw)
        except clusterlib.CLIError as err:
            if "currentlyBroken" in str(err):
                pytest.xfail(
                    "`query protocol-state` is currently broken - cardano-node issue #3883"
                )
            raise

        protocol_state_keys = set(protocol_state)

        if VERSIONS.cluster_era == VERSIONS.ALONZO:
            # node v1.35.x+
            if "chainDepState" in protocol_state_keys:
                assert protocol_state_keys == PROTOCOL_STATE_KEYS_ALONZO
                assert set(protocol_state["chainDepState"]) == PROTOCOL_STATE_KEYS_ALONZO_DEP_STATE
            # node < v1.35.x
            else:
                assert protocol_state_keys == PROTOCOL_STATE_KEYS_ALONZO_DEP_STATE
        elif VERSIONS.cluster_era > VERSIONS.ALONZO:
            assert protocol_state_keys == PROTOCOL_STATE_KEYS

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
        assert set(protocol_params) == PROTOCOL_PARAM_KEYS
