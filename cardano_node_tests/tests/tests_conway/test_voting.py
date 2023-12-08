"""Tests for Conway governance voting functionality."""
import logging
import pathlib as pl

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests.tests_conway import gov_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


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
    cluster_lock_governance: gov_common.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "lock governance"."""
    cluster, __ = cluster_lock_governance
    key = cluster_management.get_fixture_line_str()
    return get_pool_user(cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key)


@pytest.fixture
def pool_user_ug(
    cluster_manager: cluster_management.ClusterManager,
    cluster_use_governance: gov_common.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "use governance"."""
    cluster, __ = cluster_use_governance
    key = cluster_management.get_fixture_line_str()
    return get_pool_user(cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key)


class TestVoting:
    """Tests for voting."""

    @allure.link(helpers.get_vcs_link())
    def test_enact_constitution(
        self,
        cluster_lock_governance: gov_common.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test enactment of change of constitution.

        * submit a "create constitution" action
        * vote to approve the action
        * check that the action is ratified
        * check that the action is enacted
        """
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Create an action

        deposit_amt = cluster.conway_genesis["govActionDeposit"]

        anchor_url = "http://www.const-action.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        constitution_url = "http://www.const-new.com"
        constitution_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        prev_action_rec = gov_common.get_prev_action(
            cluster_obj=cluster, action_type=gov_common.PrevGovActionIds.CONSTITUTION
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
            proposal_files=[constitution_action],
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
        prop_action = gov_common.lookup_proposal(cluster_obj=cluster, action_txid=action_txid)
        assert prop_action, "Create constitution action not found"
        assert (
            prop_action["action"]["tag"] == gov_common.ActionTags.NEW_CONSTITUTION.value
        ), "Incorrect action tag"

        # Vote & approve the action

        action_ix = prop_action["actionId"]["govActionIx"]

        vote_files_cc = [
            cluster.g_conway_governance.vote.create(
                vote_name=f"{temp_template}_cc{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote_yes=True,
                cc_hot_vkey_file=m.hot_vkey_file,
            )
            for i, m in enumerate(governance_data.cc_members, start=1)
        ]
        vote_files_drep = [
            cluster.g_conway_governance.vote.create(
                vote_name=f"{temp_template}_drep{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote_yes=True,
                drep_vkey_file=d.key_pair.vkey_file,
            )
            for i, d in enumerate(governance_data.dreps_reg, start=1)
        ]

        tx_files_vote = clusterlib.TxFiles(
            vote_files=[*vote_files_cc, *vote_files_drep],
            signing_key_files=[
                pool_user_lg.payment.skey_file,
                *[r.hot_skey_file for r in governance_data.cc_members],
                *[r.key_pair.skey_file for r in governance_data.dreps_reg],
            ],
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

        prop_vote = gov_common.lookup_proposal(cluster_obj=cluster, action_txid=action_txid)
        assert prop_vote["committeeVotes"], "No committee votes"
        assert prop_vote["dRepVotes"], "No DRep votes"
        assert not prop_vote["stakePoolVotes"], "Unexpected stake pool votes"

        def _check_state(state: dict):
            anchor = state["constitution"]["anchor"]
            assert anchor["dataHash"] == constitution_hash, "Incorrect constitution anchor hash"
            assert anchor["url"] == constitution_url, "Incorrect constitution anchor URL"

        # Check ratification
        cluster.wait_for_new_epoch(padding_seconds=5)
        next_rat_state = cluster.g_conway_governance.query.gov_state()["nextRatifyState"]
        _check_state(next_rat_state["nextEnactState"])
        assert next_rat_state["ratificationDelayed"], "Ratification not delayed"
        assert next_rat_state["removedGovActions"], "No removed actions"

        # Check enactment
        cluster.wait_for_new_epoch(padding_seconds=5)
        enact_state = cluster.g_conway_governance.query.gov_state()["enactState"]
        _check_state(enact_state)

    @allure.link(helpers.get_vcs_link())
    def test_add_new_committee_member(
        self,
        cluster_lock_governance: gov_common.GovClusterT,
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
        # pylint: disable=too-many-locals
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Create an action

        cc_reg_record = clusterlib_utils.get_cc_member_reg_record(
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
        prev_action_rec = gov_common.get_prev_action(
            cluster_obj=cluster, action_type=gov_common.PrevGovActionIds.COMMITTEE
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
            proposal_files=[update_action],
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
        prop_action = gov_common.lookup_proposal(cluster_obj=cluster, action_txid=action_txid)
        assert prop_action, "Update committee action not found"
        assert (
            prop_action["action"]["tag"] == gov_common.ActionTags.UPDATE_COMMITTEE.value
        ), "Incorrect action tag"

        # Vote & approve the action

        action_ix = prop_action["actionId"]["govActionIx"]

        vote_files_drep = [
            cluster.g_conway_governance.vote.create(
                vote_name=f"{temp_template}_drep{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote_yes=True,
                drep_vkey_file=d.key_pair.vkey_file,
            )
            for i, d in enumerate(governance_data.dreps_reg, start=1)
        ]
        vote_files_pool = [
            cluster.g_conway_governance.vote.create(
                vote_name=f"{temp_template}_pool{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote_yes=True,
                cold_vkey_file=p.vkey_file,
            )
            for i, p in enumerate(governance_data.pools_cold, start=1)
        ]

        tx_files_vote = clusterlib.TxFiles(
            vote_files=[*vote_files_drep, *vote_files_pool],
            signing_key_files=[
                pool_user_lg.payment.skey_file,
                *[r.skey_file for r in governance_data.pools_cold],
                *[r.key_pair.skey_file for r in governance_data.dreps_reg],
            ],
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

        prop_vote = gov_common.lookup_proposal(cluster_obj=cluster, action_txid=action_txid)
        assert not prop_vote["committeeVotes"], "Unexpected committee votes"
        assert prop_vote["dRepVotes"], "No DRep votes"
        assert prop_vote["stakePoolVotes"], "No stake pool votes"

        def _check_state(state: dict):
            cc_member_val = state["committee"]["members"].get(f"keyHash-{cc_reg_record.key_hash}")
            assert cc_member_val, "New committee member not found"
            assert cc_member_val == cc_member.epoch

        # Check ratification
        cluster.wait_for_new_epoch(padding_seconds=5)
        next_rat_state = cluster.g_conway_governance.query.gov_state()["nextRatifyState"]
        _check_state(next_rat_state["nextEnactState"])
        assert next_rat_state["ratificationDelayed"], "Ratification not delayed"
        assert next_rat_state["removedGovActions"], "No removed actions"

        # Check enactment
        cluster.wait_for_new_epoch(padding_seconds=5)
        enact_state = cluster.g_conway_governance.query.gov_state()["enactState"]
        _check_state(enact_state)
