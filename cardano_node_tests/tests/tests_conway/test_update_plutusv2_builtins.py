"""Tests for updating PlutusV2 built-ins in Conway."""

import logging
import pathlib as pl

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.tests.tests_plutus_v2 import mint_raw
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent.parent / "data"

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


@pytest.fixture
def pool_user_lgp(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_governance_plutus: governance_utils.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "lock governance"."""
    cluster, __ = cluster_lock_governance_plutus
    key = helpers.get_current_line_str()
    name_template = common.get_test_id(cluster)
    return common.get_registered_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        caching_key=key,
    )


@pytest.fixture
def payment_addrs_lgp(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_governance_plutus: governance_utils.GovClusterT,
) -> list[clusterlib.AddressRecord]:
    """Create new payment address."""
    cluster, __ = cluster_lock_governance_plutus
    addrs = common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=2,
        fund_idx=[0],
        amount=3_000_000_000,
    )
    return addrs


class TestUpdateBuiltIns:
    """Tests for updating PlutusV2 built-ins."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not configuration.HAS_CC, reason="Runs only on setup with CC")
    @pytest.mark.long
    @pytest.mark.upgrade_step1
    def test_update_in_pv9(
        self,
        # The test is changing protocol parameters, so it is not safe to run Plutus tests at that
        # time. It could e.g. lead to `PPViewHashesDontMatch` errors on transaction submits.
        cluster_lock_governance_plutus: governance_utils.GovClusterT,
        payment_addrs_lgp: list[clusterlib.AddressRecord],
        pool_user_lgp: clusterlib.PoolUser,
    ):
        """Test updating PlutusV2 cost model in PV9.

        Checks behavior with PlutusV2 script that uses built-ins added from PlutusV3.
        So far the new built-ins are enabled only in PV10, and are expected to fail in PV9.

        * check that Plutus script fails as expected in PV9
        * update the PlutusV2 cost model
        * check again that the Plutus script fails as expected in PV9
        """
        cluster, governance_data = cluster_lock_governance_plutus
        temp_template = common.get_test_id(cluster)

        if not conway_common.is_in_bootstrap(cluster_obj=cluster):
            pytest.skip("Can run only during bootstrap period.")

        init_cost_model = cluster.g_query.get_protocol_params()["costModels"]["PlutusV2"]
        if len(init_cost_model) >= 185:
            pytest.skip("PlutusV2 cost model was already updated.")

        cost_proposal_file = DATA_DIR / "cost_models_list_185_v2_v3.json"

        def _update_cost_model() -> None:
            anchor_data = governance_utils.get_default_anchor_data()
            _name_template = f"{temp_template}_cost_model"

            update_proposals = [
                clusterlib_utils.UpdateProposal(
                    arg="--cost-model-file",
                    value=str(cost_proposal_file),
                    name="",  # costModels
                )
            ]

            cost_model_proposal = conway_common.propose_pparams_update(
                cluster_obj=cluster,
                name_template=_name_template,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                pool_user=pool_user_lgp,
                proposals=update_proposals,
            )

            # Make sure we have enough time to submit the votes in one epoch
            clusterlib_utils.wait_for_epoch_interval(cluster_obj=cluster, start=1, stop=-40)
            prop_epoch = cluster.g_query.get_epoch()

            # Vote & approve the action
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{_name_template}_yes",
                payment_addr=pool_user_lgp.payment,
                action_txid=cost_model_proposal.action_txid,
                action_ix=cost_model_proposal.action_ix,
                approve_cc=True,
            )

            assert cluster.g_query.get_epoch() == prop_epoch, (
                "Epoch changed and it would affect other checks"
            )

            # Wait for ratification
            rat_epoch = cluster.wait_for_epoch(epoch_no=prop_epoch + 1, padding_seconds=5)
            rat_gov_state = cluster.g_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=rat_gov_state, name_template=f"{_name_template}_{rat_epoch}"
            )
            rat_action = governance_utils.lookup_ratified_actions(
                gov_state=rat_gov_state,
                action_txid=cost_model_proposal.action_txid,
                action_ix=cost_model_proposal.action_ix,
            )
            assert rat_action, "Action not found in ratified actions"

            # Wait for enactment
            enact_epoch = cluster.wait_for_epoch(epoch_no=prop_epoch + 2, padding_seconds=5)
            enact_gov_state = cluster.g_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{enact_epoch}"
            )
            pparams = enact_gov_state.get("currentPParams") or {}
            assert len(pparams["costModels"]["PlutusV2"]) == 185

        # Check that Plutus script fails as expected in PV9
        mint_raw.check_missing_builtin(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addrs_lgp[0],
            issuer_addr=payment_addrs_lgp[1],
        )

        # Update the PlutusV2 cost model
        _update_cost_model()

        # Check again that the Plutus script fails as expected in PV9
        mint_raw.check_missing_builtin(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addrs_lgp[0],
            issuer_addr=payment_addrs_lgp[1],
        )
