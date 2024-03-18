"""Tests for Conway governance info."""

# pylint: disable=expression-not-assigned
import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests.tests_conway import conway_common
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


class TestInfo:
    """Tests for info."""

    @allure.link(helpers.get_vcs_link())
    def test_info(
        self,
        cluster_use_governance: governance_setup.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test voting on info action.

        * submit an "info" action
        * vote on the action
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
        req_cip31a = requirements.Req(id="intCIP031a-03", group=requirements.GroupsKnown.CHANG_US)
        req_cip53 = requirements.Req(id="CIP053", group=requirements.GroupsKnown.CHANG_US)
        req_cip59 = requirements.Req(id="CIP059", group=requirements.GroupsKnown.CHANG_US)

        # Create an action

        rand_str = helpers.get_rand_str(4)
        anchor_url = f"http://www.info-action-{rand_str}.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (req_cli16, req_cip31a)]
        info_action = cluster.g_conway_governance.action.create_info(
            action_name=temp_template,
            deposit_amt=action_deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            deposit_return_stake_vkey_file=pool_user_ug.stake.vkey_file,
        )
        [r.success() for r in (req_cli16, req_cip31a)]

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

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (req_cli21, req_cip53, req_cip59)]
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
        [r.success() for r in (req_cli21, req_cip59)]

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
            gov_state=vote_gov_state, action_txid=action_txid, action_ix=action_ix
        )
        assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
        assert prop_vote["dRepVotes"], "No DRep votes"
        assert prop_vote["stakePoolVotes"], "No stake pool votes"

        # Check that the Info action cannot be ratified
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        approved_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=approved_gov_state, name_template=f"{temp_template}_approved_{_cur_epoch}"
        )
        rat_info_action = governance_utils.lookup_ratified_actions(
            gov_state=approved_gov_state,
            action_txid=action_txid,
            action_ix=action_ix,
        )
        assert not rat_info_action, "Action found in ratified actions"
        req_cip53.success()

        # Check action view
        governance_utils.check_action_view(cluster_obj=cluster, action_data=info_action)

        # Check vote view
        req_cli22.start(url=helpers.get_vcs_link())
        if votes_cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=votes_cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=votes_drep[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=votes_spo[0])
        req_cli22.success()
