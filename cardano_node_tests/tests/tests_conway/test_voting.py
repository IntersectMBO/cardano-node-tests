"""Tests for Conway governance voting functionality."""
# pylint: disable=expression-not-assigned
import dataclasses
import json
import logging
import pathlib as pl
import random
import typing as tp

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
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


@dataclasses.dataclass(frozen=True, order=True)
class VotedVotes:
    cc: tp.List[clusterlib.VoteCC]  # pylint: disable=invalid-name
    drep: tp.List[clusterlib.VoteDrep]
    spo: tp.List[clusterlib.VoteSPO]


def _possible_rem_issue(gov_state: tp.Dict[str, tp.Any], epoch: int) -> bool:
    """Check if the unexpected removed action situation can be result of known ledger issue.

    When the issue manifests, only single expired action gets removed and all other expired or
    ratified actions are ignored int the given epoch.
    """
    removed_actions: tp.List[tp.Dict[str, tp.Any]] = gov_state["nextRatifyState"][
        "removedGovActions"
    ]
    proposals: tp.List[tp.Dict[str, tp.Any]] = gov_state["proposals"]

    if len(removed_actions) != 1 or len(proposals) == 1:
        return False

    action_txid = removed_actions[0]["txId"]
    action_ix = removed_actions[0]["govActionIx"]

    for _p in proposals:
        _p_action_id = _p["actionId"]
        if (
            _p["expiresAfter"] < epoch
            and _p_action_id["txId"] == action_txid
            and _p_action_id["govActionIx"] == action_ix
        ):
            return True

    return False


def _save_gov_state(gov_state: tp.Dict[str, tp.Any], name_template: str) -> None:
    """Save governance state to a file."""
    with open(f"{name_template}_gov_state.json", "w", encoding="utf-8") as out_fp:
        json.dump(gov_state, out_fp, indent=2)


def _save_committee_state(committee_state: tp.Dict[str, tp.Any], name_template: str) -> None:
    """Save CC state to a file."""
    with open(f"{name_template}_committee_state.json", "w", encoding="utf-8") as out_fp:
        json.dump(committee_state, out_fp, indent=2)


def get_pool_user(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    caching_key: str,
) -> clusterlib.PoolUser:
    """Create a pool user."""
    with cluster_manager.cache_fixture(key=caching_key) as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        test_id = common.get_test_id(cluster_obj)
        pool_user = clusterlib_utils.create_pool_users(
            cluster_obj=cluster_obj,
            name_template=f"{test_id}_pool_user",
            no_of_addr=1,
        )[0]
        fixture_cache.value = pool_user

    # Fund the payment address with some ADA
    clusterlib_utils.fund_from_faucet(
        pool_user.payment,
        cluster_obj=cluster_obj,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )

    # Register the stake address
    stake_deposit_amt = cluster_obj.g_query.get_address_deposit()
    stake_addr_reg_cert = cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
        addr_name=f"{test_id}_pool_user",
        deposit_amt=stake_deposit_amt,
        stake_vkey_file=pool_user.stake.vkey_file,
    )
    tx_files_action = clusterlib.TxFiles(
        certificate_files=[stake_addr_reg_cert],
        signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
    )

    clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{test_id}_pool_user",
        src_address=pool_user.payment.address,
        use_build_cmd=True,
        tx_files=tx_files_action,
    )

    assert cluster_obj.g_query.get_stake_addr_info(
        pool_user.stake.address
    ).address, f"Stake address is not registered: {pool_user.stake.address}"

    return pool_user


@pytest.fixture
def pool_user_lg(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_governance: governance_setup.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "lock governance"."""
    cluster, __ = cluster_lock_governance
    key = helpers.get_current_line_str()
    return get_pool_user(cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key)


@pytest.fixture
def pool_user_ug(
    cluster_manager: cluster_management.ClusterManager,
    cluster_use_governance: governance_setup.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "use governance"."""
    cluster, __ = cluster_use_governance
    key = helpers.get_current_line_str()
    return get_pool_user(cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key)


class TestEnactment:
    """Tests for actions enactment."""

    @allure.link(helpers.get_vcs_link())
    def test_constitution(
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
        req_cli1 = requirements.Req(id="CLI01", group=requirements.GroupsKnown.CHANG_US)
        req_cli2 = requirements.Req(id="CLI02", group=requirements.GroupsKnown.CHANG_US)
        req_cli13 = requirements.Req(id="CLI13", group=requirements.GroupsKnown.CHANG_US)
        req_cli20 = requirements.Req(id="CLI20", group=requirements.GroupsKnown.CHANG_US)
        req_cip1 = requirements.Req(id="CIP.001", group=requirements.GroupsKnown.CHANG_US)

        # Create an action

        deposit_amt = cluster.conway_genesis["govActionDeposit"]

        anchor_url = "http://www.const-action.com"
        anchor_data_hash = cluster.g_conway_governance.get_hash(text=anchor_url)

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
        constitution_hash = cluster.g_conway_governance.get_hash(file_text=constitution_file)
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
        _save_gov_state(
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

        def _cast_vote(approve: bool, vote_id: str, add_spo_votes: bool = False) -> VotedVotes:
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
            _save_gov_state(
                gov_state=vote_gov_state,
                name_template=f"{temp_template}_vote_{vote_id}_{_cur_epoch}",
            )
            prop_vote = governance_utils.lookup_proposal(
                gov_state=vote_gov_state, action_txid=action_txid
            )
            assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
            assert prop_vote["dRepVotes"], "No DRep votes"
            assert not prop_vote["stakePoolVotes"], "Unexpected stake pool votes"

            return VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo)

        # Check that SPOs cannot vote on change of constitution action
        try:
            _cast_vote(approve=False, vote_id="with_spos", add_spo_votes=True)
        except clusterlib.CLIError as err:
            if "StakePoolVoter" not in str(err):
                raise

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
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        rat_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}")
        rem_action = governance_utils.lookup_removed_actions(
            gov_state=rat_gov_state, action_txid=action_txid
        )

        # Known ledger issue where only one expired action gets removed in one epoch
        if not rem_action and _possible_rem_issue(gov_state=rat_gov_state, epoch=_cur_epoch):
            pytest.xfail("Only single expired action got removed")

        assert rem_action, "Action not found in removed actions"
        next_rat_state = rat_gov_state["nextRatifyState"]
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (req_cli1, req_cip1)]
        _check_state(next_rat_state["nextEnactState"])
        [r.success() for r in (req_cli1, req_cip1)]
        assert next_rat_state["ratificationDelayed"], "Ratification not delayed"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
        )
        _check_state(enact_gov_state["enactState"])

        # Try to vote on enacted action
        try:
            _cast_vote(approve=False, vote_id="enacted")
        except clusterlib.CLIError as err:
            if "(GovActionsDoNotExist" not in str(err):
                raise

        # Check action view
        req_cli20.start(url=helpers.get_vcs_link())
        governance_utils.check_action_view(cluster_obj=cluster, action_data=constitution_action)
        req_cli20.success()

        # Check vote view
        if voted_votes.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.drep[0])

    @allure.link(helpers.get_vcs_link())
    def test_add_new_committee_member(  # noqa: C901
        self,
        cluster_lock_governance: governance_setup.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
        testfile_temp_dir: pl.Path,
        request: FixtureRequest,
    ):
        """Test adding new CC member.

        * create an "update committee" action
        * vote to disapprove the action
        * vote to approve the action
        * check that CC members votes have no effect
        * check that the action is ratified
        * check that the action is enacted
        * check that it's not possible to vote on enacted action
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Linked user stories
        req_cli14 = requirements.Req(id="CLI14", group=requirements.GroupsKnown.CHANG_US)

        # Authorize the hot keys

        cc_auth_record1 = governance_utils.get_cc_member_auth_record(
            cluster_obj=cluster,
            name_template=f"{temp_template}_member1",
        )
        cc_member1_key = f"keyHash-{cc_auth_record1.key_hash}"

        cc_auth_record2 = governance_utils.get_cc_member_auth_record(
            cluster_obj=cluster,
            name_template=f"{temp_template}_member2",
        )
        cc_member2_key = f"keyHash-{cc_auth_record2.key_hash}"

        tx_files_auth = clusterlib.TxFiles(
            certificate_files=[
                cc_auth_record1.auth_cert,
                cc_auth_record2.auth_cert,
            ],
            signing_key_files=[
                pool_user_lg.payment.skey_file,
                cc_auth_record1.cold_key_pair.skey_file,
                cc_auth_record2.cold_key_pair.skey_file,
            ],
        )

        tx_output_auth = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_auth",
            src_address=pool_user_lg.payment.address,
            use_build_cmd=True,
            tx_files=tx_files_auth,
        )

        out_utxos_auth = cluster.g_query.get_utxo(tx_raw_output=tx_output_auth)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos_auth, address=pool_user_lg.payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output_auth.txins) - tx_output_auth.fee
        ), f"Incorrect balance for source address `{pool_user_lg.payment.address}`"

        auth_committee_state = cluster.g_conway_governance.query.committee_state()
        _cur_epoch = cluster.g_query.get_epoch()
        _save_committee_state(
            committee_state=auth_committee_state,
            name_template=f"{temp_template}_auth_{_cur_epoch}",
        )
        for mk in (cc_member1_key, cc_member2_key):
            auth_member_rec = auth_committee_state["committee"][mk]
            assert (
                auth_member_rec["hotCredsAuthStatus"]["tag"] == "MemberAuthorized"
            ), "CC Member was NOT authorized"
            assert not auth_member_rec["expiration"], "CC Member should not be elected"
            assert auth_member_rec["status"] == "Unrecognized", "CC Member should not be recognized"

        # Create an action

        cc_member = clusterlib.CCMember(
            epoch=cluster.g_query.get_epoch() + 4,
            cold_vkey_file=cc_auth_record1.cold_key_pair.vkey_file,
            cold_skey_file=cc_auth_record1.cold_key_pair.skey_file,
            hot_vkey_file=cc_auth_record1.hot_key_pair.vkey_file,
            hot_skey_file=cc_auth_record1.hot_key_pair.skey_file,
        )

        deposit_amt = cluster.conway_genesis["govActionDeposit"]
        anchor_url = "http://www.cc-update.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"
        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.COMMITTEE,
            gov_state=cluster.g_conway_governance.query.gov_state(),
        )

        req_cli14.start(url=helpers.get_vcs_link())
        update_action = cluster.g_conway_governance.action.update_committee(
            action_name=temp_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            quorum=str(cluster.conway_genesis["committee"]["quorum"]),
            add_cc_members=[cc_member],
            prev_action_txid=prev_action_rec.txid,
            prev_action_ix=prev_action_rec.ix,
            deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
        )
        req_cli14.success()

        tx_files_action = clusterlib.TxFiles(
            proposal_files=[update_action.action_file],
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
        _save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{_cur_epoch}"
        )
        prop_action = governance_utils.lookup_proposal(
            gov_state=action_gov_state, action_txid=action_txid
        )
        assert prop_action, "Update committee action not found"
        assert (
            prop_action["action"]["tag"] == governance_utils.ActionTags.UPDATE_COMMITTEE.value
        ), "Incorrect action tag"

        action_ix = prop_action["actionId"]["govActionIx"]

        # Resign the CC member so it doesn't affect voting
        def _resign():
            with helpers.change_cwd(testfile_temp_dir):
                res_cert = cluster.g_conway_governance.committee.gen_cold_key_resignation_cert(
                    key_name=temp_template,
                    cold_vkey_file=cc_auth_record1.cold_key_pair.vkey_file,
                    resignation_metadata_url="http://www.cc-resign.com",
                    resignation_metadata_hash="5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d",
                )

                tx_files_res = clusterlib.TxFiles(
                    certificate_files=[res_cert],
                    signing_key_files=[
                        pool_user_lg.payment.skey_file,
                        cc_auth_record1.cold_key_pair.skey_file,
                    ],
                )

                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_res",
                    src_address=pool_user_lg.payment.address,
                    use_build_cmd=True,
                    tx_files=tx_files_res,
                )

        resign_requested = [False]

        def _cast_vote(approve: bool, vote_id: str, add_cc_votes: bool = False) -> VotedVotes:
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
            votes_spo = [
                cluster.g_conway_governance.vote.create_spo(
                    vote_name=f"{temp_template}_{vote_id}_pool{i}",
                    action_txid=action_txid,
                    action_ix=action_ix,
                    vote=clusterlib.Votes.YES if approve else clusterlib.Votes.NO,
                    cold_vkey_file=p.vkey_file,
                )
                for i, p in enumerate(governance_data.pools_cold, start=1)
            ]

            votes_cc = []
            if add_cc_votes:
                votes_cc = [
                    cluster.g_conway_governance.vote.create_committee(
                        vote_name=f"{temp_template}_{vote_id}_cc{i}",
                        action_txid=action_txid,
                        action_ix=action_ix,
                        vote=clusterlib.Votes.NO,
                        cc_hot_vkey_file=m.hot_vkey_file,
                        anchor_url="http://www.cc-vote.com",
                        anchor_data_hash="5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d",
                    )
                    for i, m in enumerate(governance_data.cc_members, start=1)
                ]

            cc_keys = [r.hot_skey_file for r in governance_data.cc_members] if votes_cc else []
            tx_files_vote = clusterlib.TxFiles(
                vote_files=[
                    *[r.vote_file for r in votes_drep],
                    *[r.vote_file for r in votes_spo],
                    *[r.vote_file for r in votes_cc],
                ],
                signing_key_files=[
                    pool_user_lg.payment.skey_file,
                    *[r.skey_file for r in governance_data.pools_cold],
                    *[r.key_pair.skey_file for r in governance_data.dreps_reg],
                    *cc_keys,
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

            if approve and not resign_requested[0]:
                request.addfinalizer(_resign)
                resign_requested[0] = True

            out_utxos_vote = cluster.g_query.get_utxo(tx_raw_output=tx_output_vote)
            assert (
                clusterlib.filter_utxos(utxos=out_utxos_vote, address=pool_user_lg.payment.address)[
                    0
                ].amount
                == clusterlib.calculate_utxos_balance(tx_output_vote.txins) - tx_output_vote.fee
            ), f"Incorrect balance for source address `{pool_user_lg.payment.address}`"

            vote_gov_state = cluster.g_conway_governance.query.gov_state()
            _cur_epoch = cluster.g_query.get_epoch()
            _save_gov_state(
                gov_state=vote_gov_state,
                name_template=f"{temp_template}_vote_{vote_id}_{_cur_epoch}",
            )
            prop_vote = governance_utils.lookup_proposal(
                gov_state=vote_gov_state, action_txid=action_txid
            )
            if not votes_cc:
                assert not prop_vote["committeeVotes"], "Unexpected committee votes"
            assert prop_vote["dRepVotes"], "No DRep votes"
            assert prop_vote["stakePoolVotes"], "No stake pool votes"

            return VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo)

        # Vote & disapprove the action
        _cast_vote(approve=False, vote_id="no")

        # Vote & approve the action
        _cast_vote(approve=True, vote_id="yes")

        # Check that CC members votes on "update committee" action are ignored
        voted_votes = _cast_vote(approve=True, vote_id="with_ccs", add_cc_votes=True)

        def _check_state(state: dict):
            cc_member_val = state["committee"]["members"].get(cc_member1_key)
            assert cc_member_val, "New committee member not found"
            assert cc_member_val == cc_member.epoch

        # Check ratification
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        rat_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}")
        rem_action = governance_utils.lookup_removed_actions(
            gov_state=rat_gov_state, action_txid=action_txid
        )

        # Known ledger issue where only one expired action gets removed in one epoch
        if not rem_action and _possible_rem_issue(gov_state=rat_gov_state, epoch=_cur_epoch):
            pytest.xfail("Only single expired action got removed")

        assert rem_action, "Action not found in removed actions"

        next_rat_state = rat_gov_state["nextRatifyState"]
        _check_state(next_rat_state["nextEnactState"])
        assert next_rat_state["ratificationDelayed"], "Ratification not delayed"

        # Check committee state after ratification
        rat_committee_state = cluster.g_conway_governance.query.committee_state()
        _save_committee_state(
            committee_state=rat_committee_state,
            name_template=f"{temp_template}_rat_{_cur_epoch}",
        )

        has_ledger_issue_4001 = False
        rat_member_rec = rat_committee_state["committee"].get(cc_member1_key) or {}
        if rat_member_rec:
            assert (
                rat_member_rec["hotCredsAuthStatus"]["tag"] == "MemberAuthorized"
            ), "CC Member is no longer authorized"
        else:
            has_ledger_issue_4001 = True

        assert not rat_committee_state["committee"].get(
            cc_member2_key
        ), "Non-elected unrecognized CC member was not removed"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
        )
        _check_state(enact_gov_state["enactState"])

        # Check committee state after enactment
        enact_committee_state = cluster.g_conway_governance.query.committee_state()
        _save_committee_state(
            committee_state=enact_committee_state,
            name_template=f"{temp_template}_enact_{_cur_epoch}",
        )
        enact_member1_rec = enact_committee_state["committee"][cc_member1_key]
        assert (
            has_ledger_issue_4001
            or enact_member1_rec["hotCredsAuthStatus"]["tag"] == "MemberAuthorized"
        ), "CC Member was NOT authorized"
        assert enact_member1_rec["expiration"] == cc_member.epoch, "Expiration epoch is incorrect"
        assert enact_member1_rec["status"] == "Active", "CC Member should be active"

        # Try to vote on enacted action
        try:
            _cast_vote(approve=False, vote_id="enacted")
        except clusterlib.CLIError as err:
            if "(GovActionsDoNotExist" not in str(err):
                raise

        # Check action view
        governance_utils.check_action_view(cluster_obj=cluster, action_data=update_action)

        # Check vote view
        if voted_votes.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.drep[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.spo[0])

        if has_ledger_issue_4001:
            blockers.GH(
                issue=4001,
                repo="IntersectMBO/cardano-ledger",
                message="Newly elected CC members are removed during ratification",
            ).finish_test()

    @allure.link(helpers.get_vcs_link())
    def test_pparam_update(
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
        req_cli17 = requirements.Req(id="CLI17", group=requirements.GroupsKnown.CHANG_US)

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
        update_args = clusterlib_utils.get_pparams_update_args(update_proposals=update_proposals)

        req_cli17.start(url=helpers.get_vcs_link())
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
        req_cli17.success()

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
        _save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{_cur_epoch}"
        )
        prop_action = governance_utils.lookup_proposal(
            gov_state=action_gov_state, action_txid=action_txid
        )
        assert prop_action, "Param update action not found"
        assert (
            prop_action["action"]["tag"] == governance_utils.ActionTags.PARAMETER_CHANGE.value
        ), "Incorrect action tag"

        action_ix = prop_action["actionId"]["govActionIx"]

        def _cast_vote(approve: bool, vote_id: str, add_spo_votes: bool = False) -> VotedVotes:
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
            _save_gov_state(
                gov_state=vote_gov_state,
                name_template=f"{temp_template}_vote_{vote_id}_{_cur_epoch}",
            )
            prop_vote = governance_utils.lookup_proposal(
                gov_state=vote_gov_state, action_txid=action_txid
            )
            assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
            assert prop_vote["dRepVotes"], "No DRep votes"
            assert not prop_vote["stakePoolVotes"], "Unexpected stake pool votes"

            return VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo)

        # Check that SPOs cannot vote on change of constitution action
        try:
            _cast_vote(approve=False, vote_id="with_spos", add_spo_votes=True)
        except clusterlib.CLIError as err:
            if "StakePoolVoter" not in str(err):
                raise

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
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        rat_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}")
        rem_action = governance_utils.lookup_removed_actions(
            gov_state=rat_gov_state, action_txid=action_txid
        )

        # Known ledger issue where only one expired action gets removed in one epoch
        if not rem_action and _possible_rem_issue(gov_state=rat_gov_state, epoch=_cur_epoch):
            pytest.xfail("Only single expired action got removed")

        assert rem_action, "Action not found in removed actions"

        next_rat_state = rat_gov_state["nextRatifyState"]
        _check_state(next_rat_state["nextEnactState"])
        assert not next_rat_state["ratificationDelayed"], "Ratification is delayed unexpectedly"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
        )
        _check_state(enact_gov_state["enactState"])

        # Try to vote on enacted action
        try:
            _cast_vote(approve=False, vote_id="enacted")
        except clusterlib.CLIError as err:
            if "(GovActionsDoNotExist" not in str(err):
                raise

        # Check vote view
        if voted_votes.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.drep[0])

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
        req_cli15 = requirements.Req(id="CLI15", group=requirements.GroupsKnown.CHANG_US)

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
        _save_gov_state(
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

        def _cast_vote(approve: bool, vote_id: str, add_spo_votes: bool = False) -> VotedVotes:
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
            _save_gov_state(
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

            return VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo)

        # Check that SPOs cannot vote on change of constitution action
        try:
            _cast_vote(approve=False, vote_id="with_spos", add_spo_votes=True)
        except clusterlib.CLIError as err:
            if "StakePoolVoter" not in str(err):
                raise

        # Vote & disapprove the action
        _cast_vote(approve=False, vote_id="no")

        # Vote & approve the action
        voted_votes = _cast_vote(approve=True, vote_id="yes")

        # Check ratification
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        rat_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}")
        rem_action = governance_utils.lookup_removed_actions(
            gov_state=rat_gov_state, action_txid=action_txid
        )

        # Known ledger issue where only one expired action gets removed in one epoch
        if not rem_action and _possible_rem_issue(gov_state=rat_gov_state, epoch=_cur_epoch):
            pytest.xfail("Only single expired action got removed")

        assert rem_action, "Action not found in removed actions"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        assert (
            cluster.g_query.get_stake_addr_info(recv_stake_addr_rec.address).reward_account_balance
            == transfer_amt * actions_num
        ), "Incorrect reward account balance"

        # Try to vote on enacted action
        try:
            _cast_vote(approve=False, vote_id="enacted")
        except clusterlib.CLIError as err:
            if "(GovActionsDoNotExist" not in str(err):
                raise

        # Check action view
        governance_utils.check_action_view(cluster_obj=cluster, action_data=withdrawal_actions[0])

        # Check vote view
        if voted_votes.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.drep[0])

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not configuration.HAS_CC, reason="Runs only on setup with CC")
    @pytest.mark.long
    def test_no_confidence(
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
        * check that the action is enacted
        * check that it's not possible to vote on enacted action
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Linked user stories
        req_cli18 = requirements.Req(id="CLI18", group=requirements.GroupsKnown.CHANG_US)

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

        # Create an action
        deposit_amt = cluster.conway_genesis["govActionDeposit"]
        anchor_url = "http://www.cc-no-confidence.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        req_cli18.start(url=helpers.get_vcs_link())
        no_confidence_action = cluster.g_conway_governance.action.create_no_confidence(
            action_name=temp_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            prev_action_txid=prev_action_rec.txid,
            prev_action_ix=prev_action_rec.ix,
            deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
        )
        req_cli18.success()

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
        _save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{_cur_epoch}"
        )
        prop_action = governance_utils.lookup_proposal(
            gov_state=action_gov_state, action_txid=action_txid
        )
        assert prop_action, "No confidence action not found"
        assert (
            prop_action["action"]["tag"] == governance_utils.ActionTags.NO_CONFIDENCE.value
        ), "Incorrect action tag"

        action_ix = prop_action["actionId"]["govActionIx"]

        def _cast_vote(approve: bool, vote_id: str, add_cc_votes: bool = False) -> VotedVotes:
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
            votes_spo = [
                cluster.g_conway_governance.vote.create_spo(
                    vote_name=f"{temp_template}_{vote_id}_pool{i}",
                    action_txid=action_txid,
                    action_ix=action_ix,
                    vote=clusterlib.Votes.YES if approve else clusterlib.Votes.NO,
                    cold_vkey_file=p.vkey_file,
                )
                for i, p in enumerate(governance_data.pools_cold, start=1)
            ]

            votes_cc = []
            if add_cc_votes:
                votes_cc = [
                    cluster.g_conway_governance.vote.create_committee(
                        vote_name=f"{temp_template}_{vote_id}_cc{i}",
                        action_txid=action_txid,
                        action_ix=action_ix,
                        vote=clusterlib.Votes.NO,
                        cc_hot_vkey_file=m.hot_vkey_file,
                    )
                    for i, m in enumerate(governance_data.cc_members, start=1)
                ]

            cc_keys = [r.hot_skey_file for r in governance_data.cc_members] if votes_cc else []
            tx_files_vote = clusterlib.TxFiles(
                vote_files=[
                    *[r.vote_file for r in votes_drep],
                    *[r.vote_file for r in votes_spo],
                    *[r.vote_file for r in votes_cc],
                ],
                signing_key_files=[
                    pool_user_lg.payment.skey_file,
                    *[r.skey_file for r in governance_data.pools_cold],
                    *[r.key_pair.skey_file for r in governance_data.dreps_reg],
                    *cc_keys,
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
            _save_gov_state(
                gov_state=vote_gov_state,
                name_template=f"{temp_template}_vote_{vote_id}_{_cur_epoch}",
            )
            prop_vote = governance_utils.lookup_proposal(
                gov_state=vote_gov_state, action_txid=action_txid
            )
            if not votes_cc:
                assert not prop_vote["committeeVotes"], "Unexpected committee votes"
            assert prop_vote["dRepVotes"], "No DRep votes"
            assert prop_vote["stakePoolVotes"], "No stake pool votes"

            return VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo)

        # Vote & disapprove the action
        _cast_vote(approve=False, vote_id="no")

        # Vote & approve the action
        _cast_vote(approve=True, vote_id="yes")

        # Testnet will be in state of no confidence, respin is needed
        with cluster_manager.respin_on_failure():
            # Check that CC members votes on "no confidence" action are ignored
            voted_votes = _cast_vote(approve=True, vote_id="with_ccs", add_cc_votes=True)

            # Check ratification
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            rat_gov_state = cluster.g_conway_governance.query.gov_state()
            _save_gov_state(
                gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}"
            )
            rem_action = governance_utils.lookup_removed_actions(
                gov_state=rat_gov_state, action_txid=action_txid
            )

            # Known ledger issue where only one expired action gets removed in one epoch
            if not rem_action and _possible_rem_issue(gov_state=rat_gov_state, epoch=_cur_epoch):
                pytest.xfail("Only single expired action got removed")

            assert rem_action, "Action not found in removed actions"

            next_rat_state = rat_gov_state["nextRatifyState"]
            assert next_rat_state["ratificationDelayed"], "Ratification not delayed"

            # Check enactment
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            enact_gov_state = cluster.g_conway_governance.query.gov_state()
            _save_gov_state(
                gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
            )
            assert not enact_gov_state["enactState"]["committee"], "Committee is not empty"

            enact_prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.COMMITTEE,
                gov_state=enact_gov_state,
            )
            assert enact_prev_action_rec.txid == action_txid, "Incorrect previous action Txid"
            assert enact_prev_action_rec.ix == action_ix, "Incorrect previous action index"

            # Try to vote on enacted action
            try:
                _cast_vote(approve=False, vote_id="enacted")
            except clusterlib.CLIError as err:
                if "(GovActionsDoNotExist" not in str(err):
                    raise

            governance_setup.reinstate_committee(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_reinstate2",
                pool_user=pool_user_lg,
            )

        # Check action view
        governance_utils.check_action_view(cluster_obj=cluster, action_data=no_confidence_action)

        # Check vote view
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.drep[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.spo[0])


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
        req_cli16 = requirements.Req(id="CLI16", group=requirements.GroupsKnown.CHANG_US)
        req_cli21 = requirements.Req(id="CLI21", group=requirements.GroupsKnown.CHANG_US)
        req_cli22 = requirements.Req(id="CLI22", group=requirements.GroupsKnown.CHANG_US)
        req_cli23 = requirements.Req(id="CLI23", group=requirements.GroupsKnown.CHANG_US)
        req_cli24 = requirements.Req(id="CLI24", group=requirements.GroupsKnown.CHANG_US)
        req_cli31 = requirements.Req(id="CLI31", group=requirements.GroupsKnown.CHANG_US)

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
        _save_gov_state(
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
        _save_gov_state(
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
        req_cli25 = requirements.Req(id="CLI25", group=requirements.GroupsKnown.CHANG_US)
        req_cli26 = requirements.Req(id="CLI26", group=requirements.GroupsKnown.CHANG_US)

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
        _save_gov_state(
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
        _save_gov_state(
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
        _save_gov_state(
            gov_state=nonrat_gov_state, name_template=f"{temp_template}_nonrat_{_cur_epoch}"
        )
        for action_ix in range(actions_num):
            assert not governance_utils.lookup_removed_actions(
                gov_state=nonrat_gov_state, action_txid=action_txid, action_ix=action_ix
            )

        # Check that the actions are not enacted
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        nonenacted_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(
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
        _save_gov_state(
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
        _save_gov_state(gov_state=rem_gov_state, name_template=f"{temp_template}_rem_{_cur_epoch}")
        rem_deposit_returned = cluster.g_query.get_stake_addr_info(
            pool_user_ug.stake.address
        ).reward_account_balance

        # Known ledger issue where only one expired action gets removed in one epoch
        if (
            rem_deposit_returned < init_return_account_balance + actions_deposit_combined
        ) and _possible_rem_issue(gov_state=rem_gov_state, epoch=_cur_epoch):
            pytest.xfail("Only single expired action got removed")

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
