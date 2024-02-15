"""Tests for Conway governance protocol parameters update."""
# pylint: disable=expression-not-assigned
import logging
import random

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import blockers
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import requirements
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)

# Security related pparams that require also SPO approval
SECURITY_PPARAMS = {
    "maxBlockBodySize",
    "maxTxSize",
    "maxBlockHeaderSize",
    "maxValSize",
    "maxBlockExUnits",
    "minFeeA",
    "minFeeB",
    "coinsPerUTxOByte",
    "govActionDeposit",
    "minFeeRefScriptsCoinsPerByte",  # not in 8.8 release yet
}


@pytest.fixture
def pool_user_lg(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_governance: governance_setup.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "lock governance"."""
    cluster, __ = cluster_lock_governance
    key = helpers.get_current_line_str()
    return conway_common.get_pool_user(
        cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key
    )


class TestPParamUpdate:
    """Tests for protocol parameters update."""

    @allure.link(helpers.get_vcs_link())
    def test_pparam_update(  # noqa: C901
        self,
        cluster_lock_governance: governance_setup.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test enactment of protocol parameter update.

        * submit a "protocol parameters update" action
        * check that SPOs cannot vote on a "protocol parameters update" action
        * vote to approve the action
        * check that the action is ratified
        * check that the action is enacted
        * check that it's not possible to vote on enacted action
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Linked user stories
        req_cli17 = requirements.Req(id="CLI017", group=requirements.GroupsKnown.CHANG_US)
        req_cip6 = requirements.Req(id="CIP006", group=requirements.GroupsKnown.CHANG_US)

        # Create an action

        deposit_amt = cluster.conway_genesis["govActionDeposit"]

        anchor_url = "http://www.pparam-action.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.PPARAM_UPDATE,
            gov_state=cluster.g_conway_governance.query.gov_state(),
        )

        # Picked parameters and values that can stay changed even for other tests
        update_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--committee-term-length",
                value=random.randint(11000, 12000),
                name="committeeMaxTermLength",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-activity",
                value=random.randint(101, 120),
                name="dRepActivity",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-header-size",
                value=random.randint(1100, 1150),
                name="maxBlockHeaderSize",
            ),
        ]
        if configuration.HAS_CC:
            update_proposals.append(
                clusterlib_utils.UpdateProposal(
                    arg="--min-committee-size",
                    value=random.randint(3, 5),
                    name="committeeMinSize",
                )
            )
        update_args = clusterlib_utils.get_pparams_update_args(update_proposals=update_proposals)

        _url = helpers.get_vcs_link()
        req_cli17.start(url=_url)
        if configuration.HAS_CC:
            req_cip6.start(url=_url)
        pparams_action = cluster.g_conway_governance.action.create_pparams_update(
            action_name=temp_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            cli_args=update_args,
            prev_action_txid=prev_action_rec.txid,
            prev_action_ix=prev_action_rec.ix,
            deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
        )

        tx_files_action = clusterlib.TxFiles(
            proposal_files=[pparams_action.action_file],
            signing_key_files=[pool_user_lg.payment.skey_file],
        )

        # Make sure we have enough time to submit the proposal in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action",
            src_address=pool_user_lg.payment.address,
            use_build_cmd=True,
            tx_files=tx_files_action,
        )

        out_utxos_action = cluster.g_query.get_utxo(tx_raw_output=tx_output_action)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos_action, address=pool_user_lg.payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output_action.txins)
            - tx_output_action.fee
            - deposit_amt
        ), f"Incorrect balance for source address `{pool_user_lg.payment.address}`"

        action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
        action_gov_state = cluster.g_conway_governance.query.gov_state()
        _cur_epoch = cluster.g_query.get_epoch()
        conway_common.save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{_cur_epoch}"
        )
        prop_action = governance_utils.lookup_proposal(
            gov_state=action_gov_state, action_txid=action_txid
        )
        assert prop_action, "Param update action not found"
        assert (
            prop_action["action"]["tag"] == governance_utils.ActionTags.PARAMETER_CHANGE.value
        ), "Incorrect action tag"
        req_cli17.success()

        action_ix = prop_action["actionId"]["govActionIx"]

        proposal_names = {p.name for p in update_proposals}
        add_spo_votes = True

        if proposal_names.isdisjoint(SECURITY_PPARAMS):
            # Check that SPOs cannot vote on change of constitution action
            with pytest.raises(clusterlib.CLIError) as excinfo:
                conway_common.cast_vote(
                    cluster_obj=cluster,
                    governance_data=governance_data,
                    name_template=f"{temp_template}_with_spos",
                    payment_addr=pool_user_lg.payment,
                    action_txid=action_txid,
                    action_ix=action_ix,
                    approve_cc=False,
                    approve_drep=False,
                    approve_spo=True,
                )
            err_str = str(excinfo.value)
            assert "StakePoolVoter" in err_str, err_str

            add_spo_votes = False

        # Vote & disapprove the action
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_no",
            payment_addr=pool_user_lg.payment,
            action_txid=action_txid,
            action_ix=action_ix,
            approve_cc=False,
            approve_drep=False,
            approve_spo=False if add_spo_votes else None,
        )

        # Vote & approve the action
        voted_votes = conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_yes",
            payment_addr=pool_user_lg.payment,
            action_txid=action_txid,
            action_ix=action_ix,
            approve_cc=True,
            approve_drep=True,
            approve_spo=True if add_spo_votes else None,
        )

        def _check_state(state: dict):
            pparams = state["curPParams"]
            clusterlib_utils.check_updated_params(
                update_proposals=update_proposals, protocol_params=pparams
            )

        # Check ratification
        xfail_ledger_3979_msgs = set()
        for __ in range(3):
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            rat_gov_state = cluster.g_conway_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}"
            )
            rat_action = governance_utils.lookup_ratified_actions(
                gov_state=rat_gov_state, action_txid=action_txid
            )
            if rat_action:
                break

            # Known ledger issue where only one expired action gets removed in one epoch.
            # See https://github.com/IntersectMBO/cardano-ledger/issues/3979
            if not rat_action and conway_common.possible_rem_issue(
                gov_state=rat_gov_state, epoch=_cur_epoch
            ):
                xfail_ledger_3979_msgs.add("Only single expired action got removed")
                continue

            raise AssertionError("Action not found in ratified actions")

        next_rat_state = rat_gov_state["nextRatifyState"]
        _check_state(next_rat_state["nextEnactState"])
        assert not next_rat_state["ratificationDelayed"], "Ratification is delayed unexpectedly"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
        )
        _check_state(enact_gov_state["enactState"])
        if configuration.HAS_CC:
            req_cip6.success()

        # Try to vote on enacted action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_enacted",
                payment_addr=pool_user_lg.payment,
                action_txid=action_txid,
                action_ix=action_ix,
                approve_cc=False,
                approve_drep=False,
            )
        err_str = str(excinfo.value)
        assert "(GovActionsDoNotExist" in err_str, err_str

        # Check vote view
        if voted_votes.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.drep[0])

        if xfail_ledger_3979_msgs:
            blockers.GH(
                issue=3979,
                repo="IntersectMBO/cardano-ledger",
                message="; ".join(xfail_ledger_3979_msgs),
                check_on_devel=False,
            ).finish_test()
