"""Tests for node upgrade."""
import json
import logging
import os
from pathlib import Path
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import artifacts
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles

LOGGER = logging.getLogger(__name__)

ALONZO_GENESIS_SPEC = (
    Path(__file__).parent.parent / "cluster_scripts" / "babbage" / "genesis.alonzo.spec.json"
)

UPGRADE_TESTS_STEP = int(os.environ.get("UPGRADE_TESTS_STEP") or 0)
BASE_REVISION = os.environ.get("BASE_REVISION")
UPGRADE_REVISION = os.environ.get("UPGRADE_REVISION")

pytestmark = [
    pytest.mark.skipif(not UPGRADE_TESTS_STEP, reason="not upgrade testing"),
]


@pytest.fixture
def cluster_locked(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    return cluster_manager.get(lock_resources=[cluster_management.Resources.CLUSTER])


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster_locked: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    cluster = cluster_locked
    temp_template = common.get_test_id(cluster)

    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_payment_addr_0",
            f"{temp_template}_payment_addr_1",
            cluster_obj=cluster,
        )
        fixture_cache.value = addrs

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )

    return addrs


class TestUpgrade:
    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP != 2, reason="runs only on step 2 of upgrade testing")
    def test_ignore_log_errors(
        self,
        cluster_locked: clusterlib.ClusterLib,
        worker_id: str,
    ):
        """Ignore selected errors in log right after node upgrade."""
        cluster = cluster_locked
        common.get_test_id(cluster)

        # Ignore ledger replay when upgrading from node version 1.34.1.
        # The error message appears only right after the node is upgraded. This ignore rule has
        # effect only in this test.
        if BASE_REVISION == "1.34.1":
            logfiles.add_ignore_rule(
                files_glob="*.stdout",
                regex="ChainDB:Error:.* Invalid snapshot DiskSnapshot .*DeserialiseFailure 168",
                ignore_file_id=worker_id,
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP != 3, reason="runs only on step 3 of upgrade testing")
    def test_update_to_babbage(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_locked: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Update cluster to Babbage era."""
        cluster = cluster_locked
        temp_template = common.get_test_id(cluster)
        src_addr = payment_addrs[0]

        cluster.wait_for_new_epoch()

        # update to Babbage

        update_proposal_babbage = [
            clusterlib_utils.UpdateProposal(
                arg="--protocol-major-version",
                value=7,
                name="",  # needs custom check
            ),
            clusterlib_utils.UpdateProposal(
                arg="--protocol-minor-version",
                value=0,
                name="",  # needs custom check
            ),
        ]

        clusterlib_utils.update_params(
            cluster_obj=cluster,
            src_addr_record=src_addr,
            update_proposals=update_proposal_babbage,
        )

        cluster.wait_for_new_epoch(padding_seconds=3)

        protocol_params = cluster.get_protocol_params()
        assert protocol_params["protocolVersion"]["major"] == 7
        assert protocol_params["protocolVersion"]["minor"] == 0

        # update cluster instance - we need to use Babbage-era Tx for now on

        artifacts.save_cli_coverage(
            cluster_obj=cluster, pytest_config=cluster_manager.pytest_config
        )

        configuration.CLUSTER_ERA = "babbage"
        configuration.TX_ERA = "babbage"

        cluster = cluster_nodes.get_cluster_type().get_cluster_obj(tx_era=configuration.TX_ERA)
        cluster_manager.cache.cluster_obj = cluster

        # update cost model

        with open(ALONZO_GENESIS_SPEC, encoding="utf-8") as genesis_fp:
            alonzo_genesis_spec = json.load(genesis_fp)
        cost_models = alonzo_genesis_spec["costModels"]

        cost_models_file = Path(f"{temp_template}_cost_values.json").resolve()
        with open(cost_models_file, "w", encoding="utf-8") as out_fp:
            out_fp.write(json.dumps(cost_models, indent=4))

        update_proposal_cost_model = [
            clusterlib_utils.UpdateProposal(
                arg="--cost-model-file",
                value=str(cost_models_file),
                name="",  # needs custom check
            ),
        ]

        clusterlib_utils.update_params(
            cluster_obj=cluster,
            src_addr_record=src_addr,
            update_proposals=update_proposal_cost_model,
        )

        cluster.wait_for_new_epoch(padding_seconds=3)

        protocol_params = cluster.get_protocol_params()
        assert protocol_params["costModels"]["PlutusScriptV2"]["bData-memory-arguments"] == 32
