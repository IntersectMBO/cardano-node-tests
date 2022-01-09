"""Tests for update proposals."""
import logging
import time
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    p = Path(tmp_path_factory.getbasetemp()).joinpath(helpers.get_id_for_mktemp(__file__)).resolve()
    p.mkdir(exist_ok=True, parents=True)
    return p


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


@pytest.mark.order(3)
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
    def test_update_proposal(
        self,
        cluster_update_proposal: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
    ):
        """Test changing protocol parameters using update proposal.

        * if era >= Alonzo, update Alonzo-speciffic parameters and:

           - wait for next epoch
           - check that parameters were updated

        * submit update proposal
        * in the same epoch, submit another update proposal
        * wait for next epoch
        * check that parameters were updated with the values submitted in the second
          update proposal, i.e. the second update proposal overwritten the first one
        """
        cluster = cluster_update_proposal
        common.get_test_id(cluster)

        max_tx_execution_units = 11000000000
        max_block_execution_units = 110000000000
        price_execution_steps = "12/10"
        price_execution_memory = "1.3"

        cluster.wait_for_new_epoch()

        # update Alonzo-speciffic parameters in separate update proposal
        if VERSIONS.transaction_era >= VERSIONS.ALONZO:
            update_proposals_alonzo = [
                clusterlib_utils.UpdateProposal(
                    arg="--utxo-cost-per-word",
                    value=2,
                    name="utxoCostPerWord",
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
            ]

            clusterlib_utils.update_params_build(
                cluster_obj=cluster,
                src_addr_record=payment_addr,
                update_proposals=update_proposals_alonzo,
            )

            cluster.wait_for_new_epoch()

            protocol_params = cluster.get_protocol_params()
            clusterlib_utils.check_updated_params(
                update_proposals=update_proposals_alonzo, protocol_params=protocol_params
            )
            assert protocol_params["maxTxExecutionUnits"]["memory"] == max_tx_execution_units
            assert protocol_params["maxTxExecutionUnits"]["steps"] == max_tx_execution_units
            assert protocol_params["maxBlockExecutionUnits"]["memory"] == max_block_execution_units
            assert protocol_params["maxBlockExecutionUnits"]["steps"] == max_block_execution_units
            assert protocol_params["executionUnitPrices"]["priceSteps"] == 1.2
            assert protocol_params["executionUnitPrices"]["priceMemory"] == 1.3

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
                value=410000000,
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
                value=65555,
                name="maxBlockBodySize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-size",
                value=16400,
                name="maxTxSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-pool-cost",
                value=2,
                name="minPoolCost",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-header-size",
                value=1400,
                name="maxBlockHeaderSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-constant",
                value=155390,
                name="txFeeFixed",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--key-reg-deposit-amt",
                value=300050,
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
        update_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-linear",
                value=45,
                name="txFeePerByte",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-reg-deposit",
                value=400000000,
                name="stakePoolDeposit",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--decentralization-parameter",
                value=0.1,
                name="decentralization",
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
                value=65544,
                name="maxBlockBodySize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-size",
                value=16392,
                name="maxTxSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-pool-cost",
                value=1,
                name="minPoolCost",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-header-size",
                value=1200,
                name="maxBlockHeaderSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-constant",
                value=155380,
                name="txFeeFixed",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--key-reg-deposit-amt",
                value=300000,
                name="stakeAddressDeposit",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-influence",
                value=0.4,
                name="poolPledgeInfluence",
            ),
        ]
        if VERSIONS.cluster_era < VERSIONS.ALONZO:
            update_proposals.append(
                clusterlib_utils.UpdateProposal(
                    arg="--min-utxo-value",
                    value=2,
                    name="minUTxOValue",
                )
            )

        clusterlib_utils.update_params(
            cluster_obj=cluster,
            src_addr_record=payment_addr,
            update_proposals=update_proposals,
        )

        cluster.wait_for_new_epoch()

        clusterlib_utils.check_updated_params(
            update_proposals=update_proposals, protocol_params=cluster.get_protocol_params()
        )
