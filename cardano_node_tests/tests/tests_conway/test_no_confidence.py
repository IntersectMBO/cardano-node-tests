"""Tests for Conway governance state of no confidence."""

# pylint: disable=expression-not-assigned
import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import blockers
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


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


class TestNoConfidence:
    """Tests for state of no confidence."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not configuration.HAS_CC, reason="Runs only on setup with CC")
    @pytest.mark.long
    def test_no_confidence_action(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_governance: governance_setup.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test enactment of no confidence action.

        * create a "no confidence" action
        * vote to disapprove the action
        * vote to approve the action
        * check that CC members votes have no effect
        * check that the action is ratified
        * try to disapprove the ratified action, this shouldn't have any effect
        * check that the action is enacted
        * check that it's not possible to vote on enacted action
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Reinstate CC members first, if needed, so we have a previous action
        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.COMMITTEE,
            gov_state=cluster.g_conway_governance.query.gov_state(),
        )
        if not prev_action_rec.txid:
            governance_setup.reinstate_committee(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_reinstate1",
                pool_user=pool_user_lg,
            )
            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.COMMITTEE,
                gov_state=cluster.g_conway_governance.query.gov_state(),
            )
        assert prev_action_rec.txid, "No previous action found, it is needed for 'no confidence'"

        init_return_account_balance = cluster.g_query.get_stake_addr_info(
            pool_user_lg.stake.address
        ).reward_account_balance

        # Create an action
        deposit_amt = cluster.conway_genesis["govActionDeposit"]
        anchor_url = "http://www.cc-no-confidence.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        _url = helpers.get_vcs_link()
        [
            r.start(url=_url)
            for r in (
                reqc.cli018,
                reqc.cip013,
                reqc.cip029,
                reqc.cip030en,
                reqc.cip031a_04,
                reqc.cip054_04,
            )
        ]
        no_confidence_action = cluster.g_conway_governance.action.create_no_confidence(
            action_name=temp_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            prev_action_txid=prev_action_rec.txid,
            prev_action_ix=prev_action_rec.ix,
            deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
        )
        [r.success() for r in (reqc.cli018, reqc.cip031a_04, reqc.cip054_04)]

        tx_files_action = clusterlib.TxFiles(
            proposal_files=[no_confidence_action.action_file],
            signing_key_files=[
                pool_user_lg.payment.skey_file,
            ],
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
        assert prop_action, "No confidence action not found"
        assert (
            prop_action["proposalProcedure"]["govAction"]["tag"]
            == governance_utils.ActionTags.NO_CONFIDENCE.value
        ), "Incorrect action tag"

        reqc.cip029.success()

        action_ix = prop_action["actionId"]["govActionIx"]

        reqc.cip069en.start(url=helpers.get_vcs_link())

        # Vote & disapprove the action
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_no",
            payment_addr=pool_user_lg.payment,
            action_txid=action_txid,
            action_ix=action_ix,
            approve_drep=False,
            approve_spo=False,
        )

        # Vote & approve the action
        reqc.cip039.start(url=helpers.get_vcs_link())
        voted_votes = conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_yes",
            payment_addr=pool_user_lg.payment,
            action_txid=action_txid,
            action_ix=action_ix,
            approve_drep=True,
            approve_spo=True,
        )

        # Testnet will be in state of no confidence, respin is needed
        with cluster_manager.respin_on_failure():
            # Check that CC cannot vote on "no confidence" action
            with pytest.raises(clusterlib.CLIError) as excinfo:
                conway_common.cast_vote(
                    cluster_obj=cluster,
                    governance_data=governance_data,
                    name_template=f"{temp_template}_with_ccs",
                    payment_addr=pool_user_lg.payment,
                    action_txid=action_txid,
                    action_ix=action_ix,
                    approve_cc=False,
                    approve_drep=True,
                    approve_spo=True,
                )
            err_str = str(excinfo.value)
            assert "CommitteeVoter" in err_str, err_str

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

                msg = "Action not found in ratified actions"
                raise AssertionError(msg)

            # Disapprove ratified action, the voting shouldn't have any effect
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_after_ratification",
                payment_addr=pool_user_lg.payment,
                action_txid=action_txid,
                action_ix=action_ix,
                approve_drep=False,
                approve_spo=False,
            )

            reqc.cip038_03.start(url=helpers.get_vcs_link())
            assert rat_gov_state["nextRatifyState"][
                "ratificationDelayed"
            ], "Ratification not delayed"
            reqc.cip038_03.success()

            # Check enactment
            _url = helpers.get_vcs_link()
            [r.start(url=_url) for r in (reqc.cip032en, reqc.cip057)]
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            enact_gov_state = cluster.g_conway_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
            )
            assert not conway_common.get_committee_val(
                data=enact_gov_state
            ), "Committee is not empty"
            [r.success() for r in (reqc.cip013, reqc.cip039, reqc.cip057)]

            enact_prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.COMMITTEE,
                gov_state=enact_gov_state,
            )
            assert enact_prev_action_rec.txid == action_txid, "Incorrect previous action Txid"
            assert enact_prev_action_rec.ix == action_ix, "Incorrect previous action index"
            [r.success() for r in (reqc.cip032en, reqc.cip069en)]

            reqc.cip034en.start(url=helpers.get_vcs_link())
            enact_deposit_returned = cluster.g_query.get_stake_addr_info(
                pool_user_lg.stake.address
            ).reward_account_balance

            assert (
                enact_deposit_returned == init_return_account_balance + deposit_amt
            ), "Incorrect return account balance"
            [r.success() for r in (reqc.cip030en, reqc.cip034en)]

            # Try to vote on enacted action
            with pytest.raises(clusterlib.CLIError) as excinfo:
                conway_common.cast_vote(
                    cluster_obj=cluster,
                    governance_data=governance_data,
                    name_template=f"{temp_template}_enacted",
                    payment_addr=pool_user_lg.payment,
                    action_txid=action_txid,
                    action_ix=action_ix,
                    approve_drep=False,
                    approve_spo=False,
                )
            err_str = str(excinfo.value)
            assert "(GovActionsDoNotExist" in err_str, err_str

            reqc.cip041.start(url=helpers.get_vcs_link())
            governance_setup.reinstate_committee(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_reinstate2",
                pool_user=pool_user_lg,
            )
            reqc.cip041.success()

        # Check action view
        governance_utils.check_action_view(cluster_obj=cluster, action_data=no_confidence_action)

        # Check vote view
        if voted_votes.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.drep[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.spo[0])

        if xfail_ledger_3979_msgs:
            blockers.GH(
                issue=3979,
                repo="IntersectMBO/cardano-ledger",
                message="; ".join(xfail_ledger_3979_msgs),
                check_on_devel=False,
            ).finish_test()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not configuration.HAS_CC, reason="Runs only on setup with CC")
    @pytest.mark.long
    def test_committee_min_size(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_governance: governance_setup.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test that actions cannot be ratified when the number of CC members < committeeMinSize.

        Only update-committee and no-confidence governance actions can be ratified.

        * resign all CC Members but one
        * try to ratify a "create constitution" action
        * check that the action is not ratified
        * reinstate the original CC members
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Resign all CC Members but one

        reqc.cip014.start(url=helpers.get_vcs_link())

        cc_members_to_resign = governance_data.cc_members[1:]
        _cur_epoch = cluster.g_query.get_epoch()
        conway_common.resign_ccs(
            cluster_obj=cluster,
            name_template=f"{temp_template}_{_cur_epoch}",
            ccs_to_resign=cc_members_to_resign,
            payment_addr=pool_user_lg.payment,
        )

        # Testnet will be in (sort of) state of no confidence, respin is needed in case of
        # failure.
        with cluster_manager.respin_on_failure():
            # Try to ratify a "create constitution" action
            anchor_url = "http://www.const-action.com"
            constitution_url = "http://www.const-new.com"
            (
                __,
                const_action_txid,
                const_action_ix,
            ) = conway_common.propose_change_constitution(
                cluster_obj=cluster,
                name_template=f"{temp_template}_constitution",
                anchor_url=anchor_url,
                anchor_data_hash=cluster.g_conway_governance.get_anchor_data_hash(text=anchor_url),
                constitution_url=constitution_url,
                constitution_hash=cluster.g_conway_governance.get_anchor_data_hash(
                    text=constitution_url
                ),
                pool_user=pool_user_lg,
            )
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_constitution",
                payment_addr=pool_user_lg.payment,
                action_txid=const_action_txid,
                action_ix=const_action_ix,
                approve_cc=True,
                approve_drep=True,
            )

            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            approved_gov_state = cluster.g_conway_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=approved_gov_state, name_template=f"{temp_template}_approved_{_cur_epoch}"
            )
            prop_const_action = governance_utils.lookup_proposal(
                gov_state=approved_gov_state,
                action_txid=const_action_txid,
                action_ix=const_action_ix,
            )

            # Check that the action is not ratified
            rat_const_action = governance_utils.lookup_ratified_actions(
                gov_state=approved_gov_state,
                action_txid=const_action_txid,
                action_ix=const_action_ix,
            )
            if rat_const_action:
                blockers.GH(
                    issue=4204,
                    repo="IntersectMBO/cardano-ledger",
                    message="Resigned CC members can approve actions",
                    check_on_devel=False,
                ).finish_test()
            assert not rat_const_action, "Action found in ratified actions"

            # Check that the action expired
            expire_epoch = prop_const_action["expiresAfter"] + 1
            _cur_epoch = cluster.wait_for_new_epoch(
                new_epochs=expire_epoch - _cur_epoch, padding_seconds=5
            )
            expire_gov_state = cluster.g_conway_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=expire_gov_state, name_template=f"{temp_template}_expire_{_cur_epoch}"
            )
            exp_const_action = governance_utils.lookup_expired_actions(
                gov_state=expire_gov_state, action_txid=const_action_txid, action_ix=const_action_ix
            )
            assert exp_const_action, "Action NOT found in expired actions"

            reqc.cip014.success()

            # Reinstate the original CC members
            governance_data = governance_setup.refresh_cc_keys(
                cluster_obj=cluster,
                cc_members=cc_members_to_resign,
                governance_data=governance_data,
            )
            governance_setup.reinstate_committee(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_reinstate",
                pool_user=pool_user_lg,
            )
