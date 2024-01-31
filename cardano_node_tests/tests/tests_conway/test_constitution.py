"""Tests for Conway governance constitution."""
# pylint: disable=expression-not-assigned
import logging

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


class TestConstitution:
    """Tests for constitution."""

    @allure.link(helpers.get_vcs_link())
    def test_change_constitution(
        self,
        cluster_lock_governance: governance_setup.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test enactment of change of constitution.

        * submit a "create constitution" action
        * check that SPOs cannot vote on a "create constitution" action
        * vote to disapprove the action
        * vote to approve the action
        * check that the action is ratified
        * check that the action is enacted
        * check that it's not possible to vote on enacted action
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Linked user stories
        req_cli1 = requirements.Req(id="CLI001", group=requirements.GroupsKnown.CHANG_US)
        req_cli2 = requirements.Req(id="CLI002", group=requirements.GroupsKnown.CHANG_US)
        req_cli13 = requirements.Req(id="CLI013", group=requirements.GroupsKnown.CHANG_US)
        req_cli20 = requirements.Req(id="CLI020", group=requirements.GroupsKnown.CHANG_US)
        req_cip1 = requirements.Req(id="CIP001", group=requirements.GroupsKnown.CHANG_US)

        # Create an action

        deposit_amt = cluster.conway_genesis["govActionDeposit"]

        anchor_url = "http://www.const-action.com"
        anchor_data_hash = cluster.g_conway_governance.get_anchor_data_hash(text=anchor_url)

        constitution_file = f"{temp_template}_constitution.txt"
        constitution_text = (
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, "
            "sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
            "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi "
            "ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit "
            "in voluptate velit esse cillum dolore eu fugiat nulla pariatur. "
            "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia "
            "deserunt mollit anim id est laborum."
        )
        with open(constitution_file, "w", encoding="utf-8") as out_fp:
            out_fp.write(constitution_text)

        constitution_url = "http://www.const-new.com"
        req_cli2.start(url=helpers.get_vcs_link())
        constitution_hash = cluster.g_conway_governance.get_anchor_data_hash(
            file_text=constitution_file
        )
        req_cli2.success()

        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.CONSTITUTION,
            gov_state=cluster.g_conway_governance.query.gov_state(),
        )

        req_cli13.start(url=helpers.get_vcs_link())
        constitution_action = cluster.g_conway_governance.action.create_constitution(
            action_name=temp_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            constitution_url=constitution_url,
            constitution_hash=constitution_hash,
            prev_action_txid=prev_action_rec.txid,
            prev_action_ix=prev_action_rec.ix,
            deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
        )
        req_cli13.success()

        tx_files_action = clusterlib.TxFiles(
            proposal_files=[constitution_action.action_file],
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
        assert prop_action, "Create constitution action not found"
        assert (
            prop_action["action"]["tag"] == governance_utils.ActionTags.NEW_CONSTITUTION.value
        ), "Incorrect action tag"

        action_ix = prop_action["actionId"]["govActionIx"]

        def _cast_vote(
            approve: bool, vote_id: str, add_spo_votes: bool = False
        ) -> conway_common.VotedVotes:
            votes_cc = [
                cluster.g_conway_governance.vote.create_committee(
                    vote_name=f"{temp_template}_{vote_id}_cc{i}",
                    action_txid=action_txid,
                    action_ix=action_ix,
                    vote=clusterlib.Votes.YES if approve else clusterlib.Votes.NO,
                    cc_hot_vkey_file=m.hot_vkey_file,
                )
                for i, m in enumerate(governance_data.cc_members, start=1)
            ]
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
            anchor = state["constitution"]["anchor"]
            assert (
                anchor["dataHash"]
                == constitution_hash
                == "d6d9034f61e2f7ada6e58c252e15684c8df7f0b197a95d80f42ca0a3685de26e"
            ), "Incorrect constitution anchor hash"
            assert anchor["url"] == constitution_url, "Incorrect constitution anchor URL"

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
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (req_cli1, req_cip1)]
        _check_state(next_rat_state["nextEnactState"])
        [r.success() for r in (req_cli1, req_cip1)]
        assert next_rat_state["ratificationDelayed"], "Ratification not delayed"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
        )
        _check_state(enact_gov_state["enactState"])

        # Try to vote on enacted action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _cast_vote(approve=False, vote_id="enacted")
        err_str = str(excinfo.value)
        assert "(GovActionsDoNotExist" in err_str, err_str

        # Check action view
        req_cli20.start(url=helpers.get_vcs_link())
        governance_utils.check_action_view(cluster_obj=cluster, action_data=constitution_action)
        req_cli20.success()

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
