"""Tests for Conway governance voting functionality."""
# pylint: disable=expression-not-assigned
import logging
import random
import typing as tp

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
from cardano_node_tests.utils import submit_utils
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


@pytest.fixture
def pool_user_ug(
    cluster_manager: cluster_management.ClusterManager,
    cluster_use_governance: governance_setup.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "use governance"."""
    cluster, __ = cluster_use_governance
    key = helpers.get_current_line_str()
    return conway_common.get_pool_user(
        cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key
    )


class TestEnactment:
    """Tests for actions enactment."""

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

        def _cast_vote(
            approve: bool, vote_id: str, add_spo_votes: bool = False
        ) -> conway_common.VotedVotes:
            _votes_cc = [
                None  # This CC member doesn't vote, his votes count as "No"
                if i % 3 == 0
                else cluster.g_conway_governance.vote.create_committee(
                    vote_name=f"{temp_template}_{vote_id}_cc{i}",
                    action_txid=action_txid,
                    action_ix=action_ix,
                    vote=conway_common.get_yes_abstain_vote(i) if approve else clusterlib.Votes.NO,
                    cc_hot_vkey_file=m.hot_vkey_file,
                )
                for i, m in enumerate(governance_data.cc_members, start=1)
            ]
            votes_cc = [r for r in _votes_cc if r]
            votes_drep = [
                cluster.g_conway_governance.vote.create_drep(
                    vote_name=f"{temp_template}_{vote_id}_drep{i}",
                    action_txid=action_txid,
                    action_ix=action_ix,
                    vote=clusterlib.Votes.YES if approve else clusterlib.Votes.NO,
                    drep_vkey_file=d.key_pair.vkey_file,
                )
                for i, d in enumerate(governance_data.dreps_reg, start=1)
            ]

            votes_spo = []
            if add_spo_votes:
                votes_spo = [
                    cluster.g_conway_governance.vote.create_spo(
                        vote_name=f"{temp_template}_{vote_id}_pool{i}",
                        action_txid=action_txid,
                        action_ix=action_ix,
                        vote=clusterlib.Votes.NO,
                        cold_vkey_file=p.vkey_file,
                    )
                    for i, p in enumerate(governance_data.pools_cold, start=1)
                ]

            spo_keys = [r.skey_file for r in governance_data.pools_cold] if votes_spo else []
            tx_files_vote = clusterlib.TxFiles(
                vote_files=[
                    *[r.vote_file for r in votes_cc],
                    *[r.vote_file for r in votes_drep],
                    *[r.vote_file for r in votes_spo],
                ],
                signing_key_files=[
                    pool_user_lg.payment.skey_file,
                    *[r.hot_skey_file for r in governance_data.cc_members],
                    *[r.key_pair.skey_file for r in governance_data.dreps_reg],
                    *spo_keys,
                ],
            )

            # Make sure we have enough time to submit the votes in one epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
            )

            tx_output_vote = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_vote_{vote_id}",
                src_address=pool_user_lg.payment.address,
                use_build_cmd=True,
                tx_files=tx_files_vote,
            )

            out_utxos_vote = cluster.g_query.get_utxo(tx_raw_output=tx_output_vote)
            assert (
                clusterlib.filter_utxos(utxos=out_utxos_vote, address=pool_user_lg.payment.address)[
                    0
                ].amount
                == clusterlib.calculate_utxos_balance(tx_output_vote.txins) - tx_output_vote.fee
            ), f"Incorrect balance for source address `{pool_user_lg.payment.address}`"

            vote_gov_state = cluster.g_conway_governance.query.gov_state()
            _cur_epoch = cluster.g_query.get_epoch()
            conway_common.save_gov_state(
                gov_state=vote_gov_state,
                name_template=f"{temp_template}_vote_{vote_id}_{_cur_epoch}",
            )
            prop_vote = governance_utils.lookup_proposal(
                gov_state=vote_gov_state, action_txid=action_txid
            )
            assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
            assert prop_vote["dRepVotes"], "No DRep votes"
            assert not prop_vote["stakePoolVotes"], "Unexpected stake pool votes"

            return conway_common.VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo)

        # Check that SPOs cannot vote on change of constitution action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _cast_vote(approve=False, vote_id="with_spos", add_spo_votes=True)
        err_str = str(excinfo.value)
        assert "StakePoolVoter" in err_str, err_str

        # Vote & disapprove the action
        _cast_vote(approve=False, vote_id="no")

        # Vote & approve the action
        voted_votes = _cast_vote(approve=True, vote_id="yes")

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
            rem_action = governance_utils.lookup_removed_actions(
                gov_state=rat_gov_state, action_txid=action_txid
            )
            if rem_action:
                break

            # Known ledger issue where only one expired action gets removed in one epoch.
            # See https://github.com/IntersectMBO/cardano-ledger/issues/3979
            if not rem_action and conway_common.possible_rem_issue(
                gov_state=rat_gov_state, epoch=_cur_epoch
            ):
                xfail_ledger_3979_msgs.add("Only single expired action got removed")
                continue

            raise AssertionError("Action not found in removed actions")

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
            _cast_vote(approve=False, vote_id="enacted")
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

    @allure.link(helpers.get_vcs_link())
    def test_treasury_withdrawals(  # noqa: C901
        self,
        cluster_use_governance: governance_setup.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test enactment of multiple treasury withdrawals in single epoch.

        Use `transaction build` for building the transactions.
        When available, use cardano-submit-api for votes submission.

        * submit multiple "treasury withdrawal" actions
        * check that SPOs cannot vote on a "treasury withdrawal" action
        * vote to approve the actions
        * check that the actions are ratified
        * check that the action are enacted
        * check that it's not possible to vote on enacted action
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_use_governance
        temp_template = common.get_test_id(cluster)
        actions_num = 3

        # Linked user stories
        req_cli15 = requirements.Req(id="CLI015", group=requirements.GroupsKnown.CHANG_US)

        # Create stake address and registration certificate
        stake_deposit_amt = cluster.g_query.get_address_deposit()

        recv_stake_addr_rec = cluster.g_stake_address.gen_stake_addr_and_keys(
            name=f"{temp_template}_receive"
        )
        recv_stake_addr_reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_receive",
            deposit_amt=stake_deposit_amt,
            stake_vkey_file=recv_stake_addr_rec.vkey_file,
        )

        # Create an action and register stake address

        action_deposit_amt = cluster.conway_genesis["govActionDeposit"]
        transfer_amt = 10_000_000_000

        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        req_cli15.start(url=helpers.get_vcs_link())
        withdrawal_actions = [
            cluster.g_conway_governance.action.create_treasury_withdrawal(
                action_name=f"{temp_template}_{a}",
                transfer_amt=transfer_amt,
                deposit_amt=action_deposit_amt,
                anchor_url=f"http://www.withdrawal-action{a}.com",
                anchor_data_hash=anchor_data_hash,
                funds_receiving_stake_vkey_file=recv_stake_addr_rec.vkey_file,
                deposit_return_stake_vkey_file=pool_user_ug.stake.vkey_file,
            )
            for a in range(actions_num)
        ]
        req_cli15.success()

        tx_files_action = clusterlib.TxFiles(
            certificate_files=[recv_stake_addr_reg_cert],
            proposal_files=[w.action_file for w in withdrawal_actions],
            signing_key_files=[pool_user_ug.payment.skey_file, recv_stake_addr_rec.skey_file],
        )

        # Make sure we have enough time to submit the proposals in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action",
            src_address=pool_user_ug.payment.address,
            submit_method=submit_utils.SubmitMethods.API
            if submit_utils.is_submit_api_available()
            else submit_utils.SubmitMethods.CLI,
            use_build_cmd=True,
            tx_files=tx_files_action,
        )

        assert cluster.g_query.get_stake_addr_info(
            recv_stake_addr_rec.address
        ).address, f"Stake address is not registered: {recv_stake_addr_rec.address}"

        actions_deposit_combined = action_deposit_amt * actions_num

        out_utxos_action = cluster.g_query.get_utxo(tx_raw_output=tx_output_action)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos_action, address=pool_user_ug.payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output_action.txins)
            - tx_output_action.fee
            - actions_deposit_combined
            - stake_deposit_amt
        ), f"Incorrect balance for source address `{pool_user_ug.payment.address}`"

        action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
        action_gov_state = cluster.g_conway_governance.query.gov_state()
        _cur_epoch = cluster.g_query.get_epoch()
        conway_common.save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{_cur_epoch}"
        )

        for action_ix in range(actions_num):
            prop_action = governance_utils.lookup_proposal(
                gov_state=action_gov_state, action_txid=action_txid, action_ix=action_ix
            )
            assert prop_action, "Treasury withdrawals action not found"
            assert (
                prop_action["action"]["tag"]
                == governance_utils.ActionTags.TREASURY_WITHDRAWALS.value
            ), "Incorrect action tag"

        def _cast_vote(
            approve: bool, vote_id: str, add_spo_votes: bool = False
        ) -> conway_common.VotedVotes:
            votes_cc = []
            votes_drep = []
            votes_spo = []
            for action_ix in range(actions_num):
                votes_cc.extend(
                    [
                        cluster.g_conway_governance.vote.create_committee(
                            vote_name=f"{temp_template}_{vote_id}_{action_ix}_cc{i}",
                            action_txid=action_txid,
                            action_ix=action_ix,
                            vote=clusterlib.Votes.YES if approve else clusterlib.Votes.NO,
                            cc_hot_vkey_file=m.hot_vkey_file,
                        )
                        for i, m in enumerate(governance_data.cc_members, start=1)
                    ]
                )
                votes_drep.extend(
                    [
                        cluster.g_conway_governance.vote.create_drep(
                            vote_name=f"{temp_template}_{vote_id}_{action_ix}_drep{i}",
                            action_txid=action_txid,
                            action_ix=action_ix,
                            vote=clusterlib.Votes.YES if approve else clusterlib.Votes.NO,
                            drep_vkey_file=d.key_pair.vkey_file,
                        )
                        for i, d in enumerate(governance_data.dreps_reg, start=1)
                    ]
                )
                if add_spo_votes:
                    votes_spo.extend(
                        [
                            cluster.g_conway_governance.vote.create_spo(
                                vote_name=f"{temp_template}_{vote_id}_{vote_id}_pool{i}",
                                action_txid=action_txid,
                                action_ix=action_ix,
                                vote=clusterlib.Votes.NO,
                                cold_vkey_file=p.vkey_file,
                            )
                            for i, p in enumerate(governance_data.pools_cold, start=1)
                        ]
                    )

            spo_keys = [r.skey_file for r in governance_data.pools_cold] if votes_spo else []
            tx_files_vote = clusterlib.TxFiles(
                vote_files=[
                    *[r.vote_file for r in votes_cc],
                    *[r.vote_file for r in votes_drep],
                    *[r.vote_file for r in votes_spo],
                ],
                signing_key_files=[
                    pool_user_ug.payment.skey_file,
                    *[r.hot_skey_file for r in governance_data.cc_members],
                    *[r.key_pair.skey_file for r in governance_data.dreps_reg],
                    *spo_keys,
                ],
            )

            # Make sure we have enough time to submit the votes in one epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
            )

            tx_output_vote = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_vote_{vote_id}",
                src_address=pool_user_ug.payment.address,
                use_build_cmd=True,
                tx_files=tx_files_vote,
            )

            out_utxos_vote = cluster.g_query.get_utxo(tx_raw_output=tx_output_vote)
            assert (
                clusterlib.filter_utxos(utxos=out_utxos_vote, address=pool_user_ug.payment.address)[
                    0
                ].amount
                == clusterlib.calculate_utxos_balance(tx_output_vote.txins) - tx_output_vote.fee
            ), f"Incorrect balance for source address `{pool_user_ug.payment.address}`"

            vote_gov_state = cluster.g_conway_governance.query.gov_state()
            _cur_epoch = cluster.g_query.get_epoch()
            conway_common.save_gov_state(
                gov_state=vote_gov_state,
                name_template=f"{temp_template}_vote_{vote_id}_{_cur_epoch}",
            )

            for action_ix in range(actions_num):
                prop_vote = governance_utils.lookup_proposal(
                    gov_state=vote_gov_state, action_txid=action_txid, action_ix=action_ix
                )
                assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
                assert prop_vote["dRepVotes"], "No DRep votes"
                assert not prop_vote["stakePoolVotes"], "Unexpected stake pool votes"

            return conway_common.VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo)

        # Check that SPOs cannot vote on change of constitution action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _cast_vote(approve=False, vote_id="with_spos", add_spo_votes=True)
        err_str = str(excinfo.value)
        assert "StakePoolVoter" in err_str, err_str

        # Vote & disapprove the action
        _cast_vote(approve=False, vote_id="no")

        # Vote & approve the action
        voted_votes = _cast_vote(approve=True, vote_id="yes")

        # Check ratification
        xfail_ledger_3979_msgs = set()
        removed_actions: tp.Set[int] = set()
        remaining_actions: tp.Set[int] = set(range(actions_num))
        for __ in range(4):
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            rat_gov_state = cluster.g_conway_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}"
            )
            for action_ix in remaining_actions:
                rem_action = governance_utils.lookup_removed_actions(
                    gov_state=rat_gov_state, action_txid=action_txid, action_ix=action_ix
                )
                if rem_action:
                    removed_actions.add(action_ix)
                    continue

                # Known ledger issue where only one expired action gets removed in one epoch.
                # See https://github.com/IntersectMBO/cardano-ledger/issues/3979
                if not rem_action and conway_common.possible_rem_issue(
                    gov_state=rat_gov_state, epoch=_cur_epoch
                ):
                    xfail_ledger_3979_msgs.add("Only single expired action got removed")
                    continue
                # Maybe known ledger issue that no action gets removed. Wait until actions removal
                # rewrite in ledger code is finished.
                xfail_ledger_3979_msgs.add("The action haven't got removed")

            remaining_actions = removed_actions.symmetric_difference(range(actions_num))
            if not remaining_actions:
                break
        else:
            raise AssertionError("Not all actions got removed")

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        assert (
            cluster.g_query.get_stake_addr_info(recv_stake_addr_rec.address).reward_account_balance
            == transfer_amt * actions_num
        ), "Incorrect reward account balance"

        # Try to vote on enacted action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _cast_vote(approve=False, vote_id="enacted")
        err_str = str(excinfo.value)
        assert "(GovActionsDoNotExist" in err_str, err_str

        # Check action view
        governance_utils.check_action_view(cluster_obj=cluster, action_data=withdrawal_actions[0])

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


class TestExpiration:
    """Tests for actions that expire."""

    @allure.link(helpers.get_vcs_link())
    def test_vote_info(
        self,
        cluster_use_governance: governance_setup.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test voting on info action.

        * submit an "info" action
        * vote on the the action
        * check the votes
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_use_governance
        temp_template = common.get_test_id(cluster)
        action_deposit_amt = cluster.conway_genesis["govActionDeposit"]

        # Linked user stories
        req_cli16 = requirements.Req(id="CLI016", group=requirements.GroupsKnown.CHANG_US)
        req_cli21 = requirements.Req(id="CLI021", group=requirements.GroupsKnown.CHANG_US)
        req_cli22 = requirements.Req(id="CLI022", group=requirements.GroupsKnown.CHANG_US)
        req_cli23 = requirements.Req(id="CLI023", group=requirements.GroupsKnown.CHANG_US)
        req_cli24 = requirements.Req(id="CLI024", group=requirements.GroupsKnown.CHANG_US)
        req_cli31 = requirements.Req(id="CLI031", group=requirements.GroupsKnown.CHANG_US)

        # Create an action

        rand_str = helpers.get_rand_str(4)
        anchor_url = f"http://www.info-action-{rand_str}.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        req_cli16.start(url=helpers.get_vcs_link())
        info_action = cluster.g_conway_governance.action.create_info(
            action_name=temp_template,
            deposit_amt=action_deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            deposit_return_stake_vkey_file=pool_user_ug.stake.vkey_file,
        )
        req_cli16.success()

        tx_files_action = clusterlib.TxFiles(
            proposal_files=[info_action.action_file],
            signing_key_files=[pool_user_ug.payment.skey_file],
        )

        # Make sure we have enough time to submit the proposal in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        req_cli23.start(url=helpers.get_vcs_link())
        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action",
            src_address=pool_user_ug.payment.address,
            use_build_cmd=True,
            tx_files=tx_files_action,
        )
        req_cli23.success()

        out_utxos_action = cluster.g_query.get_utxo(tx_raw_output=tx_output_action)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos_action, address=pool_user_ug.payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output_action.txins)
            - tx_output_action.fee
            - action_deposit_amt
        ), f"Incorrect balance for source address `{pool_user_ug.payment.address}`"

        action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
        req_cli31.start(url=helpers.get_vcs_link())
        action_gov_state = cluster.g_conway_governance.query.gov_state()
        _cur_epoch = cluster.g_query.get_epoch()
        conway_common.save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{_cur_epoch}"
        )
        prop_action = governance_utils.lookup_proposal(
            gov_state=action_gov_state, action_txid=action_txid
        )
        req_cli31.success()
        assert prop_action, "Info action not found"
        assert (
            prop_action["action"]["tag"] == governance_utils.ActionTags.INFO_ACTION.value
        ), "Incorrect action tag"

        # Vote

        action_ix = prop_action["actionId"]["govActionIx"]

        def _get_vote(idx: int) -> clusterlib.Votes:
            if idx % 3 == 0:
                return clusterlib.Votes.ABSTAIN
            if idx % 2 == 0:
                return clusterlib.Votes.YES
            return clusterlib.Votes.NO

        req_cli21.start(url=helpers.get_vcs_link())
        votes_cc = [
            cluster.g_conway_governance.vote.create_committee(
                vote_name=f"{temp_template}_cc{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=_get_vote(i),
                cc_hot_vkey_file=m.hot_vkey_file,
            )
            for i, m in enumerate(governance_data.cc_members, start=1)
        ]
        votes_drep = [
            cluster.g_conway_governance.vote.create_drep(
                vote_name=f"{temp_template}_drep{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=_get_vote(i),
                drep_vkey_file=d.key_pair.vkey_file,
            )
            for i, d in enumerate(governance_data.dreps_reg, start=1)
        ]
        votes_spo = [
            cluster.g_conway_governance.vote.create_spo(
                vote_name=f"{temp_template}_pool{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=_get_vote(i),
                cold_vkey_file=p.vkey_file,
            )
            for i, p in enumerate(governance_data.pools_cold, start=1)
        ]
        req_cli21.success()

        tx_files_vote = clusterlib.TxFiles(
            vote_files=[
                *[r.vote_file for r in votes_cc],
                *[r.vote_file for r in votes_drep],
                *[r.vote_file for r in votes_spo],
            ],
            signing_key_files=[
                pool_user_ug.payment.skey_file,
                *[r.hot_skey_file for r in governance_data.cc_members],
                *[r.key_pair.skey_file for r in governance_data.dreps_reg],
                *[r.skey_file for r in governance_data.pools_cold],
            ],
        )

        # Make sure we have enough time to submit the votes in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        req_cli24.start(url=helpers.get_vcs_link())
        tx_output_vote = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_vote",
            src_address=pool_user_ug.payment.address,
            use_build_cmd=True,
            tx_files=tx_files_vote,
        )
        req_cli24.success()

        out_utxos_vote = cluster.g_query.get_utxo(tx_raw_output=tx_output_vote)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos_vote, address=pool_user_ug.payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output_vote.txins) - tx_output_vote.fee
        ), f"Incorrect balance for source address `{pool_user_ug.payment.address}`"

        vote_gov_state = cluster.g_conway_governance.query.gov_state()
        _cur_epoch = cluster.g_query.get_epoch()
        conway_common.save_gov_state(
            gov_state=vote_gov_state, name_template=f"{temp_template}_vote_{_cur_epoch}"
        )
        prop_vote = governance_utils.lookup_proposal(
            gov_state=vote_gov_state, action_txid=action_txid
        )
        assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
        assert prop_vote["dRepVotes"], "No DRep votes"
        assert prop_vote["stakePoolVotes"], "No stake pool votes"

        # Check action view
        governance_utils.check_action_view(cluster_obj=cluster, action_data=info_action)

        # Check vote view
        req_cli22.start(url=helpers.get_vcs_link())
        if votes_cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=votes_cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=votes_drep[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=votes_spo[0])
        req_cli22.success()

    @allure.link(helpers.get_vcs_link())
    def test_expire_treasury_withdrawals(
        self,
        cluster_use_governance: governance_setup.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test expiration of treasury withdrawals.

        Use `transaction build-raw` for building the transactions.
        When available, use cardano-submit-api for proposal submission.

        * submit multiple "treasury withdrawal" actions
        * vote in a way that the actions are not approved

          - first action is approved by CC and disapproved by DReps
          - second action is disapproved by CC and approved by DReps
          - third action is disapproved by both CC and DReps

        * check that the actions are not ratified
        * check that the actions expire and action deposits are returned
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_use_governance
        temp_template = common.get_test_id(cluster)
        actions_num = 3

        # Linked user stories
        req_cli25 = requirements.Req(id="CLI025", group=requirements.GroupsKnown.CHANG_US)
        req_cli26 = requirements.Req(id="CLI026", group=requirements.GroupsKnown.CHANG_US)

        # Create stake address and registration certificate
        stake_deposit_amt = cluster.g_query.get_address_deposit()

        recv_stake_addr_rec = cluster.g_stake_address.gen_stake_addr_and_keys(
            name=f"{temp_template}_receive"
        )
        recv_stake_addr_reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_receive",
            deposit_amt=stake_deposit_amt,
            stake_vkey_file=recv_stake_addr_rec.vkey_file,
        )

        # Create an action and register stake address

        action_deposit_amt = cluster.conway_genesis["govActionDeposit"]
        transfer_amt = 5_000_000_000
        init_return_account_balance = cluster.g_query.get_stake_addr_info(
            pool_user_ug.stake.address
        ).reward_account_balance

        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        withdrawal_actions = [
            cluster.g_conway_governance.action.create_treasury_withdrawal(
                action_name=f"{temp_template}_{a}",
                transfer_amt=transfer_amt + a,
                deposit_amt=action_deposit_amt,
                anchor_url=f"http://www.withdrawal-expire{a}.com",
                anchor_data_hash=anchor_data_hash,
                funds_receiving_stake_vkey_file=recv_stake_addr_rec.vkey_file,
                deposit_return_stake_vkey_file=pool_user_ug.stake.vkey_file,
            )
            for a in range(actions_num)
        ]

        tx_files_action = clusterlib.TxFiles(
            certificate_files=[recv_stake_addr_reg_cert],
            proposal_files=[w.action_file for w in withdrawal_actions],
            signing_key_files=[pool_user_ug.payment.skey_file, recv_stake_addr_rec.skey_file],
        )

        # Make sure we have enough time to submit the proposals in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        action_prop_epoch = cluster.g_query.get_epoch()

        actions_deposit_combined = action_deposit_amt * len(withdrawal_actions)

        req_cli25.start(url=helpers.get_vcs_link())
        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action",
            src_address=pool_user_ug.payment.address,
            tx_files=tx_files_action,
            deposit=actions_deposit_combined + stake_deposit_amt,
        )
        req_cli25.success()

        assert cluster.g_query.get_stake_addr_info(
            recv_stake_addr_rec.address
        ).address, f"Stake address is not registered: {recv_stake_addr_rec.address}"

        out_utxos_action = cluster.g_query.get_utxo(tx_raw_output=tx_output_action)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos_action, address=pool_user_ug.payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output_action.txins)
            - tx_output_action.fee
            - actions_deposit_combined
            - stake_deposit_amt
        ), f"Incorrect balance for source address `{pool_user_ug.payment.address}`"

        action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
        action_gov_state = cluster.g_conway_governance.query.gov_state()
        _cur_epoch = cluster.g_query.get_epoch()
        conway_common.save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{_cur_epoch}"
        )

        def _get_vote(idx: int) -> clusterlib.Votes:
            if idx % 3 == 0:
                return clusterlib.Votes.ABSTAIN
            if idx % 2 == 0:
                return clusterlib.Votes.NO
            return clusterlib.Votes.YES

        votes_cc = []
        votes_drep = []
        for action_ix in range(actions_num):
            prop_action = governance_utils.lookup_proposal(
                gov_state=action_gov_state, action_txid=action_txid, action_ix=action_ix
            )
            assert prop_action, "Treasury withdrawals action not found"
            assert (
                prop_action["action"]["tag"]
                == governance_utils.ActionTags.TREASURY_WITHDRAWALS.value
            ), "Incorrect action tag"

            approve_cc = False
            approve_dreps = False
            if action_ix == 0:
                approve_cc = True
            elif action_ix == 1 and configuration.HAS_CC:
                approve_dreps = True

            votes_cc.extend(
                [
                    cluster.g_conway_governance.vote.create_committee(
                        vote_name=f"{temp_template}_{action_ix}_cc{i}",
                        action_txid=action_txid,
                        action_ix=action_ix,
                        vote=clusterlib.Votes.YES if approve_cc else _get_vote(i),
                        cc_hot_vkey_file=m.hot_vkey_file,
                    )
                    for i, m in enumerate(governance_data.cc_members, start=1)
                ]
            )
            votes_drep.extend(
                [
                    cluster.g_conway_governance.vote.create_drep(
                        vote_name=f"{temp_template}_{action_ix}_drep{i}",
                        action_txid=action_txid,
                        action_ix=action_ix,
                        vote=clusterlib.Votes.YES if approve_dreps else _get_vote(i),
                        drep_vkey_file=d.key_pair.vkey_file,
                    )
                    for i, d in enumerate(governance_data.dreps_reg, start=1)
                ]
            )

        tx_files_vote = clusterlib.TxFiles(
            vote_files=[
                *[r.vote_file for r in votes_cc],
                *[r.vote_file for r in votes_drep],
            ],
            signing_key_files=[
                pool_user_ug.payment.skey_file,
                *[r.hot_skey_file for r in governance_data.cc_members],
                *[r.key_pair.skey_file for r in governance_data.dreps_reg],
            ],
        )

        # Make sure we have enough time to submit the votes in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        req_cli26.start(url=helpers.get_vcs_link())
        tx_output_vote = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_vote",
            src_address=pool_user_ug.payment.address,
            submit_method=submit_utils.SubmitMethods.API
            if submit_utils.is_submit_api_available()
            else submit_utils.SubmitMethods.CLI,
            tx_files=tx_files_vote,
        )
        req_cli26.success()

        out_utxos_vote = cluster.g_query.get_utxo(tx_raw_output=tx_output_vote)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos_vote, address=pool_user_ug.payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output_vote.txins) - tx_output_vote.fee
        ), f"Incorrect balance for source address `{pool_user_ug.payment.address}`"

        vote_gov_state = cluster.g_conway_governance.query.gov_state()
        _cur_epoch = cluster.g_query.get_epoch()
        conway_common.save_gov_state(
            gov_state=vote_gov_state, name_template=f"{temp_template}_vote_{_cur_epoch}"
        )

        for action_ix in range(actions_num):
            prop_vote = governance_utils.lookup_proposal(
                gov_state=vote_gov_state, action_txid=action_txid, action_ix=action_ix
            )
            assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
            assert prop_vote["dRepVotes"], "No DRep votes"
            assert not prop_vote["stakePoolVotes"], "Unexpected stake pool votes"

        # Check that the actions are not ratified
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        nonrat_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=nonrat_gov_state, name_template=f"{temp_template}_nonrat_{_cur_epoch}"
        )
        for action_ix in range(actions_num):
            assert not governance_utils.lookup_removed_actions(
                gov_state=nonrat_gov_state, action_txid=action_txid, action_ix=action_ix
            )

        # Check that the actions are not enacted
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        nonenacted_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=nonenacted_gov_state, name_template=f"{temp_template}_nonenact_{_cur_epoch}"
        )
        assert (
            cluster.g_query.get_stake_addr_info(recv_stake_addr_rec.address).reward_account_balance
            == 0
        ), "Incorrect reward account balance"

        # Check that the actions expired
        epochs_to_expiration = (
            cluster.conway_genesis["govActionLifetime"]
            + 1
            + action_prop_epoch
            - cluster.g_query.get_epoch()
        )
        _cur_epoch = cluster.wait_for_new_epoch(new_epochs=epochs_to_expiration, padding_seconds=5)
        expire_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=expire_gov_state, name_template=f"{temp_template}_expire_{_cur_epoch}"
        )
        assert (
            cluster.g_query.get_stake_addr_info(recv_stake_addr_rec.address).reward_account_balance
            == 0
        ), "Incorrect reward account balance"
        expire_return_account_balance = cluster.g_query.get_stake_addr_info(
            pool_user_ug.stake.address
        ).reward_account_balance
        assert (
            expire_return_account_balance == init_return_account_balance
        ), f"Incorrect return account balance {expire_return_account_balance}"

        # Check that the proposals were removed and the actions deposits were returned
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        rem_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=rem_gov_state, name_template=f"{temp_template}_rem_{_cur_epoch}"
        )
        rem_deposit_returned = cluster.g_query.get_stake_addr_info(
            pool_user_ug.stake.address
        ).reward_account_balance

        # Known ledger issue where only one expired action gets removed in one epoch
        if (
            rem_deposit_returned < init_return_account_balance + actions_deposit_combined
        ) and conway_common.possible_rem_issue(gov_state=rem_gov_state, epoch=_cur_epoch):
            blockers.GH(
                issue=3979,
                repo="IntersectMBO/cardano-ledger",
                message="Only single expired action got removed",
                check_on_devel=False,
            ).finish_test()

        assert (
            rem_deposit_returned == init_return_account_balance + actions_deposit_combined
        ), "Incorrect return account balance"

        # Additional checks of governance state output
        assert governance_utils.lookup_removed_actions(
            gov_state=expire_gov_state, action_txid=action_txid
        ), "Action not found in removed actions"
        assert governance_utils.lookup_proposal(
            gov_state=expire_gov_state, action_txid=action_txid
        ), "Action no longer found in proposals"

        assert not governance_utils.lookup_proposal(
            gov_state=rem_gov_state, action_txid=action_txid
        ), "Action was not removed from proposals"
