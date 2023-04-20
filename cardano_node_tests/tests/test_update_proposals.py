"""Tests for update proposals."""
import json
import logging
import time
from pathlib import Path

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"


@pytest.mark.order(8)
@pytest.mark.skipif(
    VERSIONS.cluster_era != VERSIONS.transaction_era,
    reason="must run with same cluster and Tx era",
)
@pytest.mark.long
class TestUpdateProposals:
    """Tests for update proposals."""

    @pytest.fixture
    def cluster_update_proposal(
        self,
        cluster_manager: cluster_management.ClusterManager,
    ) -> clusterlib.ClusterLib:
        return cluster_manager.get(
            lock_resources=[cluster_management.Resources.CLUSTER], cleanup=True
        )

    @pytest.fixture
    def payment_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_update_proposal: clusterlib.ClusterLib,
    ) -> clusterlib.AddressRecord:
        """Create new payment address."""
        cluster = cluster_update_proposal

        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addr = clusterlib_utils.create_payment_addr_records(
                f"addr_test_update_proposal_ci{cluster_manager.cluster_instance_num}_0",
                cluster_obj=cluster,
            )[0]
            fixture_cache.value = addr

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addr,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return addr

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_update_proposal(
        self,
        cluster_update_proposal: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
    ):
        """Test changing protocol parameters using update proposal.

        * update parameters specific for Alonzo+ eras, and:

           - wait for next epoch
           - check that parameters were updated

        * submit update proposal
        * in the same epoch, submit another update proposal
        * wait for next epoch
        * check that parameters were updated with the values submitted in the second
          update proposal, i.e. the second update proposal overwritten the first one
        """
        # pylint: disable=too-many-statements
        cluster = cluster_update_proposal
        temp_template = common.get_test_id(cluster)

        state_dir = cluster_nodes.get_cluster_env().state_dir
        has_conway = (state_dir / "shelley" / "genesis.conway.json").exists()

        max_tx_execution_units = 11_000_000_000
        max_block_execution_units = 110_000_000_000
        price_execution_steps = "12/10"
        price_execution_memory = "1.3"

        this_epoch = cluster.wait_for_new_epoch()

        protocol_params = cluster.g_query.get_protocol_params()
        with open(f"{temp_template}_pparams_ep{this_epoch}.json", "w", encoding="utf-8") as fp_out:
            json.dump(protocol_params, fp_out, indent=4)

        # update Alonzo+ specific parameters in separate update proposal

        # TODO: On node >= 1.36.0 the cost models are lists. On older versions they are dicts.
        cost_proposal_file = (
            DATA_DIR / "cost_models_list.json" if has_conway else DATA_DIR / "cost_models_dict.json"
        )

        update_proposals_babbage = [
            clusterlib_utils.UpdateProposal(
                arg="--utxo-cost-per-byte",
                value=4300,
                name="utxoCostPerByte",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-value-size",
                value=5000,
                name="maxValueSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--collateral-percent",
                value=90,
                name="collateralPercentage",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-collateral-inputs",
                value=4,
                name="maxCollateralInputs",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-execution-units",
                value=f"({max_tx_execution_units},{max_tx_execution_units})",
                name="",  # needs custom check
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-execution-units",
                value=f"({max_block_execution_units},{max_block_execution_units})",
                name="",  # needs custom check
            ),
            clusterlib_utils.UpdateProposal(
                arg="--price-execution-steps",
                value=price_execution_steps,
                name="",  # needs custom check
            ),
            clusterlib_utils.UpdateProposal(
                arg="--price-execution-memory",
                value=price_execution_memory,
                name="",  # needs custom check
            ),
            clusterlib_utils.UpdateProposal(
                arg="--cost-model-file",
                value=str(cost_proposal_file),
                name="",  # needs custom check
            ),
        ]

        clusterlib_utils.update_params_build(
            cluster_obj=cluster,
            src_addr_record=payment_addr,
            update_proposals=update_proposals_babbage,
        )

        this_epoch = cluster.wait_for_new_epoch()

        protocol_params = cluster.g_query.get_protocol_params()
        with open(f"{temp_template}_pparams_ep{this_epoch}.json", "w", encoding="utf-8") as fp_out:
            json.dump(protocol_params, fp_out, indent=4)

        clusterlib_utils.check_updated_params(
            update_proposals=update_proposals_babbage, protocol_params=protocol_params
        )
        assert protocol_params["maxTxExecutionUnits"]["memory"] == max_tx_execution_units
        assert protocol_params["maxTxExecutionUnits"]["steps"] == max_tx_execution_units
        assert protocol_params["maxBlockExecutionUnits"]["memory"] == max_block_execution_units
        assert protocol_params["maxBlockExecutionUnits"]["steps"] == max_block_execution_units
        assert protocol_params["executionUnitPrices"]["priceSteps"] == 1.2
        assert protocol_params["executionUnitPrices"]["priceMemory"] == 1.3

        cost_model_v1 = protocol_params["costModels"]["PlutusV1"]
        cost_model_v2 = protocol_params["costModels"]["PlutusV2"]
        if has_conway:
            with open(cost_proposal_file, encoding="utf-8") as fp_in:
                cost_model_prop_content = json.load(fp_in)

            assert cost_model_v1 == cost_model_prop_content["PlutusV1"]
            assert cost_model_v2 == cost_model_prop_content["PlutusV2"]
        else:
            # check only selected expected value as some key names don't necessarily match
            assert cost_model_v1["verifyEd25519Signature-memory-arguments"] == 11
            assert cost_model_v2["verifyEd25519Signature-memory-arguments"] == 11

        # check param proposal on dbsync
        dbsync_utils.check_param_proposal(protocol_params=protocol_params)

        # Check that only one update proposal can be applied each epoch and that the last
        # update proposal cancels the previous one. Following parameter values will be
        # overwritten by the next update proposal.
        update_proposal_canceled = [
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-linear",
                value=47,
                name="txFeePerByte",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-reg-deposit",
                value=410_000_000,
                name="stakePoolDeposit",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--decentralization-parameter",
                value=0.2,
                name="decentralization",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-retirement-epoch-boundary",
                value=18,
                name="poolRetireMaxEpoch",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--number-of-pools",
                value=10,
                name="stakePoolTargetNum",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-body-size",
                value=65_555,
                name="maxBlockBodySize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-size",
                value=16_400,
                name="maxTxSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-pool-cost",
                value=2,
                name="minPoolCost",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-header-size",
                value=1_400,
                name="maxBlockHeaderSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-constant",
                value=155_390,
                name="txFeeFixed",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--key-reg-deposit-amt",
                value=300_050,
                name="stakeAddressDeposit",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-influence",
                value=0.5,
                name="poolPledgeInfluence",
            ),
        ]

        clusterlib_utils.update_params(
            cluster_obj=cluster,
            src_addr_record=payment_addr,
            update_proposals=update_proposal_canceled,
        )
        time.sleep(2)

        # the final update proposal
        decentralization = clusterlib_utils.UpdateProposal(
            arg="--decentralization-parameter",
            value=0.1,
            name="",  # needs custom check
        )
        update_proposals = [
            decentralization,
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-linear",
                value=45,
                name="txFeePerByte",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-reg-deposit",
                value=400_000_000,
                name="stakePoolDeposit",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-retirement-epoch-boundary",
                value=19,
                name="poolRetireMaxEpoch",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--number-of-pools",
                value=9,
                name="stakePoolTargetNum",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-body-size",
                value=65_544,
                name="maxBlockBodySize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-size",
                value=16_392,
                name="maxTxSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-pool-cost",
                value=1,
                name="minPoolCost",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-header-size",
                value=1_200,
                name="maxBlockHeaderSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-constant",
                value=155_380,
                name="txFeeFixed",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--key-reg-deposit-amt",
                value=300_000,
                name="stakeAddressDeposit",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-influence",
                value=0.4,
                name="poolPledgeInfluence",
            ),
        ]

        clusterlib_utils.update_params(
            cluster_obj=cluster,
            src_addr_record=payment_addr,
            update_proposals=update_proposals,
        )

        this_epoch = cluster.wait_for_new_epoch()

        protocol_params = cluster.g_query.get_protocol_params()
        with open(f"{temp_template}_pparams_ep{this_epoch}.json", "w", encoding="utf-8") as fp_out:
            json.dump(protocol_params, fp_out, indent=4)

        clusterlib_utils.check_updated_params(
            update_proposals=update_proposals, protocol_params=protocol_params
        )

        # check param proposal on dbsync
        dbsync_utils.check_param_proposal(protocol_params=protocol_params)

        assert protocol_params["decentralization"] is None
