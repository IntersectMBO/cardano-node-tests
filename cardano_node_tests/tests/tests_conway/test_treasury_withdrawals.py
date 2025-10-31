"""Tests for Conway governance treasury withdrawals."""

import logging

import allure
import pytest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
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


@pytest.fixture
def cluster_use_governance_lock_treasury(
    cluster_manager: cluster_management.ClusterManager,
) -> governance_utils.GovClusterT:
    """Mark governance as "in use", treasury pot as locked.

    Return instance of `clusterlib.ClusterLib`.
    """
    cluster_obj = cluster_manager.get(
        lock_resources=[cluster_management.Resources.TREASURY],
        use_resources=[
            cluster_management.Resources.COMMITTEE,
            cluster_management.Resources.DREPS,
            *cluster_management.Resources.ALL_POOLS,
        ],
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster_obj
    )
    governance_utils.wait_delayed_ratification(cluster_obj=cluster_obj)
    return cluster_obj, governance_data


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
        amount=500_000_000,
    )


@pytest.fixture
def pool_user_ug_treasury(
    cluster_manager: cluster_management.ClusterManager,
    cluster_use_governance_lock_treasury: governance_utils.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "use governance, lock treasury"."""
    cluster, __ = cluster_use_governance_lock_treasury
    key = helpers.get_current_line_str()
    name_template = common.get_test_id(cluster)
    return common.get_registered_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        caching_key=key,
        amount=500_000_000,
    )


class TestTreasuryWithdrawals:
    """Tests for treasury withdrawals."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.long
    @pytest.mark.upgrade_step3
    def test_enact_treasury_withdrawals(
        self,
        cluster_use_governance_lock_treasury: governance_utils.GovClusterT,
        pool_user_ug_treasury: clusterlib.PoolUser,
    ):
        """Test enactment of multiple treasury withdrawals in single epoch.

        Use `transaction build` for building the transactions.
        When available, use cardano-submit-api for votes submission.

        * submit multiple "treasury withdrawal" actions
        * check that SPOs cannot vote on a "treasury withdrawal" action
        * vote to approve the actions
        * check that the actions are ratified
        * try to disapprove the ratified action, this shouldn't have any effect
        * check that the action are enacted
        * check that it's not possible to vote on enacted action
        """
        cluster, governance_data = cluster_use_governance_lock_treasury
        temp_template = common.get_test_id(cluster)
        actions_num = 3

        pparams = cluster.g_query.get_protocol_params()
        stake_deposit_amt = cluster.g_query.get_address_deposit(pparams=pparams)
        action_deposit_amt = cluster.g_query.get_gov_action_deposit(pparams=pparams)

        # Create stake address and registration certificate
        recv_stake_addr_rec = cluster.g_stake_address.gen_stake_addr_and_keys(
            name=f"{temp_template}_receive"
        )
        recv_stake_addr_reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_receive",
            deposit_amt=stake_deposit_amt,
            stake_vkey_file=recv_stake_addr_rec.vkey_file,
        )

        # Create an action and register stake address
        transfer_amts = list(range(10_000_000_000, 10_000_000_000 + actions_num))

        anchor_data = governance_utils.get_default_anchor_data()

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cli015, reqc.cip031a_06, reqc.cip031f, reqc.cip054_05)]

        withdrawal_actions = [
            cluster.g_governance.action.create_treasury_withdrawal(
                action_name=f"{temp_template}_{a}",
                transfer_amt=a,
                deposit_amt=action_deposit_amt,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                funds_receiving_stake_vkey_file=recv_stake_addr_rec.vkey_file,
                deposit_return_stake_vkey_file=pool_user_ug_treasury.stake.vkey_file,
            )
            for i, a in enumerate(transfer_amts, start=1)
        ]
        [r.success() for r in (reqc.cli015, reqc.cip031a_06, reqc.cip031f, reqc.cip054_05)]

        # Check that duplicated proposals are discarded when building the transaction.
        # This one is the same as one that already exists in `withdrawal_actions`,
        # and will not be taken into account.
        withdrawal_actions.append(
            cluster.g_governance.action.create_treasury_withdrawal(
                action_name=f"{temp_template}_duplicated",
                transfer_amt=transfer_amts[0],
                deposit_amt=action_deposit_amt,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                funds_receiving_stake_vkey_file=recv_stake_addr_rec.vkey_file,
                deposit_return_stake_vkey_file=pool_user_ug_treasury.stake.vkey_file,
            )
        )

        tx_files_action = clusterlib.TxFiles(
            certificate_files=[recv_stake_addr_reg_cert],
            proposal_files=[w.action_file for w in withdrawal_actions],
            signing_key_files=[
                pool_user_ug_treasury.payment.skey_file,
                recv_stake_addr_rec.skey_file,
            ],
        )

        actions_deposit_combined = action_deposit_amt * actions_num

        if conway_common.is_in_bootstrap(cluster_obj=cluster):
            reqc.cip026_03.start(url=helpers.get_vcs_link())
            with pytest.raises(clusterlib.CLIError) as excinfo:
                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_action_bootstrap",
                    src_address=pool_user_ug_treasury.payment.address,
                    build_method=clusterlib_utils.BuildMethods.BUILD_RAW,
                    tx_files=tx_files_action,
                    deposit=actions_deposit_combined + stake_deposit_amt,
                )
            err_str = str(excinfo.value)
            assert "(DisallowedProposalDuringBootstrap" in err_str, err_str
            reqc.cip026_03.success()
            return

        if VERSIONS.cli > version.parse("10.1.1.0"):
            with pytest.raises(clusterlib.CLIError) as excinfo:
                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_action_build",
                    src_address=pool_user_ug_treasury.payment.address,
                    build_method=clusterlib_utils.BuildMethods.BUILD,
                    tx_files=tx_files_action,
                )
            err_str = str(excinfo.value)
            assert (
                "Stake credential specified in the proposal is not registered on-chain" in err_str
            ), err_str

        # Make sure we have enough time to submit the proposals in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action_build_raw",
            src_address=pool_user_ug_treasury.payment.address,
            submit_method=submit_utils.SubmitMethods.API
            if submit_utils.is_submit_api_available()
            else submit_utils.SubmitMethods.CLI,
            build_method=clusterlib_utils.BuildMethods.BUILD_RAW,
            tx_files=tx_files_action,
            deposit=actions_deposit_combined + stake_deposit_amt,
        )

        assert cluster.g_query.get_stake_addr_info(recv_stake_addr_rec.address).address, (
            f"Stake address is not registered: {recv_stake_addr_rec.address}"
        )

        out_utxos_action = cluster.g_query.get_utxo(tx_raw_output=tx_output_action)
        assert (
            clusterlib.filter_utxos(
                utxos=out_utxos_action, address=pool_user_ug_treasury.payment.address
            )[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_action.txins)
            - tx_output_action.fee
            - actions_deposit_combined
            - stake_deposit_amt
        ), f"Incorrect balance for source address `{pool_user_ug_treasury.payment.address}`"

        action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
        action_gov_state = cluster.g_query.get_gov_state()
        action_epoch = cluster.g_query.get_epoch()
        conway_common.save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{action_epoch}"
        )

        for action_ix in range(actions_num):
            prop_action = governance_utils.lookup_proposal(
                gov_state=action_gov_state, action_txid=action_txid, action_ix=action_ix
            )
            assert prop_action, "Treasury withdrawals action not found"
            assert (
                prop_action["proposalProcedure"]["govAction"]["tag"]
                == governance_utils.ActionTags.TREASURY_WITHDRAWALS.value
            ), "Incorrect action tag"

        def _cast_vote(
            approve: bool, vote_id: str, add_spo_votes: bool = False
        ) -> governance_utils.VotedVotes:
            votes_cc = []
            votes_drep = []
            votes_spo = []
            for action_ix in range(actions_num):
                votes_cc.extend(
                    [
                        cluster.g_governance.vote.create_committee(
                            vote_name=f"{temp_template}_{vote_id}_{action_ix}_cc{i}",
                            action_txid=action_txid,
                            action_ix=action_ix,
                            vote=clusterlib.Votes.YES if approve else clusterlib.Votes.NO,
                            cc_hot_vkey_file=m.hot_keys.hot_vkey_file,
                        )
                        for i, m in enumerate(governance_data.cc_key_members, start=1)
                    ]
                )
                votes_drep.extend(
                    [
                        cluster.g_governance.vote.create_drep(
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
                            cluster.g_governance.vote.create_spo(
                                vote_name=f"{temp_template}_{vote_id}_{action_ix}_pool{i}",
                                action_txid=action_txid,
                                action_ix=action_ix,
                                vote=clusterlib.Votes.NO,
                                cold_vkey_file=p.vkey_file,
                            )
                            for i, p in enumerate(governance_data.pools_cold, start=1)
                        ]
                    )

            votes: list[governance_utils.VotesAllT] = [*votes_cc, *votes_drep, *votes_spo]
            spo_keys = [r.skey_file for r in governance_data.pools_cold] if votes_spo else []
            vote_keys = [
                *[r.hot_keys.hot_skey_file for r in governance_data.cc_key_members],
                *[r.key_pair.skey_file for r in governance_data.dreps_reg],
                *spo_keys,
            ]

            # Make sure we have enough time to submit the votes in one epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
            )
            _cast_vote_epoch = cluster.g_query.get_epoch()

            conway_common.submit_vote(
                cluster_obj=cluster,
                name_template=f"{temp_template}_{vote_id}",
                payment_addr=pool_user_ug_treasury.payment,
                votes=votes,
                keys=vote_keys,
            )

            vote_gov_state = cluster.g_query.get_gov_state()
            conway_common.save_gov_state(
                gov_state=vote_gov_state,
                name_template=f"{temp_template}_vote_{vote_id}_{_cast_vote_epoch}",
            )

            assert cluster.g_query.get_epoch() == _cast_vote_epoch, (
                "Epoch changed and it would affect other checks"
            )

            for action_ix in range(actions_num):
                prop_vote = governance_utils.lookup_proposal(
                    gov_state=vote_gov_state, action_txid=action_txid, action_ix=action_ix
                )
                assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
                assert prop_vote["dRepVotes"], "No DRep votes"
                assert not votes_spo or prop_vote["stakePoolVotes"], "Unexpected stake pool votes"

            return governance_utils.VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo)

        # Check that SPOs cannot vote on treasury withdrawal action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _cast_vote(approve=False, vote_id="with_spos", add_spo_votes=True)
        err_str = str(excinfo.value)
        assert "StakePoolVoter" in err_str, err_str

        # Vote & disapprove the action
        _cast_vote(approve=False, vote_id="no")

        # Vote & approve the action
        reqc.cip048.start(url=helpers.get_vcs_link())
        voted_votes = _cast_vote(approve=True, vote_id="yes")
        approved_epoch = cluster.g_query.get_epoch()

        treasury_init = clusterlib_utils.get_chain_account_state(
            ledger_state=clusterlib_utils.get_ledger_state(cluster_obj=cluster)
        ).treasury

        # Check ratification
        rat_epoch = cluster.wait_for_epoch(epoch_no=approved_epoch + 1, padding_seconds=5)
        rat_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{rat_epoch}"
        )
        for action_ix in range(actions_num):
            rat_action = governance_utils.lookup_ratified_actions(
                gov_state=rat_gov_state, action_txid=action_txid, action_ix=action_ix
            )
            assert rat_action, f"Action with ix {action_ix} not found in ratified actions"

        reqc.cip038_06.start(url=helpers.get_vcs_link())
        assert not rat_gov_state["nextRatifyState"]["ratificationDelayed"], (
            "Ratification is delayed unexpectedly"
        )
        reqc.cip038_06.success()

        # Disapprove ratified action, the voting shouldn't have any effect
        _cast_vote(approve=False, vote_id="after_ratification")

        reqc.cip033.start(url=helpers.get_vcs_link())

        # Check enactment
        cluster.wait_for_epoch(epoch_no=approved_epoch + 2, padding_seconds=5)
        assert cluster.g_query.get_stake_addr_info(
            recv_stake_addr_rec.address
        ).reward_account_balance == sum(transfer_amts), "Incorrect reward account balance"
        [r.success() for r in (reqc.cip033, reqc.cip048)]

        # Try to vote on enacted action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _cast_vote(approve=False, vote_id="enacted")
        err_str = str(excinfo.value)
        assert "(GovActionsDoNotExist" in err_str, err_str

        reqc.cip079.start(url=helpers.get_vcs_link())
        treasury_finish = clusterlib_utils.get_chain_account_state(
            ledger_state=clusterlib_utils.get_ledger_state(cluster_obj=cluster)
        ).treasury
        assert treasury_init != treasury_finish, "Treasury balance didn't change"
        reqc.cip079.success()

        # Check action view
        governance_utils.check_action_view(cluster_obj=cluster, action_data=withdrawal_actions[0])

        # Check vote view
        if voted_votes.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.drep[0])

        # Check dbsync
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cip084, reqc.db009, reqc.db022)]
        dbsync_utils.check_treasury_withdrawal(
            stake_address=recv_stake_addr_rec.address,
            transfer_amts=transfer_amts,
            txhash=action_txid,
        )
        dbsync_utils.check_reward_rest(
            stake_address=recv_stake_addr_rec.address,
            transfer_amts=transfer_amts,
            type="treasury",
        )

        [r.success() for r in (reqc.cip084, reqc.db009, reqc.db022)]

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_expire_treasury_withdrawals(
        self,
        cluster_use_governance: governance_utils.GovClusterT,
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
        cluster, governance_data = cluster_use_governance
        temp_template = common.get_test_id(cluster)
        is_in_bootstrap = conway_common.is_in_bootstrap(cluster_obj=cluster)
        actions_num = 3

        pparams = cluster.g_query.get_protocol_params()
        stake_deposit_amt = cluster.g_query.get_address_deposit(pparams=pparams)
        action_deposit_amt = cluster.g_query.get_gov_action_deposit(pparams=pparams)

        # Create stake address and registration certificate
        recv_stake_addr_rec = cluster.g_stake_address.gen_stake_addr_and_keys(
            name=f"{temp_template}_receive"
        )
        recv_stake_addr_reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_receive",
            deposit_amt=stake_deposit_amt,
            stake_vkey_file=recv_stake_addr_rec.vkey_file,
        )

        # Create an action and register stake address
        transfer_amt = 5_000_000_000
        init_return_account_balance = cluster.g_query.get_stake_addr_info(
            pool_user_ug.stake.address
        ).reward_account_balance

        anchor_data = governance_utils.get_default_anchor_data()

        if not is_in_bootstrap:
            reqc.cip030ex.start(url=helpers.get_vcs_link())
        withdrawal_actions = [
            cluster.g_governance.action.create_treasury_withdrawal(
                action_name=f"{temp_template}_{a}",
                transfer_amt=transfer_amt + a,
                deposit_amt=action_deposit_amt,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
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

        actions_deposit_combined = action_deposit_amt * actions_num

        if is_in_bootstrap:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_action_bootstrap",
                    src_address=pool_user_ug.payment.address,
                    tx_files=tx_files_action,
                    deposit=actions_deposit_combined + stake_deposit_amt,
                )
            err_str = str(excinfo.value)
            assert "(DisallowedProposalDuringBootstrap" in err_str, err_str
            return

        # Make sure we have enough time to submit the proposals in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        action_prop_epoch = cluster.g_query.get_epoch()

        reqc.cli025.start(url=helpers.get_vcs_link())
        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action",
            src_address=pool_user_ug.payment.address,
            tx_files=tx_files_action,
            deposit=actions_deposit_combined + stake_deposit_amt,
        )
        reqc.cli025.success()

        assert cluster.g_query.get_stake_addr_info(recv_stake_addr_rec.address).address, (
            f"Stake address is not registered: {recv_stake_addr_rec.address}"
        )

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
        action_gov_state = cluster.g_query.get_gov_state()
        action_epoch = cluster.g_query.get_epoch()
        conway_common.save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{action_epoch}"
        )

        votes: list[governance_utils.VotesAllT] = []
        for action_ix in range(actions_num):
            prop_action = governance_utils.lookup_proposal(
                gov_state=action_gov_state, action_txid=action_txid, action_ix=action_ix
            )
            assert prop_action, "Treasury withdrawals action not found"
            assert (
                prop_action["proposalProcedure"]["govAction"]["tag"]
                == governance_utils.ActionTags.TREASURY_WITHDRAWALS.value
            ), "Incorrect action tag"

            approve_cc = False
            approve_dreps = False
            if action_ix == 0:
                approve_cc = True
            elif action_ix == 1 and configuration.HAS_CC:
                approve_dreps = True

            votes.extend(
                [
                    cluster.g_governance.vote.create_committee(
                        vote_name=f"{temp_template}_{action_ix}_cc{i}",
                        action_txid=action_txid,
                        action_ix=action_ix,
                        vote=conway_common.get_yes_abstain_vote(i)
                        if approve_cc
                        else conway_common.get_no_abstain_vote(i),
                        cc_hot_vkey_file=m.hot_keys.hot_vkey_file,
                    )
                    for i, m in enumerate(governance_data.cc_key_members, start=1)
                ]
            )
            votes.extend(
                [
                    cluster.g_governance.vote.create_drep(
                        vote_name=f"{temp_template}_{action_ix}_drep{i}",
                        action_txid=action_txid,
                        action_ix=action_ix,
                        vote=conway_common.get_yes_abstain_vote(i)
                        if approve_dreps
                        else conway_common.get_no_abstain_vote(i),
                        drep_vkey_file=d.key_pair.vkey_file,
                    )
                    for i, d in enumerate(governance_data.dreps_reg, start=1)
                ]
            )

        vote_keys = [
            *[r.hot_keys.hot_skey_file for r in governance_data.cc_key_members],
            *[r.key_pair.skey_file for r in governance_data.dreps_reg],
        ]

        # Make sure we have enough time to submit the votes in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        vote_epoch = cluster.g_query.get_epoch()

        reqc.cli026.start(url=helpers.get_vcs_link())
        conway_common.submit_vote(
            cluster_obj=cluster,
            name_template=temp_template,
            payment_addr=pool_user_ug.payment,
            votes=votes,
            keys=vote_keys,
            submit_method=submit_utils.SubmitMethods.API
            if submit_utils.is_submit_api_available()
            else submit_utils.SubmitMethods.CLI,
        )
        reqc.cli026.success()

        vote_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=vote_gov_state, name_template=f"{temp_template}_vote_{vote_epoch}"
        )

        assert cluster.g_query.get_epoch() == vote_epoch, (
            "Epoch changed and it would affect other checks"
        )

        reqc.cip069ex.start(url=helpers.get_vcs_link())

        for action_ix in range(actions_num):
            prop_vote = governance_utils.lookup_proposal(
                gov_state=vote_gov_state, action_txid=action_txid, action_ix=action_ix
            )
            assert not configuration.HAS_CC or prop_vote["committeeVotes"], "No committee votes"
            assert prop_vote["dRepVotes"], "No DRep votes"
            assert not prop_vote["stakePoolVotes"], "Unexpected stake pool votes"

        # Check that the actions are not ratified
        nonrat_epoch = cluster.wait_for_epoch(epoch_no=vote_epoch + 1, padding_seconds=5)
        nonrat_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=nonrat_gov_state, name_template=f"{temp_template}_nonrat_{nonrat_epoch}"
        )
        for action_ix in range(actions_num):
            assert not governance_utils.lookup_ratified_actions(
                gov_state=nonrat_gov_state, action_txid=action_txid, action_ix=action_ix
            ), f"Action {action_txid}#{action_ix} got ratified unexpectedly"

        # Check that the actions are not enacted
        nonenacted_epoch = cluster.wait_for_epoch(epoch_no=vote_epoch + 2, padding_seconds=5)
        nonenacted_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=nonenacted_gov_state,
            name_template=f"{temp_template}_nonenact_{nonenacted_epoch}",
        )
        assert (
            cluster.g_query.get_stake_addr_info(recv_stake_addr_rec.address).reward_account_balance
            == 0
        ), "Incorrect reward account balance"

        # Check that the actions expired
        reqc.cip032ex.start(url=helpers.get_vcs_link())
        epochs_to_expiration = action_prop_epoch + cluster.conway_genesis["govActionLifetime"] + 1
        expire_epoch = cluster.wait_for_epoch(epoch_no=epochs_to_expiration, padding_seconds=5)
        expire_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=expire_gov_state, name_template=f"{temp_template}_expire_{expire_epoch}"
        )
        assert (
            cluster.g_query.get_stake_addr_info(recv_stake_addr_rec.address).reward_account_balance
            == 0
        ), "Incorrect reward account balance"
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
        rem_deposit_returned = cluster.g_query.get_stake_addr_info(
            pool_user_ug.stake.address
        ).reward_account_balance

        assert rem_deposit_returned == init_return_account_balance + actions_deposit_combined, (
            "Incorrect return account balance"
        )

        reqc.cip030ex.success()

        # Additional checks of governance state output
        for action_ix in range(actions_num):
            assert governance_utils.lookup_expired_actions(
                gov_state=expire_gov_state, action_txid=action_txid, action_ix=action_ix
            ), f"Action {action_txid}#{action_ix} not found in removed actions"
            assert governance_utils.lookup_proposal(
                gov_state=expire_gov_state, action_txid=action_txid, action_ix=action_ix
            ), f"Action {action_txid}#{action_ix} no longer found in proposals"
            assert not governance_utils.lookup_proposal(
                gov_state=rem_gov_state, action_txid=action_txid, action_ix=action_ix
            ), f"Action {action_txid}#{action_ix} not removed from proposals"

        [r.success() for r in (reqc.cip032ex, reqc.cip069ex)]


class TestMIRCerts:
    """Tests for MIR certificates."""

    @pytest.fixture(scope="class")
    def skip_on_missing_legacy(self) -> None:
        if not clusterlib_utils.cli_has("legacy governance"):
            pytest.skip("`legacy governance` commands are not available")

    @pytest.fixture
    def payment_addr(
        self,
        skip_on_missing_legacy: None,  # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.AddressRecord:
        """Create new payment address."""
        addr = common.get_payment_addr(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            caching_key=helpers.get_current_line_str(),
            amount=4_000_000,
        )
        return addr

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "mir_cert", ("to_treasury", "to_rewards", "treasury_to_addr", "reserves_to_addr")
    )
    @pytest.mark.smoke
    def test_mir_certificates(
        self,
        skip_on_missing_legacy: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        mir_cert: str,
    ):
        """Try to use MIR certificates in Conway+ eras.

        Expect failure.

        * try and fail to build the Tx using `transaction build`
        * successfully build the Tx as Babbage Tx using `transaction build-raw`
        * try and fail to submit the Babbage Tx
        """
        # TODO: convert to use `compatible babbage governance create-mir-certificate`
        temp_template = common.get_test_id(cluster)
        amount = 1_500_000

        reqc.cip070.start(url=helpers.get_vcs_link())

        if mir_cert == "to_treasury":
            cert_file = cluster.g_legacy_governance.gen_mir_cert_to_treasury(
                transfer=amount,
                tx_name=temp_template,
            )
        elif mir_cert == "to_rewards":
            cert_file = cluster.g_legacy_governance.gen_mir_cert_to_rewards(
                transfer=amount,
                tx_name=temp_template,
            )
        elif mir_cert == "treasury_to_addr":
            cert_file = cluster.g_legacy_governance.gen_mir_cert_stake_addr(
                tx_name=temp_template,
                stake_addr="stake_test1uzy5myemjnne3gr0jp7yhtznxx2lvx4qgv730jktsu46v5gaw7rmt",
                reward=amount,
                use_treasury=True,
            )
        elif mir_cert == "reserves_to_addr":
            cert_file = cluster.g_legacy_governance.gen_mir_cert_stake_addr(
                tx_name=temp_template,
                stake_addr="stake_test1uzy5myemjnne3gr0jp7yhtznxx2lvx4qgv730jktsu46v5gaw7rmt",
                reward=amount,
                use_treasury=False,
            )
        else:
            _verr = f"Unknown MIR cert scenario: {mir_cert}"
            raise ValueError(_verr)

        tx_files = clusterlib.TxFiles(
            certificate_files=[cert_file],
            signing_key_files=[
                payment_addr.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        # The Tx cannot be build in Conway using `build`
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                tx_name=temp_template,
                src_address=payment_addr.address,
                tx_files=tx_files,
            )
        err_build = str(excinfo.value)
        assert "TextEnvelope type error:" in err_build, err_build

        # The Tx can be build as Babbage Tx using `build-raw`, but cannot be submitted.
        # TODO: convert to use `compatible babbage transaction signed-transaction`
        if clusterlib_utils.cli_has("babbage transaction build-raw"):
            cluster_babbage = cluster_nodes.get_cluster_type().get_cluster_obj(
                command_era="babbage"
            )
            tx_output = cluster_babbage.g_transaction.build_raw_tx(
                tx_name=temp_template,
                src_address=payment_addr.address,
                fee=400_000,
                tx_files=tx_files,
            )

            out_file_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )

            with pytest.raises(clusterlib.CLIError) as excinfo:
                cluster.g_transaction.submit_tx(tx_file=out_file_signed, txins=tx_output.txins)
            err_submit = str(excinfo.value)
            assert "Error: The era of the node and the tx do not match." in err_submit, err_submit

        reqc.cip070.success()
