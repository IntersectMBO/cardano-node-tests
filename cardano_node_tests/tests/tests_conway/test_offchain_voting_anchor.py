"""Tests for Conway governance info."""

import json
import logging
import pathlib as pl
from dataclasses import dataclass
from typing import Any

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent.parent / "data"

pytestmark = [
    pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.CONWAY,
        reason="runs only with Tx era >= Conway",
    ),
    pytest.mark.dbsync_config,
]


@pytest.fixture
def pool_user_ug(
    cluster_manager: cluster_management.ClusterManager,
    cluster_use_governance: governance_utils.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "use governance"."""
    cluster, __ = cluster_use_governance
    key = helpers.get_current_line_str()
    name_template = common.get_test_id(cluster)
    return common.get_registered_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        caching_key=key,
    )


@dataclass
class AnchorConfig:
    """Configuration for anchor data testing."""
    url: str
    data_hash: str
    json_file: pl.Path | None = None
    expected_json: dict[str, Any] | None = None
    expected_bytes_length: int = 0
    expected_bytes_start: str = ""
    expected_bytes_end: str = ""
    expected_is_valid: bool | None = None


class TestInfo:
    """Tests for info."""

    NON_CONFORMANT_ANCHOR_FILE = DATA_DIR / "governance_action_anchor_cip_100_non_conformant.json"
    INVALID_ANCHOR_DATA_HASH = "9b6b2ee5690d74888385af01465d1f70b4277a8264e5ce79faca270889c7b070"

    def _test_voting_anchor(
        self,
        cluster_use_governance: governance_utils.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
        anchor_config: AnchorConfig,
    ):
        """Common test logic for voting on info action with different anchor configurations."""
        cluster, governance_data = cluster_use_governance
        temp_template = common.get_test_id(cluster)
        action_deposit_amt = cluster.g_query.get_gov_action_deposit()

        # Get initial return account balance
        init_return_account_balance = cluster.g_query.get_stake_addr_info(
            pool_user_ug.stake.address
        ).reward_account_balance

        # Create an action
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cli016, reqc.cip031a_03, reqc.cip054_06)]
        info_action = cluster.g_governance.action.create_info(
            action_name=temp_template,
            deposit_amt=action_deposit_amt,
            anchor_url=anchor_config.url,
            anchor_data_hash=anchor_config.data_hash,
            deposit_return_stake_vkey_file=pool_user_ug.stake.vkey_file,
        )
        [r.success() for r in (reqc.cli016, reqc.cip031a_03, reqc.cip054_06)]

        tx_files_action = clusterlib.TxFiles(
            proposal_files=[info_action.action_file],
            signing_key_files=[pool_user_ug.payment.skey_file],
        )

        # Make sure we have enough time to submit the proposal in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # Get epoch where the action was submitted
        action_epoch = cluster.g_query.get_epoch()

        reqc.cli023.start(url=helpers.get_vcs_link())
        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action",
            src_address=pool_user_ug.payment.address,
            build_method=clusterlib_utils.BuildMethods.BUILD,
            tx_files=tx_files_action,
        )
        reqc.cli023.success()

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
        reqc.cli031.start(url=helpers.get_vcs_link())
        action_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{action_epoch}"
        )
        prop_action = governance_utils.lookup_proposal(
            gov_state=action_gov_state, action_txid=action_txid
        )
        reqc.cli031.success()
        assert prop_action, "Info action not found"
        assert (
            prop_action["proposalProcedure"]["govAction"]["tag"]
            == governance_utils.ActionTags.INFO_ACTION.value
        ), "Incorrect action tag"

        action_ix = prop_action["actionId"]["govActionIx"]
        prop_query_action = cluster.g_query.get_proposals(
            action_txid=action_txid, action_ix=action_ix
        )

        assert cluster.g_query.get_epoch() == action_epoch, (
            "Epoch changed and it would affect other checks"
        )

        # Vote
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cli021, reqc.cip053, reqc.cip059)]
        votes_cc = [
            cluster.g_governance.vote.create_committee(
                vote_name=f"{temp_template}_cc{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=clusterlib.Votes.YES,
                cc_hot_vkey_file=m.hot_keys.hot_vkey_file,
            )
            for i, m in enumerate(governance_data.cc_key_members, start=1)
        ]
        votes_drep = [
            cluster.g_governance.vote.create_drep(
                vote_name=f"{temp_template}_drep{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=clusterlib.Votes.YES,
                drep_vkey_file=d.key_pair.vkey_file,
            )
            for i, d in enumerate(governance_data.dreps_reg, start=1)
        ]
        votes_spo = [
            cluster.g_governance.vote.create_spo(
                vote_name=f"{temp_template}_pool{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=clusterlib.Votes.YES,
                cold_vkey_file=p.vkey_file,
            )
            for i, p in enumerate(governance_data.pools_cold, start=1)
        ]
        [r.success() for r in (reqc.cli021, reqc.cip059)]

        votes: list[governance_utils.VotesAllT] = [*votes_cc, *votes_drep, *votes_spo]
        vote_keys = [
            *[r.hot_keys.hot_skey_file for r in governance_data.cc_key_members],
            *[r.key_pair.skey_file for r in governance_data.dreps_reg],
            *[r.skey_file for r in governance_data.pools_cold],
        ]

        # Make sure we have enough time to submit the votes in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        vote_epoch = cluster.g_query.get_epoch()

        reqc.cli024.start(url=helpers.get_vcs_link())
        vote_tx_output = conway_common.submit_vote(
            cluster_obj=cluster,
            name_template=temp_template,
            payment_addr=pool_user_ug.payment,
            votes=votes,
            keys=vote_keys,
            build_method=clusterlib_utils.BuildMethods.BUILD,
        )
        reqc.cli024.success()

        assert cluster.g_query.get_epoch() == vote_epoch, (
            "Epoch changed and it would affect other checks"
        )

        vote_txid = cluster.g_transaction.get_txid(tx_body_file=vote_tx_output.out_file)

        vote_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=vote_gov_state, name_template=f"{temp_template}_vote_{vote_epoch}"
        )
        prop_vote = governance_utils.lookup_proposal(
            gov_state=vote_gov_state, action_txid=action_txid, action_ix=action_ix
        )
        assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
        assert prop_vote["dRepVotes"], "No DRep votes"
        assert prop_vote["stakePoolVotes"], "No stake pool votes"

        # Ensure the proposal is being queried in an epoch where it is eligible for ratification
        cluster.wait_for_epoch(epoch_no=action_epoch + 1, padding_seconds=5)
        prop_query_rat = cluster.g_query.get_proposals(action_txid=action_txid, action_ix=action_ix)
        all_proposals = cluster.g_query.get_proposals()

        # Check that the Info action cannot be ratified
        approved_epoch = cluster.wait_for_epoch(epoch_no=vote_epoch + 1, padding_seconds=5)
        approved_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=approved_gov_state, name_template=f"{temp_template}_approved_{approved_epoch}"
        )

        rat_state = cluster.g_query.get_ratify_state()
        rat_action = governance_utils.lookup_ratified_actions(
            state=rat_state,
            action_txid=action_txid,
        )
        assert not rat_action, "Action found in ratified actions"
        reqc.cip053.success()

        # Check ratification delay flag
        reqc.cip038_05.start(url=helpers.get_vcs_link())
        assert not rat_state["ratificationDelayed"], "Ratification is delayed unexpectedly"
        reqc.cip038_05.success()

        # Check deposit is returned
        reqc.cip034ex.start(url=helpers.get_vcs_link())

        # First wait for gov action to expire according to gov action lifetime
        epochs_to_expiration = action_epoch + cluster.conway_genesis["govActionLifetime"] + 1
        expire_epoch = cluster.wait_for_epoch(epoch_no=epochs_to_expiration, padding_seconds=5)
        expire_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=expire_gov_state, name_template=f"{temp_template}_expire_{expire_epoch}"
        )
        expire_return_account_balance = cluster.g_query.get_stake_addr_info(
            pool_user_ug.stake.address
        ).reward_account_balance
        assert expire_return_account_balance == init_return_account_balance, (
            f"Incorrect return account balance {expire_return_account_balance}"
        )

        # Check that the proposals were removed and the actions deposits were returned
        rem_epoch = cluster.wait_for_epoch(epoch_no=epochs_to_expiration + 1, padding_seconds=5)
        rem_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=rem_gov_state, name_template=f"{temp_template}_rem_{rem_epoch}"
        )

        deposit_returned = cluster.g_query.get_stake_addr_info(
            pool_user_ug.stake.address
        ).reward_account_balance
        assert deposit_returned == init_return_account_balance + action_deposit_amt, (
            "Incorrect return account balance"
        )
        reqc.cip034ex.success()

        assert not governance_utils.lookup_proposal(
            gov_state=rem_gov_state, action_txid=action_txid, action_ix=action_ix
        ), f"Action {action_txid}#{action_ix} not removed from proposals"

        # Check action view
        governance_utils.check_action_view(cluster_obj=cluster, action_data=info_action)

        # Check vote view
        reqc.cli022.start(url=helpers.get_vcs_link())
        if votes_cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=votes_cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=votes_drep[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=votes_spo[0])
        reqc.cli022.success()

        # Check `query proposals`
        assert not prop_query_action, (
            f"Expected no proposals in action creation epoch, but found: {prop_query_action}"
        )
        assert prop_query_rat, "No proposals found in ratification eligible epoch"
        assert all_proposals, "No proposals found in all proposals query"

        # Check dbsync
        reqc.db013.start(url=helpers.get_vcs_link())
        dbsync_utils.check_votes(
            votes=governance_utils.VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo),
            txhash=vote_txid,
        )
        reqc.db013.success()
        
        # Check anchor-specific data
        self._check_anchor_data(anchor_config)

    def _check_anchor_data(self, anchor_config: AnchorConfig):
        """Check dbsync data for anchor configuration."""
        db_action_data = dbsync_utils.get_action_data(data_hash=anchor_config.data_hash)
        if db_action_data is not None:
            if anchor_config.expected_json:
                assert db_action_data.json == anchor_config.expected_json
            assert db_action_data.hash == anchor_config.data_hash
            
            if anchor_config.expected_bytes_length:
                assert len(db_action_data.bytes) == anchor_config.expected_bytes_length
            if anchor_config.expected_bytes_start:
                assert db_action_data.bytes.startswith(anchor_config.expected_bytes_start)
            if anchor_config.expected_bytes_end:
                assert db_action_data.bytes.endswith(anchor_config.expected_bytes_end)
            
            assert db_action_data.warning is None
            assert db_action_data.language == ''
            assert db_action_data.comment is None
            
            if anchor_config.expected_is_valid is not None:
                assert db_action_data.is_valid == anchor_config.expected_is_valid

            assert db_action_data.authors == []
            assert db_action_data.references == []
            assert db_action_data.gov_action_data == {}
            assert db_action_data.external_updates == []

            assert db_action_data.voting_anchor["url"] == anchor_config.url
            assert db_action_data.voting_anchor["data_hash"] == anchor_config.data_hash
            assert db_action_data.voting_anchor["type"] == "gov_action"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.dbsync
    @pytest.mark.upgrade_step1
    def test_invalid_voting_anchor_json(
        self,
        cluster_use_governance: governance_utils.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test voting on info action with invalid JSON anchor."""
        anchor_config = AnchorConfig(
            url="https://tinyurl.com/muy62k9v",
            data_hash=self.INVALID_ANCHOR_DATA_HASH,
            expected_json={
                "error": "Content is not valid JSON. See bytes column for raw data.", 
                "parse_error": 'Unexpected ",\\n      }\\n   ],\\n   \\"body\\":{\\n  ", expecting key literal'
            },
            expected_bytes_length=4946,
            expected_bytes_start='7b0a202020227469746c65223a22434950',
            expected_bytes_end='7d0a7d',
            expected_is_valid=None,
        )
        self._test_voting_anchor(cluster_use_governance, pool_user_ug, anchor_config)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.dbsync
    @pytest.mark.upgrade_step1
    def test_valid_voting_anchor_json_not_conforming_to_CIP_100(
        self,
        cluster_use_governance: governance_utils.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test voting on info action with valid JSON that doesn't conform to CIP-100."""
        # Load expected JSON from file
        with open(self.NON_CONFORMANT_ANCHOR_FILE, encoding="utf-8") as anchor_fp:
            json_anchor_file = json.load(anchor_fp)

        cluster, __ = cluster_use_governance
        anchor_data_hash = cluster.g_governance.get_anchor_data_hash(
            file_text=self.NON_CONFORMANT_ANCHOR_FILE
        )

        anchor_config = AnchorConfig(
            url="https://tinyurl.com/7nz8964r",
            data_hash=anchor_data_hash,
            expected_json=json_anchor_file,
            expected_bytes_length=5468,
            expected_bytes_start='7b0a20202020227469746c65223a2022434950',
            expected_bytes_end='207d0a7d',
            expected_is_valid=False,
        )
        self._test_voting_anchor(cluster_use_governance, pool_user_ug, anchor_config)