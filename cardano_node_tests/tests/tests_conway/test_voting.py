"""Tests for Conway governance voting functionality."""
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
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


def _save_gov_state(gov_state: tp.Dict[str, tp.Any], name_template: str) -> None:
    """Save governance state to a file."""
    with open(f"{name_template}_gov_state.json", "w", encoding="utf-8") as out_fp:
        json.dump(gov_state, out_fp, indent=2)


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
        * vote to approve the action
        * check that the action is ratified
        * check that the action is enacted
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Create an action

        deposit_amt = cluster.conway_genesis["govActionDeposit"]

        anchor_url = "http://www.const-action.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        constitution_url = "http://www.const-new.com"
        constitution_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        prev_action_rec = governance_utils.get_prev_action(
            cluster_obj=cluster, action_type=governance_utils.PrevGovActionIds.CONSTITUTION
        )

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

        # Vote & approve the action

        action_ix = prop_action["actionId"]["govActionIx"]

        votes_cc = [
            cluster.g_conway_governance.vote.create_committee(
                vote_name=f"{temp_template}_cc{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=clusterlib.Votes.YES,
                cc_hot_vkey_file=m.hot_vkey_file,
            )
            for i, m in enumerate(governance_data.cc_members, start=1)
        ]
        votes_drep = [
            cluster.g_conway_governance.vote.create_drep(
                vote_name=f"{temp_template}_drep{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=clusterlib.Votes.YES,
                drep_vkey_file=d.key_pair.vkey_file,
            )
            for i, d in enumerate(governance_data.dreps_reg, start=1)
        ]

        tx_files_vote = clusterlib.TxFiles(
            vote_files=[
                *[r.vote_file for r in votes_cc],
                *[r.vote_file for r in votes_drep],
            ],
            signing_key_files=[
                pool_user_lg.payment.skey_file,
                *[r.hot_skey_file for r in governance_data.cc_members],
                *[r.key_pair.skey_file for r in governance_data.dreps_reg],
            ],
        )

        # Make sure we have enough time to submit the votes in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        tx_output_vote = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_vote",
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
            gov_state=vote_gov_state, name_template=f"{temp_template}_vote_{_cur_epoch}"
        )
        prop_vote = governance_utils.lookup_proposal(
            gov_state=vote_gov_state, action_txid=action_txid
        )
        assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
        assert prop_vote["dRepVotes"], "No DRep votes"
        assert not prop_vote["stakePoolVotes"], "Unexpected stake pool votes"

        def _check_state(state: dict):
            anchor = state["constitution"]["anchor"]
            assert anchor["dataHash"] == constitution_hash, "Incorrect constitution anchor hash"
            assert anchor["url"] == constitution_url, "Incorrect constitution anchor URL"

        # Check ratification
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        rat_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}")
        next_rat_state = rat_gov_state["nextRatifyState"]
        _check_state(next_rat_state["nextEnactState"])
        assert next_rat_state["ratificationDelayed"], "Ratification not delayed"
        assert governance_utils.lookup_removed_actions(
            gov_state=rat_gov_state, action_txid=action_txid
        ), "Action not found in removed actions"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
        )
        _check_state(enact_gov_state["enactState"])

    @allure.link(helpers.get_vcs_link())
    def test_add_new_committee_member(
        self,
        cluster_lock_governance: governance_setup.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
        testfile_temp_dir: pl.Path,
        request: FixtureRequest,
    ):
        """Test adding new CC member.

        * create an "update committee" action
        * vote to approve the action
        * check that the action is ratified
        * check that the action is enacted
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Create an action

        cc_reg_record = governance_utils.get_cc_member_reg_record(
            cluster_obj=cluster,
            name_template=temp_template,
        )
        cc_member = clusterlib.CCMember(
            epoch=cluster.g_query.get_epoch() + 3,
            cold_vkey_file=cc_reg_record.cold_key_pair.vkey_file,
            cold_skey_file=cc_reg_record.cold_key_pair.skey_file,
            hot_vkey_file=cc_reg_record.hot_key_pair.vkey_file,
            hot_skey_file=cc_reg_record.hot_key_pair.skey_file,
        )

        deposit_amt = cluster.conway_genesis["govActionDeposit"]
        anchor_url = "http://www.cc-update.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"
        prev_action_rec = governance_utils.get_prev_action(
            cluster_obj=cluster, action_type=governance_utils.PrevGovActionIds.COMMITTEE
        )

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

        tx_files_action = clusterlib.TxFiles(
            certificate_files=[cc_reg_record.registration_cert],
            proposal_files=[update_action.action_file],
            signing_key_files=[
                pool_user_lg.payment.skey_file,
                cc_reg_record.cold_key_pair.skey_file,
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

        # Vote & approve the action

        action_ix = prop_action["actionId"]["govActionIx"]

        votes_drep = [
            cluster.g_conway_governance.vote.create_drep(
                vote_name=f"{temp_template}_drep{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=clusterlib.Votes.YES,
                drep_vkey_file=d.key_pair.vkey_file,
            )
            for i, d in enumerate(governance_data.dreps_reg, start=1)
        ]
        votes_spo = [
            cluster.g_conway_governance.vote.create_spo(
                vote_name=f"{temp_template}_pool{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=clusterlib.Votes.YES,
                cold_vkey_file=p.vkey_file,
            )
            for i, p in enumerate(governance_data.pools_cold, start=1)
        ]

        tx_files_vote = clusterlib.TxFiles(
            vote_files=[
                *[r.vote_file for r in votes_drep],
                *[r.vote_file for r in votes_spo],
            ],
            signing_key_files=[
                pool_user_lg.payment.skey_file,
                *[r.skey_file for r in governance_data.pools_cold],
                *[r.key_pair.skey_file for r in governance_data.dreps_reg],
            ],
        )

        # Make sure we have enough time to submit the votes in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        tx_output_vote = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_vote",
            src_address=pool_user_lg.payment.address,
            use_build_cmd=True,
            tx_files=tx_files_vote,
        )

        # Resign the CC member so it doesn't affect voting
        def _resign():
            with helpers.change_cwd(testfile_temp_dir):
                res_cert = cluster.g_conway_governance.committee.gen_cold_key_resignation_cert(
                    key_name=temp_template,
                    cold_vkey_file=cc_reg_record.cold_key_pair.vkey_file,
                    resignation_metadata_url="http://www.cc-resign.com",
                    resignation_metadata_hash="5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d",
                )

                tx_files_res = clusterlib.TxFiles(
                    certificate_files=[res_cert],
                    signing_key_files=[
                        pool_user_lg.payment.skey_file,
                        cc_reg_record.cold_key_pair.skey_file,
                    ],
                )

                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_res",
                    src_address=pool_user_lg.payment.address,
                    use_build_cmd=True,
                    tx_files=tx_files_res,
                )

        request.addfinalizer(_resign)

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
            gov_state=vote_gov_state, name_template=f"{temp_template}_vote_{_cur_epoch}"
        )
        prop_vote = governance_utils.lookup_proposal(
            gov_state=vote_gov_state, action_txid=action_txid
        )
        assert not prop_vote["committeeVotes"], "Unexpected committee votes"
        assert prop_vote["dRepVotes"], "No DRep votes"
        assert prop_vote["stakePoolVotes"], "No stake pool votes"

        def _check_state(state: dict):
            cc_member_val = state["committee"]["members"].get(f"keyHash-{cc_reg_record.key_hash}")
            assert cc_member_val, "New committee member not found"
            assert cc_member_val == cc_member.epoch

        # Check ratification
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        rat_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}")
        next_rat_state = rat_gov_state["nextRatifyState"]
        _check_state(next_rat_state["nextEnactState"])
        assert next_rat_state["ratificationDelayed"], "Ratification not delayed"
        assert governance_utils.lookup_removed_actions(
            gov_state=rat_gov_state, action_txid=action_txid
        ), "Action not found in removed actions"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
        )
        _check_state(enact_gov_state["enactState"])

    @allure.link(helpers.get_vcs_link())
    def test_pparam_update(
        self,
        cluster_lock_governance: governance_setup.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test enactment of protocol parameter update.

        * submit a "protocol parameters update" action
        * vote to approve the action
        * check that the action is ratified
        * check that the action is enacted
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Create an action

        deposit_amt = cluster.conway_genesis["govActionDeposit"]

        anchor_url = "http://www.pparam-action.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        prev_action_rec = governance_utils.get_prev_action(
            cluster_obj=cluster, action_type=governance_utils.PrevGovActionIds.PPARAM_UPDATE
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

        # Vote & approve the action

        action_ix = prop_action["actionId"]["govActionIx"]

        votes_cc = [
            cluster.g_conway_governance.vote.create_committee(
                vote_name=f"{temp_template}_cc{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=clusterlib.Votes.YES,
                cc_hot_vkey_file=m.hot_vkey_file,
            )
            for i, m in enumerate(governance_data.cc_members, start=1)
        ]
        votes_drep = [
            cluster.g_conway_governance.vote.create_drep(
                vote_name=f"{temp_template}_drep{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=clusterlib.Votes.YES,
                drep_vkey_file=d.key_pair.vkey_file,
            )
            for i, d in enumerate(governance_data.dreps_reg, start=1)
        ]

        tx_files_vote = clusterlib.TxFiles(
            vote_files=[
                *[r.vote_file for r in votes_cc],
                *[r.vote_file for r in votes_drep],
            ],
            signing_key_files=[
                pool_user_lg.payment.skey_file,
                *[r.hot_skey_file for r in governance_data.cc_members],
                *[r.key_pair.skey_file for r in governance_data.dreps_reg],
            ],
        )

        # Make sure we have enough time to submit the votes in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        tx_output_vote = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_vote",
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
            gov_state=vote_gov_state, name_template=f"{temp_template}_vote_{_cur_epoch}"
        )
        prop_vote = governance_utils.lookup_proposal(
            gov_state=vote_gov_state, action_txid=action_txid
        )
        assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
        assert prop_vote["dRepVotes"], "No DRep votes"
        assert not prop_vote["stakePoolVotes"], "Unexpected stake pool votes"

        def _check_state(state: dict):
            pparams = state["curPParams"]
            clusterlib_utils.check_updated_params(
                update_proposals=update_proposals, protocol_params=pparams
            )

        # Check ratification
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        rat_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}")
        next_rat_state = rat_gov_state["nextRatifyState"]
        _check_state(next_rat_state["nextEnactState"])
        assert not next_rat_state["ratificationDelayed"], "Ratification is delayed unexpectedly"
        assert governance_utils.lookup_removed_actions(
            gov_state=rat_gov_state, action_txid=action_txid
        ), "Action not found in removed actions"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
        )
        _check_state(enact_gov_state["enactState"])

    @allure.link(helpers.get_vcs_link())
    def test_treasury_withdrawals(
        self,
        cluster_use_governance: governance_setup.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test enactment of multiple treasury withdrawals in single epoch.

        Use `transaction build` for building the transactions.
        When available, use cardano-submit-api for votes submission.

        * submit multiple "treasury withdrawal" actions
        * vote to approve the actions
        * check that the actions are ratified
        * check that the action are enacted
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_use_governance
        temp_template = common.get_test_id(cluster)
        actions_num = 3

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

        anchor_url = "http://www.withdrawal-action.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        withdrawal_actions = []
        for a in range(actions_num):
            anchor_url = f"http://www.withdrawal-action{a}.com"
            anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

            withdrawal_actions.append(
                cluster.g_conway_governance.action.create_treasury_withdrawal(
                    action_name=f"{temp_template}_{a}",
                    transfer_amt=transfer_amt,
                    deposit_amt=action_deposit_amt,
                    anchor_url=anchor_url,
                    anchor_data_hash=anchor_data_hash,
                    funds_receiving_stake_vkey_file=recv_stake_addr_rec.vkey_file,
                    deposit_return_stake_vkey_file=pool_user_ug.stake.vkey_file,
                )
            )

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

            # Vote & approve the actions

            votes_cc.extend(
                [
                    cluster.g_conway_governance.vote.create_committee(
                        vote_name=f"{temp_template}_{action_ix}_cc{i}",
                        action_txid=action_txid,
                        action_ix=action_ix,
                        vote=clusterlib.Votes.YES,
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
                        vote=clusterlib.Votes.YES,
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

        tx_output_vote = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_vote",
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
            gov_state=vote_gov_state, name_template=f"{temp_template}_vote_{_cur_epoch}"
        )

        for action_ix in range(actions_num):
            prop_vote = governance_utils.lookup_proposal(
                gov_state=vote_gov_state, action_txid=action_txid, action_ix=action_ix
            )
            assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
            assert prop_vote["dRepVotes"], "No DRep votes"
            assert not prop_vote["stakePoolVotes"], "Unexpected stake pool votes"

        # Check ratification
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        rat_gov_state = cluster.g_conway_governance.query.gov_state()
        _save_gov_state(gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}")
        assert rat_gov_state["nextRatifyState"]["removedGovActions"], "No removed actions"
        assert governance_utils.lookup_removed_actions(
            gov_state=rat_gov_state, action_txid=action_txid
        ), "Action not found in removed actions"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        assert (
            cluster.g_query.get_stake_addr_info(recv_stake_addr_rec.address).reward_account_balance
            == transfer_amt * actions_num
        ), "Incorrect reward account balance"

        # Check action view
        recv_addr_vkey_hash = cluster.g_stake_address.get_stake_vkey_hash(
            stake_vkey_file=recv_stake_addr_rec.vkey_file
        )
        return_addr_vkey_hash = cluster.g_stake_address.get_stake_vkey_hash(
            stake_vkey_file=pool_user_ug.stake.vkey_file
        )

        action_to_check = withdrawal_actions[0]
        governance_utils.check_action_view(
            cluster_obj=cluster,
            action_tag=governance_utils.ActionTags.TREASURY_WITHDRAWALS,
            action_file=action_to_check.action_file,
            anchor_url=action_to_check.anchor_url,
            anchor_data_hash=action_to_check.anchor_data_hash,
            deposit_amt=action_to_check.deposit_amt,
            return_addr_vkey_hash=return_addr_vkey_hash,
            recv_addr_vkey_hash=recv_addr_vkey_hash,
            transfer_amt=action_to_check.transfer_amt,
        )


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
        cluster, governance_data = cluster_use_governance
        temp_template = common.get_test_id(cluster)
        action_deposit_amt = cluster.conway_genesis["govActionDeposit"]

        # Create an action

        rand_str = helpers.get_rand_str(4)
        anchor_url = f"http://www.info-action-{rand_str}.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        info_action = cluster.g_conway_governance.action.create_info(
            action_name=temp_template,
            deposit_amt=action_deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            deposit_return_stake_vkey_file=pool_user_ug.stake.vkey_file,
        )

        tx_files_action = clusterlib.TxFiles(
            proposal_files=[info_action.action_file],
            signing_key_files=[pool_user_ug.payment.skey_file],
        )

        # Make sure we have enough time to submit the proposal in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action",
            src_address=pool_user_ug.payment.address,
            use_build_cmd=True,
            tx_files=tx_files_action,
        )

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
        action_gov_state = cluster.g_conway_governance.query.gov_state()
        _cur_epoch = cluster.g_query.get_epoch()
        _save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{_cur_epoch}"
        )
        prop_action = governance_utils.lookup_proposal(
            gov_state=action_gov_state, action_txid=action_txid
        )
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

        tx_output_vote = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_vote",
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
            gov_state=vote_gov_state, name_template=f"{temp_template}_vote_{_cur_epoch}"
        )
        prop_vote = governance_utils.lookup_proposal(
            gov_state=vote_gov_state, action_txid=action_txid
        )
        assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
        assert prop_vote["dRepVotes"], "No DRep votes"
        assert prop_vote["stakePoolVotes"], "No stake pool votes"

        # Check action view
        return_addr_vkey_hash = cluster.g_stake_address.get_stake_vkey_hash(
            stake_vkey_file=pool_user_ug.stake.vkey_file
        )
        governance_utils.check_action_view(
            cluster_obj=cluster,
            action_tag=governance_utils.ActionTags.INFO_ACTION,
            action_file=info_action.action_file,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            deposit_amt=action_deposit_amt,
            return_addr_vkey_hash=return_addr_vkey_hash,
        )
