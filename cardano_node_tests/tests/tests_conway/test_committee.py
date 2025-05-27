"""Tests for Conway governance Constitutional Committee functionality."""

import logging
import pathlib as pl
import random
import re
import typing as tp

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
import pytest_subtests
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_api
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils import web
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


@pytest.fixture
def payment_addr_comm(
    cluster_manager: cluster_management.ClusterManager,
    cluster_use_committee: governance_utils.GovClusterT,
) -> clusterlib.AddressRecord:
    """Create new payment address."""
    cluster, __ = cluster_use_committee
    addr = common.get_payment_addr(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        caching_key=helpers.get_current_line_str(),
    )
    return addr


@pytest.fixture
def pool_user(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> clusterlib.PoolUser:
    """Create a pool user."""
    key = helpers.get_current_line_str()
    name_template = common.get_test_id(cluster)
    return common.get_registered_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        caching_key=key,
        amount=350_000_000,
        min_amount=250_000_000,
    )


@pytest.fixture
def pool_user_lg(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_governance: governance_utils.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "lock governance"."""
    cluster, __ = cluster_lock_governance
    key = helpers.get_current_line_str()
    name_template = common.get_test_id(cluster)
    return common.get_registered_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        caching_key=key,
        amount=400_000_000,
        min_amount=350_000_000,
    )


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
        amount=300_000_000,
        min_amount=250_000_000,
    )


class TestCommittee:
    """Tests for Constitutional Committee."""

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_register_hot_key_no_cc_member(
        self,
        cluster: clusterlib.ClusterLib,
        pool_user: clusterlib.PoolUser,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Try to submit a Hot Credential Authorization certificate without being a CC member.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        cc_auth_record = governance_utils.get_cc_member_auth_record(
            cluster_obj=cluster,
            name_template=temp_template,
        )

        tx_files_auth = clusterlib.TxFiles(
            certificate_files=[cc_auth_record.auth_cert],
            signing_key_files=[pool_user.payment.skey_file, cc_auth_record.cold_key_pair.skey_file],
        )

        # Try to submit a Hot Credential Authorization certificate without being a CC member
        with pytest.raises((clusterlib.CLIError, submit_api.SubmitApiError)) as excinfo:
            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_auth",
                src_address=pool_user.payment.address,
                submit_method=submit_method,
                use_build_cmd=use_build_cmd,
                tx_files=tx_files_auth,
            )
        err_str = str(excinfo.value)
        assert "ConwayCommitteeIsUnknown" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_invalid_cc_member_vote(  # noqa: C901
        self,
        cluster_use_governance: governance_utils.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
        subtests: pytest_subtests.SubTests,
    ):
        """Try to vote with invalid CC member.

        Expect failure.

        * Create CC memeber hot and cold keys
        * Create a pparam update proposal
        * Try to vote on the pparam update proposal with the unauthorized hot key
        * Expect `VotersDoNotExist` error on submit
        * Submit a proposal to add the new CC member, do not vote on it
        * Submit authorization certificate for the proposed CC member
        * Try to vote on the pparam update proposal with the hot keys of the proposed CC member
        * Expect `ConwayMempoolFailure "Unelected committee members are not allowed to cast votes`
          error on submit
        """
        cluster, governance_data = cluster_use_governance
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.g_query.get_gov_action_deposit()

        submit_methods = [submit_utils.SubmitMethods.CLI]
        if submit_utils.is_submit_api_available():
            submit_methods.append(submit_utils.SubmitMethods.API)

        cc_auth_record = governance_utils.get_cc_member_auth_record(
            cluster_obj=cluster,
            name_template=temp_template,
        )
        cc_member = clusterlib.CCMember(
            epoch=10_000,
            cold_vkey_file=cc_auth_record.cold_key_pair.vkey_file,
            cold_skey_file=cc_auth_record.cold_key_pair.skey_file,
        )
        cc_member_key = f"keyHash-{cc_auth_record.key_hash}"
        cc_key_member = governance_utils.CCKeyMember(
            cc_member=cc_member,
            hot_keys=governance_utils.CCHotKeys(
                hot_skey_file=cc_auth_record.hot_key_pair.skey_file,
                hot_vkey_file=cc_auth_record.hot_key_pair.vkey_file,
            ),
        )

        def _propose_pparam_change() -> conway_common.PParamPropRec:
            anchor_data = governance_utils.get_default_anchor_data()
            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.PPARAM_UPDATE,
                gov_state=cluster.g_query.get_gov_state(),
            )

            proposals = [
                clusterlib_utils.UpdateProposal(
                    arg="--drep-activity",
                    value=random.randint(1, 255),
                    name="dRepActivity",
                ),
            ]

            prop_rec = conway_common.propose_pparams_update(
                cluster_obj=cluster,
                name_template=f"{temp_template}_drep_activity",
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                pool_user=pool_user_ug,
                proposals=proposals,
                prev_action_rec=prev_action_rec,
            )

            return prop_rec

        prop_rec = _propose_pparam_change()

        vote_cc_all = [
            cluster.g_governance.vote.create_committee(
                vote_name=f"{temp_template}_all_cc{i}",
                action_txid=prop_rec.action_txid,
                action_ix=prop_rec.action_ix,
                vote=clusterlib.Votes.YES,
                cc_hot_vkey_file=m.hot_keys.hot_vkey_file,
            )
            for i, m in enumerate((*governance_data.cc_key_members, cc_key_member), start=1)
        ]
        vote_keys_all = [
            *[r.hot_keys.hot_skey_file for r in (*governance_data.cc_key_members, cc_key_member)],
        ]

        vote_cc_one = cluster.g_governance.vote.create_committee(
            vote_name=f"{temp_template}_one_cc",
            action_txid=prop_rec.action_txid,
            action_ix=prop_rec.action_ix,
            vote=clusterlib.Votes.YES,
            cc_hot_vkey_file=cc_key_member.hot_keys.hot_vkey_file,
        )
        vote_key_one = cc_key_member.hot_keys.hot_skey_file

        def _submit_vote(scenario: str, build_method: str, submit_method: str) -> None:
            conway_common.submit_vote(
                cluster_obj=cluster,
                name_template=f"{temp_template}_{scenario}_{build_method}_{submit_method}",
                payment_addr=pool_user_ug.payment,
                votes=vote_cc_all if "_all_" in scenario else [vote_cc_one],
                keys=vote_keys_all if "_all_" in scenario else [vote_key_one],
                submit_method=submit_method,
                use_build_cmd=build_method == "build",
            )

        for smethod in submit_methods:
            for build_method in ("build_raw", "build"):
                for scenario in ("all_cc_one_nonexistent", "all_cc_all_nonexistent"):
                    with subtests.test(id=f"{scenario}_{build_method}_{smethod}"):
                        with pytest.raises(
                            (clusterlib.CLIError, submit_api.SubmitApiError)
                        ) as excinfo:
                            _submit_vote(
                                scenario=scenario,
                                build_method=build_method,
                                submit_method=smethod,
                            )
                        err_str = str(excinfo.value)
                        assert "(VotersDoNotExist" in err_str, err_str

        # Update committee action is not supported in bootstrap period
        if conway_common.is_in_bootstrap(cluster_obj=cluster):
            return

        def _propose_new_member() -> None:
            anchor_data = governance_utils.get_default_anchor_data()
            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.COMMITTEE,
                gov_state=cluster.g_query.get_gov_state(),
            )

            update_action = cluster.g_governance.action.update_committee(
                action_name=temp_template,
                deposit_amt=deposit_amt,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                threshold="2/3",
                add_cc_members=[cc_member],
                prev_action_txid=prev_action_rec.txid,
                prev_action_ix=prev_action_rec.ix,
                deposit_return_stake_vkey_file=pool_user_ug.stake.vkey_file,
            )

            tx_files = clusterlib.TxFiles(
                proposal_files=[update_action.action_file],
                signing_key_files=[
                    pool_user_ug.payment.skey_file,
                    cc_auth_record.cold_key_pair.skey_file,
                ],
            )

            tx_output = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=temp_template,
                src_address=pool_user_ug.payment.address,
                use_build_cmd=True,
                tx_files=tx_files,
                deposit=deposit_amt,
            )

            out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
            assert (
                clusterlib.filter_utxos(utxos=out_utxos, address=pool_user_ug.payment.address)[
                    0
                ].amount
                == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
            ), f"Incorrect balance for source address `{pool_user_ug.payment.address}`"

            txid = cluster.g_transaction.get_txid(tx_body_file=tx_output.out_file)
            gov_state = cluster.g_query.get_gov_state()
            prop = governance_utils.lookup_proposal(gov_state=gov_state, action_txid=txid)
            assert prop, "Update committee action not found"
            assert (
                prop["proposalProcedure"]["govAction"]["tag"]
                == governance_utils.ActionTags.UPDATE_COMMITTEE.value
            ), "Incorrect action tag"

        def _auth_hot_keys() -> None:
            """Authorize the hot keys."""
            tx_files_auth = clusterlib.TxFiles(
                certificate_files=[cc_auth_record.auth_cert],
                signing_key_files=[
                    pool_user_ug.payment.skey_file,
                    cc_auth_record.cold_key_pair.skey_file,
                ],
            )

            tx_output_auth = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_auth",
                src_address=pool_user_ug.payment.address,
                use_build_cmd=True,
                tx_files=tx_files_auth,
            )

            out_utxos_auth = cluster.g_query.get_utxo(tx_raw_output=tx_output_auth)
            assert (
                clusterlib.filter_utxos(utxos=out_utxos_auth, address=pool_user_ug.payment.address)[
                    0
                ].amount
                == clusterlib.calculate_utxos_balance(tx_output_auth.txins) - tx_output_auth.fee
            ), f"Incorrect balance for source address `{pool_user_ug.payment.address}`"

            cluster.wait_for_new_block(new_blocks=2)
            auth_committee_state = cluster.g_query.get_committee_state()
            auth_epoch = cluster.g_query.get_epoch()
            conway_common.save_committee_state(
                committee_state=auth_committee_state,
                name_template=f"{temp_template}_auth_{auth_epoch}",
            )
            auth_member_rec = auth_committee_state["committee"][cc_member_key]
            assert auth_member_rec["hotCredsAuthStatus"]["tag"] == "MemberAuthorized", (
                "CC Member was NOT authorized"
            )
            assert not auth_member_rec["expiration"], "CC Member should not be elected"
            assert auth_member_rec["status"] == "Unrecognized", "CC Member should not be recognized"

        # Make sure we have enough time to submit the proposals and vote in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        _propose_new_member()
        _auth_hot_keys()

        reqc.int001.start(url=helpers.get_vcs_link())
        subtest_errors = []
        for smethod in submit_methods:
            for build_method in ("build_raw", "build"):
                for scenario in ("all_cc_one_unelected", "all_cc_all_unelected"):
                    subtest_id = f"{scenario}_{build_method}_{smethod}"
                    subtest_errors.append(subtest_id)
                    with subtests.test(id=subtest_id):
                        with pytest.raises(
                            (clusterlib.CLIError, submit_api.SubmitApiError)
                        ) as excinfo:
                            _submit_vote(
                                scenario=scenario,
                                build_method=build_method,
                                submit_method=smethod,
                            )
                        err_str = str(excinfo.value)
                        assert re.search(
                            "ConwayMempoolFailure .*Unelected committee members are not allowed "
                            "to cast votes:",
                            err_str,
                        ), err_str
                        subtest_errors.pop()

        if not subtest_errors:
            reqc.int001.success()

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("threshold_type", ("fraction", "decimal"))
    @pytest.mark.dbsync
    @pytest.mark.smoke
    def test_update_committee_action(
        self,
        cluster: clusterlib.ClusterLib,
        pool_user: clusterlib.PoolUser,
        use_build_cmd: bool,
        submit_method: str,
        threshold_type: str,
    ):
        """Test update committee action.

        * add CC Members
        * update committee threshold
        * check that the proposed changes are correct in `query gov-state`
        """
        temp_template = common.get_test_id(cluster)
        cc_size = 3
        threshold = "2/3" if threshold_type == "fraction" else "0.8457565493"

        cc_auth_records = [
            governance_utils.get_cc_member_auth_record(
                cluster_obj=cluster,
                name_template=f"{temp_template}_{i}",
            )
            for i in range(1, cc_size + 1)
        ]
        cc_members = [
            clusterlib.CCMember(
                epoch=10_000,
                cold_vkey_file=r.cold_key_pair.vkey_file,
                cold_skey_file=r.cold_key_pair.skey_file,
            )
            for r in cc_auth_records
        ]

        deposit_amt = cluster.g_query.get_gov_action_deposit()
        anchor_data = governance_utils.get_default_anchor_data()
        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.COMMITTEE,
            gov_state=cluster.g_query.get_gov_state(),
        )

        reqc.cip031a_01.start(url=helpers.get_vcs_link())
        update_action = cluster.g_governance.action.update_committee(
            action_name=temp_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_data.url,
            anchor_data_hash=anchor_data.hash,
            threshold=threshold,
            add_cc_members=cc_members,
            prev_action_txid=prev_action_rec.txid,
            prev_action_ix=prev_action_rec.ix,
            deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
        )
        reqc.cip031a_01.success()

        tx_files = clusterlib.TxFiles(
            proposal_files=[update_action.action_file],
            signing_key_files=[
                pool_user.payment.skey_file,
                *[r.cold_key_pair.skey_file for r in cc_auth_records],
            ],
        )

        if conway_common.is_in_bootstrap(cluster_obj=cluster):
            reqc.cip026_01.start(url=helpers.get_vcs_link())
            with pytest.raises((clusterlib.CLIError, submit_api.SubmitApiError)) as excinfo:
                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_bootstrap",
                    src_address=pool_user.payment.address,
                    submit_method=submit_method,
                    use_build_cmd=use_build_cmd,
                    tx_files=tx_files,
                    deposit=deposit_amt,
                )
            err_str = str(excinfo.value)
            assert "(DisallowedProposalDuringBootstrap" in err_str, err_str
            reqc.cip026_01.success()
            return

        reqc.cip007.start(url=helpers.get_vcs_link())

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=pool_user.payment.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files,
            deposit=deposit_amt,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=pool_user.payment.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_output.out_file)
        gov_state = cluster.g_query.get_gov_state()
        prop = governance_utils.lookup_proposal(gov_state=gov_state, action_txid=txid)
        assert prop, "Update committee action not found"
        assert (
            prop["proposalProcedure"]["govAction"]["tag"]
            == governance_utils.ActionTags.UPDATE_COMMITTEE.value
        ), "Incorrect action tag"

        if threshold_type == "fraction":
            assert prop["proposalProcedure"]["govAction"]["contents"][3] == {
                "denominator": 3,
                "numerator": 2,
            }, "Incorrect threshold"
        else:
            assert str(prop["proposalProcedure"]["govAction"]["contents"][3]) == threshold, (
                "Incorrect threshold"
            )

        cc_key_hashes = {f"keyHash-{c.key_hash}" for c in cc_auth_records}
        prop_cc_key_hashes = set(prop["proposalProcedure"]["govAction"]["contents"][2].keys())
        assert cc_key_hashes == prop_cc_key_hashes, "Incorrect CC key hashes"

        reqc.cip007.success()

        # Check dbsync
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.db010, reqc.db011)]
        dbsync_utils.check_committee_info(gov_state=gov_state, txid=txid)
        [r.success() for r in (reqc.db010, reqc.db011)]

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.dbsync
    def test_add_rm_committee_members(  # noqa: C901
        self,
        cluster_lock_governance: governance_utils.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
        testfile_temp_dir: pl.Path,
        request: FixtureRequest,
    ):
        """Test adding and removing CC members.

        * authorize hot keys of 3 new potential CC members
        * create the first "update committee" action to add 2 of the 3 new potential CC members

            - the first CC member is listed twice to test that it's not possible to add the same
              member multiple times
            - the first CC member expires in 3 epochs, the second and third in 5 epochs
            - vote to disapprove the action
            - vote to approve the action
            - check that CC members votes have no effect
            - check that the action is ratified
            - try to disapprove the ratified action, this shouldn't have any effect
            - check that the action is enacted
            - check that the new CC members were added
            - check that it's not possible to vote on enacted action

        * check that the first CC member has expired as expected
        * create the second "update committee" action to remove the second CC member

            - propose the action at the same epoch as the first action
            - use the first action as previous action
            - vote to disapprove the action
            - vote to approve the action in the same epoch as the first action was approved
            - check that CC members votes have no effect
            - check that the action is ratified
            - try to disapprove the ratified action, this shouldn't have any effect
            - check that the action is enacted one epoch after the first action, due to the
              ratification delay
            - check that the second CC member was removed
            - check that it's not possible to vote on enacted action

        * resign the third CC member
        * check output of votes and action `view` commands
        * check deposit is returned to user reward account after enactment
        * (optional) check committee members in db-sync
        """
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        if conway_common.is_in_bootstrap(cluster_obj=cluster):
            pytest.skip("Cannot run during bootstrap period.")

        init_return_account_balance = cluster.g_query.get_stake_addr_info(
            pool_user_lg.stake.address
        ).reward_account_balance

        deposit_amt = cluster.g_query.get_gov_action_deposit()

        # Check if total delegated stake is below the threshold. This can be used to check that
        # undelegated stake is treated as Abstain. If undelegated stake was treated as No, it
        # would not be possible to approve any action.
        delegated_stake = governance_utils.get_delegated_stake(cluster_obj=cluster)
        cur_pparams = cluster.g_query.get_protocol_params()
        drep_constitution_threshold = cur_pparams["dRepVotingThresholds"]["committeeNormal"]
        spo_constitution_threshold = cur_pparams["poolVotingThresholds"]["committeeNormal"]
        is_drep_total_below_threshold = (
            delegated_stake.drep / delegated_stake.total_lovelace
        ) < drep_constitution_threshold
        is_spo_total_below_threshold = (
            delegated_stake.spo / delegated_stake.total_lovelace
        ) < spo_constitution_threshold

        # Auth keys for CC members
        _url = helpers.get_vcs_link()
        [
            r.start(url=_url)
            for r in (reqc.cli003, reqc.cli004, reqc.cli005, reqc.cli006, reqc.cip003)
        ]

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

        cc_auth_record3 = governance_utils.get_cc_member_auth_record(
            cluster_obj=cluster,
            name_template=f"{temp_template}_member3",
        )
        cc_member3_key = f"keyHash-{cc_auth_record3.key_hash}"

        [r.success() for r in (reqc.cli003, reqc.cli004, reqc.cli005, reqc.cli006)]

        # Make sure we have enough time to submit the proposals in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER - 10
        )
        actions_epoch = cluster.g_query.get_epoch()

        # New CC members to be added
        cc_member1_expire = actions_epoch + 3
        cc_members = [
            clusterlib.CCMember(
                epoch=cc_member1_expire,
                cold_vkey_file=cc_auth_record1.cold_key_pair.vkey_file,
                cold_skey_file=cc_auth_record1.cold_key_pair.skey_file,
                cold_vkey_hash=cc_auth_record1.key_hash,
            ),
            clusterlib.CCMember(
                epoch=cluster.g_query.get_epoch() + 5,
                cold_vkey_file=cc_auth_record2.cold_key_pair.vkey_file,
                cold_skey_file=cc_auth_record2.cold_key_pair.skey_file,
                cold_vkey_hash=cc_auth_record2.key_hash,
            ),
            clusterlib.CCMember(
                epoch=cluster.g_query.get_epoch() + 5,
                cold_vkey_file=cc_auth_record3.cold_key_pair.vkey_file,
                cold_skey_file=cc_auth_record3.cold_key_pair.skey_file,
                cold_vkey_hash=cc_auth_record3.key_hash,
            ),
        ]

        def _auth_hot_keys() -> None:
            """Authorize the hot keys."""
            tx_files_auth = clusterlib.TxFiles(
                certificate_files=[
                    cc_auth_record1.auth_cert,
                    cc_auth_record2.auth_cert,
                    cc_auth_record3.auth_cert,
                ],
                signing_key_files=[
                    pool_user_lg.payment.skey_file,
                    cc_auth_record1.cold_key_pair.skey_file,
                    cc_auth_record2.cold_key_pair.skey_file,
                    cc_auth_record3.cold_key_pair.skey_file,
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

            cluster.wait_for_new_block(new_blocks=2)
            [r.start(url=_url) for r in (reqc.cli032, reqc.cip002, reqc.cip004)]
            auth_committee_state = cluster.g_query.get_committee_state()
            auth_epoch = cluster.g_query.get_epoch()
            conway_common.save_committee_state(
                committee_state=auth_committee_state,
                name_template=f"{temp_template}_auth_{auth_epoch}",
            )
            for mk in (cc_member1_key, cc_member2_key, cc_member3_key):
                auth_member_rec = auth_committee_state["committee"][mk]
                assert auth_member_rec["hotCredsAuthStatus"]["tag"] == "MemberAuthorized", (
                    "CC Member was NOT authorized"
                )
                assert not auth_member_rec["expiration"], "CC Member should not be elected"
                assert auth_member_rec["status"] == "Unrecognized", (
                    "CC Member should not be recognized"
                )
            [r.success() for r in (reqc.cli032, reqc.cip002, reqc.cip004)]

        def _add_members() -> tuple[clusterlib.ActionUpdateCommittee, str, int]:
            """Add new CC members."""
            anchor_data_add = governance_utils.get_default_anchor_data()
            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.COMMITTEE,
                gov_state=cluster.g_query.get_gov_state(),
            )

            _url = helpers.get_vcs_link()
            [r.start(url=_url) for r in (reqc.cli014, reqc.cip031b, reqc.cip054_02, reqc.cip058)]
            add_cc_action = cluster.g_governance.action.update_committee(
                action_name=f"{temp_template}_add",
                deposit_amt=deposit_amt,
                anchor_url=anchor_data_add.url,
                anchor_data_hash=anchor_data_add.hash,
                threshold=str(cluster.conway_genesis["committee"]["threshold"]),
                add_cc_members=[*cc_members, cc_members[0]],  # test adding the same member twice
                prev_action_txid=prev_action_rec.txid,
                prev_action_ix=prev_action_rec.ix,
                deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
            )
            [r.success() for r in (reqc.cli014, reqc.cip031b, reqc.cip054_02)]

            tx_files_action_add = clusterlib.TxFiles(
                proposal_files=[add_cc_action.action_file],
                signing_key_files=[
                    pool_user_lg.payment.skey_file,
                ],
            )

            tx_output_action = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_action_add",
                src_address=pool_user_lg.payment.address,
                use_build_cmd=True,
                tx_files=tx_files_action_add,
            )

            out_utxos_action_add = cluster.g_query.get_utxo(tx_raw_output=tx_output_action)
            assert (
                clusterlib.filter_utxos(
                    utxos=out_utxos_action_add, address=pool_user_lg.payment.address
                )[0].amount
                == clusterlib.calculate_utxos_balance(tx_output_action.txins)
                - tx_output_action.fee
                - deposit_amt
            ), f"Incorrect balance for source address `{pool_user_lg.payment.address}`"

            action_add_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
            action_add_gov_state = cluster.g_query.get_gov_state()
            action_add_epoch = cluster.g_query.get_epoch()
            conway_common.save_gov_state(
                gov_state=action_add_gov_state,
                name_template=f"{temp_template}_action_add_{action_add_epoch}",
            )
            prop_action_add = governance_utils.lookup_proposal(
                gov_state=action_add_gov_state, action_txid=action_add_txid
            )
            assert prop_action_add, "Update committee action not found"
            assert (
                prop_action_add["proposalProcedure"]["govAction"]["tag"]
                == governance_utils.ActionTags.UPDATE_COMMITTEE.value
            ), "Incorrect action tag"

            action_add_ix = prop_action_add["actionId"]["govActionIx"]

            return add_cc_action, action_add_txid, action_add_ix

        def _resign_member(res_member: clusterlib.CCMember) -> None:
            """Resign a CC member."""
            _url = helpers.get_vcs_link()
            [r.start(url=_url) for r in (reqc.cli007, reqc.cip012)]

            res_metadata_file = pl.Path(f"{temp_template}_res_metadata.json")
            res_metadata_content = {"name": "Resigned CC member"}
            helpers.write_json(out_file=res_metadata_file, content=res_metadata_content)
            res_metadata_hash = cluster.g_governance.get_anchor_data_hash(
                file_text=res_metadata_file
            )
            res_metadata_url = web.publish(file_path=res_metadata_file)

            res_cert = cluster.g_governance.committee.gen_cold_key_resignation_cert(
                key_name=temp_template,
                cold_vkey_file=res_member.cold_vkey_file,
                resignation_metadata_url=res_metadata_url,
                resignation_metadata_hash=res_metadata_hash,
            )
            reqc.cli007.success()

            tx_files_res = clusterlib.TxFiles(
                certificate_files=[res_cert],
                signing_key_files=[pool_user_lg.payment.skey_file, res_member.cold_skey_file],
            )

            tx_output_res = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_res_member",
                src_address=pool_user_lg.payment.address,
                tx_files=tx_files_res,
            )

            cluster.wait_for_new_block(new_blocks=2)
            res_committee_state = cluster.g_query.get_committee_state()
            member_key = f"keyHash-{res_member.cold_vkey_hash}"
            res_member_rec = res_committee_state["committee"].get(member_key)
            assert (
                not res_member_rec
                or res_member_rec["hotCredsAuthStatus"]["tag"] == "MemberResigned"
            ), "CC Member not resigned"
            reqc.cip012.success()

            res_out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_res)
            assert (
                clusterlib.filter_utxos(utxos=res_out_utxos, address=pool_user_lg.payment.address)[
                    0
                ].amount
                == clusterlib.calculate_utxos_balance(tx_output_res.txins) - tx_output_res.fee
            ), f"Incorrect balance for source address `{pool_user_lg.payment.address}`"

        def _remove_member(
            rem_member: clusterlib.CCMember, prev_action_txid: str, prev_action_ix: int
        ) -> tuple[clusterlib.ActionUpdateCommittee, str, int]:
            """Remove a CC member."""
            anchor_data_rem = governance_utils.get_default_anchor_data()

            reqc.cip005.start(url=helpers.get_vcs_link())
            rem_cc_action = cluster.g_governance.action.update_committee(
                action_name=f"{temp_template}_rem",
                deposit_amt=deposit_amt,
                anchor_url=anchor_data_rem.url,
                anchor_data_hash=anchor_data_rem.hash,
                threshold=str(cluster.conway_genesis["committee"]["threshold"]),
                rem_cc_members=[rem_member],
                prev_action_txid=prev_action_txid,
                prev_action_ix=prev_action_ix,
                deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
            )
            reqc.cip005.success()

            tx_files_action_rem = clusterlib.TxFiles(
                proposal_files=[rem_cc_action.action_file],
                signing_key_files=[
                    pool_user_lg.payment.skey_file,
                ],
            )

            tx_output_action = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_action_rem",
                src_address=pool_user_lg.payment.address,
                use_build_cmd=True,
                tx_files=tx_files_action_rem,
            )

            out_utxos_action_rem = cluster.g_query.get_utxo(tx_raw_output=tx_output_action)
            assert (
                clusterlib.filter_utxos(
                    utxos=out_utxos_action_rem, address=pool_user_lg.payment.address
                )[0].amount
                == clusterlib.calculate_utxos_balance(tx_output_action.txins)
                - tx_output_action.fee
                - deposit_amt
            ), f"Incorrect balance for source address `{pool_user_lg.payment.address}`"

            action_rem_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
            action_rem_gov_state = cluster.g_query.get_gov_state()
            action_rem_epoch = cluster.g_query.get_epoch()
            conway_common.save_gov_state(
                gov_state=action_rem_gov_state,
                name_template=f"{temp_template}_action_rem_{action_rem_epoch}",
            )
            prop_action_rem = governance_utils.lookup_proposal(
                gov_state=action_rem_gov_state, action_txid=action_rem_txid
            )
            assert prop_action_rem, "Update committee action not found"
            assert (
                prop_action_rem["proposalProcedure"]["govAction"]["tag"]
                == governance_utils.ActionTags.UPDATE_COMMITTEE.value
            ), "Incorrect action tag"

            action_rem_ix = prop_action_rem["actionId"]["govActionIx"]

            return rem_cc_action, action_rem_txid, action_rem_ix

        def _resign_active():
            """Resign the new CC members so they don't affect voting."""
            with helpers.change_cwd(testfile_temp_dir):
                # Make sure we have enough time to finish in one epoch
                clusterlib_utils.wait_for_epoch_interval(
                    cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
                )

                def _get_res_cert(idx: int, cc_auth: governance_utils.CCMemberAuth) -> pl.Path:
                    res_metadata_file = pl.Path(f"{temp_template}_{idx}_res_metadata.json")
                    res_metadata_content = {"name": "Resigned CC member", "idx": idx}
                    helpers.write_json(out_file=res_metadata_file, content=res_metadata_content)
                    res_metadata_hash = cluster.g_governance.get_anchor_data_hash(
                        file_text=res_metadata_file
                    )
                    res_metadata_url = web.publish(file_path=res_metadata_file)
                    cert = cluster.g_governance.committee.gen_cold_key_resignation_cert(
                        key_name=f"{temp_template}_res{idx}",
                        cold_vkey_file=cc_auth.cold_key_pair.vkey_file,
                        resignation_metadata_url=res_metadata_url,
                        resignation_metadata_hash=res_metadata_hash,
                    )
                    return cert

                auth_committee_state = cluster.g_query.get_committee_state()
                res_certs = [
                    _get_res_cert(idx=i, cc_auth=r)
                    for i, r in enumerate((cc_auth_record1, cc_auth_record2, cc_auth_record3))
                    if governance_utils.is_cc_active(
                        auth_committee_state["committee"].get(f"keyHash-{r.key_hash}") or {}
                    )
                ]

                tx_files_res = clusterlib.TxFiles(
                    certificate_files=res_certs,
                    signing_key_files=[
                        pool_user_lg.payment.skey_file,
                        cc_auth_record1.cold_key_pair.skey_file,
                        cc_auth_record2.cold_key_pair.skey_file,
                    ],
                )

                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_res_active",
                    src_address=pool_user_lg.payment.address,
                    use_build_cmd=True,
                    tx_files=tx_files_res,
                )

        def _check_cc_member1_expired(committee_state: dict[str, tp.Any], curr_epoch: int) -> None:
            member_rec = committee_state["committee"][cc_member1_key]
            if curr_epoch <= cc_member1_expire:
                assert member_rec["status"] != "Expired", "CC Member is already expired"
            if curr_epoch == cc_member1_expire:
                assert member_rec["nextEpochChange"]["tag"] == "ToBeExpired", (
                    "CC Member not to expire"
                )
            elif curr_epoch > cc_member1_expire:
                assert member_rec["status"] == "Expired", "CC Member should be expired"

        def _check_cc_member2_removed(gov_state: dict[str, tp.Any]):
            cc_member_val = conway_common.get_committee_val(data=gov_state)["members"].get(
                cc_member2_key
            )
            assert not cc_member_val, "Removed committee member still present"

        def _check_add_state(gov_state: dict[str, tp.Any]):
            for i, _cc_member_key in enumerate((cc_member1_key, cc_member2_key, cc_member3_key)):
                cc_member_val = conway_common.get_committee_val(data=gov_state)["members"].get(
                    _cc_member_key
                )
                assert cc_member_val, "New committee member not found"
                assert cc_member_val == cc_members[i].epoch

        def _check_resign_dbsync(res_member: clusterlib.CCMember) -> None:
            dbsync_utils.check_committee_member_registration(
                cc_member_cold_key=res_member.cold_vkey_hash
            )
            dbsync_utils.check_committee_member_deregistration(
                cc_member_cold_key=res_member.cold_vkey_hash
            )

        # Create an action to add new CC members
        add_cc_action, action_add_txid, action_add_ix = _add_members()

        # Create an action to remove CC member. Use add action as previous action, as the add
        # action will be ratified first.
        rem_cc_action, action_rem_txid, action_rem_ix = _remove_member(
            rem_member=cc_members[1], prev_action_txid=action_add_txid, prev_action_ix=action_add_ix
        )

        assert cluster.g_query.get_epoch() == actions_epoch, (
            "Haven't managed to submit the proposals in one epoch"
        )

        reqc.cip067.start(url=helpers.get_vcs_link())

        # Make sure the voting happens in next epoch
        cluster.wait_for_epoch(epoch_no=actions_epoch + 1, padding_seconds=5)

        # Vote & disapprove the add action
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_add_no",
            payment_addr=pool_user_lg.payment,
            action_txid=action_add_txid,
            action_ix=action_add_ix,
            approve_drep=False,
            approve_spo=False,
            drep_skip_votes=True,
        )

        # Vote & approve the add action
        request.addfinalizer(_resign_active)
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cip061_01, reqc.cip061_03, reqc.cip040)]
        if is_drep_total_below_threshold:
            reqc.cip064_01.start(url=_url)
        if is_spo_total_below_threshold:
            reqc.cip064_02.start(url=_url)
        voted_votes_add = conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_add_yes",
            payment_addr=pool_user_lg.payment,
            action_txid=action_add_txid,
            action_ix=action_add_ix,
            approve_drep=True,
            approve_spo=True,
            drep_skip_votes=True,
        )

        # Check that CC cannot vote on "update committee" action
        if configuration.HAS_CC:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                conway_common.cast_vote(
                    cluster_obj=cluster,
                    governance_data=governance_data,
                    name_template=f"{temp_template}_add_with_ccs",
                    payment_addr=pool_user_lg.payment,
                    action_txid=action_add_txid,
                    action_ix=action_add_ix,
                    approve_cc=True,
                    approve_drep=True,
                    approve_spo=True,
                )
            err_str = str(excinfo.value)
            assert "CommitteeVoter" in err_str, err_str

        # Vote & disapprove the removal action
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_rem_no",
            payment_addr=pool_user_lg.payment,
            action_txid=action_rem_txid,
            action_ix=action_rem_ix,
            approve_drep=False,
            approve_spo=False,
            drep_skip_votes=True,
        )

        # Vote & approve the removal action
        voted_votes_rem = conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_rem_yes",
            payment_addr=pool_user_lg.payment,
            action_txid=action_rem_txid,
            action_ix=action_rem_ix,
            approve_drep=True,
            approve_spo=True,
            drep_skip_votes=True,
        )
        vote_epoch = cluster.g_query.get_epoch()

        # Check ratification of add action
        rat_epoch = cluster.wait_for_epoch(epoch_no=vote_epoch + 1, padding_seconds=5)
        rat_add_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=rat_add_gov_state, name_template=f"{temp_template}_rat_add_{rat_epoch}"
        )
        rat_action = governance_utils.lookup_ratified_actions(
            gov_state=rat_add_gov_state, action_txid=action_add_txid
        )
        assert rat_action, "Action not found in ratified actions"

        # Disapprove ratified add action, the voting shouldn't have any effect
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_add_after_ratification",
            payment_addr=pool_user_lg.payment,
            action_txid=action_add_txid,
            action_ix=action_add_ix,
            approve_drep=False,
            approve_spo=False,
        )

        next_rat_add_state = rat_add_gov_state["nextRatifyState"]
        _check_add_state(gov_state=next_rat_add_state["nextEnactState"])
        reqc.cip038_01.start(url=helpers.get_vcs_link())
        assert next_rat_add_state["ratificationDelayed"], "Ratification not delayed"

        # Check committee state after add action ratification
        rat_add_committee_state = cluster.g_query.get_committee_state()
        conway_common.save_committee_state(
            committee_state=rat_add_committee_state,
            name_template=f"{temp_template}_rat_add_{rat_epoch}",
        )
        reqc.cip011.start(url=helpers.get_vcs_link())
        _check_cc_member1_expired(committee_state=rat_add_committee_state, curr_epoch=rat_epoch)

        xfail_ledger_4001_msgs = set()
        for _cc_member_key in (cc_member1_key, cc_member2_key, cc_member3_key):
            rat_add_member_rec = rat_add_committee_state["committee"].get(_cc_member_key) or {}
            if rat_add_member_rec:
                assert rat_add_member_rec["hotCredsAuthStatus"]["tag"] != "MemberAuthorized", (
                    "CC Member is still authorized"
                )
            else:
                xfail_ledger_4001_msgs.add(
                    "Newly elected CC members are removed during ratification"
                )

        # Authorize hot keys of new CC members. The members were elected and they can already place
        # "prevotes" that will take effect next epoch when the CC members are enacted.
        _auth_hot_keys()

        reqc.cip003.success()

        # Check enactment of add action
        enact_epoch = cluster.wait_for_epoch(epoch_no=rat_epoch + 1, padding_seconds=5)
        enact_add_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=enact_add_gov_state, name_template=f"{temp_template}_enact_add_{enact_epoch}"
        )

        reqc.cip073_03.start(url=helpers.get_vcs_link())
        _check_add_state(gov_state=enact_add_gov_state)
        [r.success() for r in (reqc.cip040, reqc.cip061_01, reqc.cip061_03, reqc.cip073_03)]
        if is_drep_total_below_threshold:
            reqc.cip064_01.success()
        if is_spo_total_below_threshold:
            reqc.cip064_02.success()

        # Check committee state after add action enactment
        enact_add_committee_state = cluster.g_query.get_committee_state()
        conway_common.save_committee_state(
            committee_state=enact_add_committee_state,
            name_template=f"{temp_template}_enact_add_{enact_epoch}",
        )
        _check_cc_member1_expired(committee_state=enact_add_committee_state, curr_epoch=enact_epoch)

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cip009, reqc.cip010)]
        for i, _cc_member_key in enumerate((cc_member1_key, cc_member2_key, cc_member3_key)):
            enact_add_member_rec = enact_add_committee_state["committee"][_cc_member_key]
            assert (
                xfail_ledger_4001_msgs
                or enact_add_member_rec["hotCredsAuthStatus"]["tag"] == "MemberAuthorized"
            ), "CC Member was NOT authorized"
            assert enact_add_member_rec["status"] == "Active", "CC Member should be active"
            assert enact_add_member_rec["expiration"] == cc_members[i].epoch, (
                "Expiration epoch is incorrect"
            )
        [r.success() for r in (reqc.cip009, reqc.cip010, reqc.cip058)]

        # Try to vote on enacted add action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_add_enacted",
                payment_addr=pool_user_lg.payment,
                action_txid=action_add_txid,
                action_ix=action_add_ix,
                approve_drep=False,
                approve_spo=False,
            )
        err_str = str(excinfo.value)
        assert "(GovActionsDoNotExist" in err_str, err_str

        # Check ratification of removal action. The removal action should be ratified at the same
        # time as the add action is enacted, because ratification of new actions was delayed by
        # the add action.
        rat_action = governance_utils.lookup_ratified_actions(
            gov_state=enact_add_gov_state, action_txid=action_rem_txid
        )
        assert rat_action, "Action not found in ratified actions"
        reqc.cip038_01.success()

        # The proposal was enacted, but it is already expired
        assert enact_epoch == actions_epoch + 3, "Unexpected epoch"
        with pytest.raises(clusterlib.CLIError) as excinfo:
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_rem_after_ratification",
                payment_addr=pool_user_lg.payment,
                action_txid=action_rem_txid,
                action_ix=action_rem_ix,
                approve_drep=False,
                approve_spo=False,
            )
        err_str = str(excinfo.value)
        assert "(VotingOnExpiredGovAction" in err_str, err_str

        next_rat_rem_state = enact_add_gov_state["nextRatifyState"]
        _check_cc_member2_removed(gov_state=next_rat_rem_state["nextEnactState"])
        assert next_rat_rem_state["ratificationDelayed"], "Ratification not delayed"

        # Check committee state after ratification
        rat_rem_member_rec = enact_add_committee_state["committee"][cc_member2_key]
        assert rat_rem_member_rec["nextEpochChange"]["tag"] == "ToBeRemoved", (
            "CC Member is not marked for removal"
        )

        # Check enactment of removal action
        rem_epoch = cluster.wait_for_epoch(epoch_no=enact_epoch + 1, padding_seconds=5)
        enact_rem_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=enact_rem_gov_state, name_template=f"{temp_template}_enact_rem_{rem_epoch}"
        )
        _check_cc_member2_removed(gov_state=enact_rem_gov_state)

        # Check committee state after enactment of removal action
        enact_rem_committee_state = cluster.g_query.get_committee_state()
        conway_common.save_committee_state(
            committee_state=enact_rem_committee_state,
            name_template=f"{temp_template}_enact_rem_{rem_epoch}",
        )
        enact_rem_member_rec = enact_rem_committee_state["committee"].get(cc_member2_key)
        assert not enact_rem_member_rec, "Removed committee member still present"

        _check_cc_member1_expired(committee_state=enact_rem_committee_state, curr_epoch=rem_epoch)
        reqc.cip011.success()

        # Check that deposit was returned immediately after enactment
        reqc.cip034en.start(url=helpers.get_vcs_link())
        enact_deposit_returned = cluster.g_query.get_stake_addr_info(
            pool_user_lg.stake.address
        ).reward_account_balance

        assert (
            enact_deposit_returned
            == init_return_account_balance
            + deposit_amt * 2  # 2 * deposit_amt for add and rem actions
        ), "Incorrect return account balance"
        reqc.cip034en.success()

        # Try to vote on enacted removal action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            voted_votes_rem = conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_rem_enacted",
                payment_addr=pool_user_lg.payment,
                action_txid=action_rem_txid,
                action_ix=action_rem_ix,
                approve_drep=True,
                approve_spo=True,
            )
        err_str = str(excinfo.value)
        assert "(GovActionsDoNotExist" in err_str, err_str

        # Resign CC member
        _resign_member(res_member=cc_members[2])
        dbsync_resign_err = ""
        try:
            _url = helpers.get_vcs_link()
            [r.start(url=_url) for r in (reqc.db002, reqc.db004, reqc.db005)]
            _check_resign_dbsync(res_member=cc_members[2])
            [r.success() for r in (reqc.db002, reqc.db004, reqc.db005)]
        except Exception as excp:
            dbsync_resign_err = str(excp)

        # Check action view
        governance_utils.check_action_view(cluster_obj=cluster, action_data=add_cc_action)
        governance_utils.check_action_view(cluster_obj=cluster, action_data=rem_cc_action)

        # Check vote view
        if voted_votes_add.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes_add.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes_add.drep[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes_add.spo[0])
        if voted_votes_rem.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes_rem.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes_rem.drep[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes_rem.spo[0])
        reqc.cip067.success()

        if xfail_ledger_4001_msgs:
            ledger_4001 = issues.ledger_4001.copy()
            ledger_4001.message = "; ".join(xfail_ledger_4001_msgs)
            ledger_4001.finish_test()

        if dbsync_resign_err:
            _msg = f"db-sync error: {dbsync_resign_err}"
            raise AssertionError(_msg)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not configuration.HAS_CC, reason="Runs only on setup with CC")
    @pytest.mark.long
    def test_empty_committee(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_governance: governance_utils.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test electing empty Constitutional Committee.

        * create "protocol parameters update" action to set `committeeMinSize` to 0

            - vote to approve the action
            - check that the action is ratified
            - check that the action is enacted
            - check that the `committeeMinSize` pparam was set to 0

        * create an "update committee" action to remove all CC members

            - vote to approve the action
            - check that the action is ratified
            - check that the action is enacted
            - check that all CC members were removed

        * create a "create constitution" action
            - vote to approve the action without needing CC members votes
            - check that the action is ratified
            - check that the action is enacted
        """
        __: tp.Any  # mypy workaround
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        if conway_common.is_in_bootstrap(cluster_obj=cluster):
            pytest.skip("Cannot run during bootstrap period.")

        deposit_amt = cluster.g_query.get_gov_action_deposit()

        def _set_zero_committee_pparam() -> conway_common.PParamPropRec:
            """Set the `committeeMinSize` pparam to 0."""
            anchor_data = governance_utils.get_default_anchor_data()

            update_proposals = [
                clusterlib_utils.UpdateProposal(
                    arg="--min-committee-size",
                    value=0,
                    name="committeeMinSize",
                )
            ]

            return conway_common.propose_pparams_update(
                cluster_obj=cluster,
                name_template=f"{temp_template}_zero_cc",
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                pool_user=pool_user_lg,
                proposals=update_proposals,
            )

        def _rem_committee() -> tuple[clusterlib.ActionUpdateCommittee, str, int]:
            """Remove all CC members."""
            anchor_data = governance_utils.get_default_anchor_data()
            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.COMMITTEE,
                gov_state=cluster.g_query.get_gov_state(),
            )

            rem_cc_action = cluster.g_governance.action.update_committee(
                action_name=f"{temp_template}_rem",
                deposit_amt=deposit_amt,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                threshold="0.0",
                rem_cc_members=[r.cc_member for r in governance_data.cc_key_members],
                prev_action_txid=prev_action_rec.txid,
                prev_action_ix=prev_action_rec.ix,
                deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
            )

            tx_files_action_rem = clusterlib.TxFiles(
                proposal_files=[rem_cc_action.action_file],
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
                name_template=f"{temp_template}_rem_action",
                src_address=pool_user_lg.payment.address,
                use_build_cmd=True,
                tx_files=tx_files_action_rem,
            )

            out_utxos_action_rem = cluster.g_query.get_utxo(tx_raw_output=tx_output_action)
            assert (
                clusterlib.filter_utxos(
                    utxos=out_utxos_action_rem, address=pool_user_lg.payment.address
                )[0].amount
                == clusterlib.calculate_utxos_balance(tx_output_action.txins)
                - tx_output_action.fee
                - deposit_amt
            ), f"Incorrect balance for source address `{pool_user_lg.payment.address}`"

            action_rem_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
            action_rem_gov_state = cluster.g_query.get_gov_state()
            action_rem_epoch = cluster.g_query.get_epoch()
            conway_common.save_gov_state(
                gov_state=action_rem_gov_state,
                name_template=f"{temp_template}_action_rem_{action_rem_epoch}",
            )
            prop_action_rem = governance_utils.lookup_proposal(
                gov_state=action_rem_gov_state, action_txid=action_rem_txid
            )
            assert prop_action_rem, "Update committee action not found"
            assert (
                prop_action_rem["proposalProcedure"]["govAction"]["tag"]
                == governance_utils.ActionTags.UPDATE_COMMITTEE.value
            ), "Incorrect action tag"

            action_rem_ix = prop_action_rem["actionId"]["govActionIx"]

            return rem_cc_action, action_rem_txid, action_rem_ix

        def _check_rat_gov_state(
            name_template: str, action_txid: str, action_ix: int, epoch_no: int
        ) -> dict[str, tp.Any]:
            gov_state = cluster.g_query.get_gov_state()
            conway_common.save_gov_state(
                gov_state=gov_state, name_template=f"{name_template}_{epoch_no}"
            )
            rat_action = governance_utils.lookup_ratified_actions(
                gov_state=gov_state, action_txid=action_txid, action_ix=action_ix
            )
            assert rat_action, "Action not found in ratified actions"
            return gov_state

        reqc.cip008.start(url=helpers.get_vcs_link())

        # Set `committeeMinSize` to 0

        # Create an action to set the pparam
        zero_cc_proposal = _set_zero_committee_pparam()

        # Vote & approve the action
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_zero_cc_yes",
            payment_addr=pool_user_lg.payment,
            action_txid=zero_cc_proposal.action_txid,
            action_ix=zero_cc_proposal.action_ix,
            approve_cc=True,
            approve_drep=True,
        )
        vote_zero_cc_epoch = cluster.g_query.get_epoch()

        def _check_zero_cc_state(state: dict):
            pparams = state.get("curPParams") or state.get("currentPParams") or {}
            clusterlib_utils.check_updated_params(
                update_proposals=zero_cc_proposal.proposals, protocol_params=pparams
            )

        # Check ratification
        rat_zero_cc_epoch = cluster.wait_for_epoch(
            epoch_no=vote_zero_cc_epoch + 1, padding_seconds=5
        )
        rat_zero_cc_gov_state = _check_rat_gov_state(
            name_template=f"{temp_template}_rat_zero_cc",
            action_txid=zero_cc_proposal.action_txid,
            action_ix=zero_cc_proposal.action_ix,
            epoch_no=rat_zero_cc_epoch,
        )
        next_rat_zero_cc_state = rat_zero_cc_gov_state["nextRatifyState"]
        _check_zero_cc_state(next_rat_zero_cc_state["nextEnactState"])

        # The cluster needs respin after this point
        cluster_manager.set_needs_respin()

        assert not next_rat_zero_cc_state["ratificationDelayed"], (
            "Ratification is delayed unexpectedly"
        )

        # Check enactment
        enact_zero_cc_epoch = cluster.wait_for_epoch(
            epoch_no=rat_zero_cc_epoch + 1, padding_seconds=5
        )
        enact_zero_cc_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=enact_zero_cc_gov_state,
            name_template=f"{temp_template}_enact_zero_cc_{enact_zero_cc_epoch}",
        )
        _check_zero_cc_state(enact_zero_cc_gov_state)

        # Remove all CC members

        # Create an action to remove CC member
        __, action_rem_txid, action_rem_ix = _rem_committee()
        removed_members_hashes = {
            f"keyHash-{r.cc_member.cold_vkey_hash}" for r in governance_data.cc_key_members
        }

        # Vote & approve the action
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_rem_yes",
            payment_addr=pool_user_lg.payment,
            action_txid=action_rem_txid,
            action_ix=action_rem_ix,
            approve_drep=True,
            approve_spo=True,
        )
        vote_rem_epoch = cluster.g_query.get_epoch()

        # Check ratification
        rat_rem_epoch = cluster.wait_for_epoch(epoch_no=vote_rem_epoch + 1, padding_seconds=5)
        rat_rem_gov_state = _check_rat_gov_state(
            name_template=f"{temp_template}_rat_rem",
            action_txid=action_rem_txid,
            action_ix=action_rem_ix,
            epoch_no=rat_rem_epoch,
        )
        next_rat_rem_state = rat_rem_gov_state["nextRatifyState"]
        assert set(next_rat_rem_state["nextEnactState"]["committee"]["members"].keys()).isdisjoint(
            removed_members_hashes
        ), "Removed committee members still present"
        assert next_rat_rem_state["ratificationDelayed"], "Ratification not delayed"

        # Check enactment
        enact_rem_epoch = cluster.wait_for_epoch(epoch_no=rat_rem_epoch + 1, padding_seconds=5)
        enact_rem_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=enact_rem_gov_state,
            name_template=f"{temp_template}_enact_rem_{enact_rem_epoch}",
        )
        assert set(
            conway_common.get_committee_val(data=enact_rem_gov_state)["members"].keys()
        ).isdisjoint(removed_members_hashes), "Removed committee members still present"

        # Check committee state after enactment
        enact_rem_committee_state = cluster.g_query.get_committee_state()
        conway_common.save_committee_state(
            committee_state=enact_rem_committee_state,
            name_template=f"{temp_template}_enact_rem_{enact_rem_epoch}",
        )
        assert set(enact_rem_committee_state["committee"].keys()).isdisjoint(
            removed_members_hashes
        ), "Removed committee members still present"

        # Change Constitution without needing CC votes

        # Create an action to change Constitution
        anchor_data_const = governance_utils.get_default_anchor_data()

        constitution_file = pl.Path(f"{temp_template}_constitution.txt")
        constitution_file.write_text(data="Constitution is here", encoding="utf-8")
        constitution_url = web.publish(file_path=constitution_file)
        constitution_hash = cluster.g_governance.get_anchor_data_hash(file_text=constitution_file)

        (
            const_action,
            action_const_txid,
            action_const_ix,
        ) = conway_common.propose_change_constitution(
            cluster_obj=cluster,
            name_template=f"{temp_template}_constitution",
            anchor_url=anchor_data_const.url,
            anchor_data_hash=anchor_data_const.hash,
            constitution_url=constitution_url,
            constitution_hash=constitution_hash,
            pool_user=pool_user_lg,
        )

        # Make sure we have enough time to submit the votes in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # Vote & approve the action
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_const_yes",
            payment_addr=pool_user_lg.payment,
            action_txid=action_const_txid,
            action_ix=action_const_ix,
            approve_drep=True,
        )
        vote_const_epoch = cluster.g_query.get_epoch()

        def _check_const_state(state: dict):
            anchor = state["constitution"]["anchor"]
            assert anchor["dataHash"] == const_action.constitution_hash, (
                "Incorrect constitution data hash"
            )
            assert anchor["url"] == const_action.constitution_url, "Incorrect constitution data URL"

        # Check ratification
        rat_const_epoch = cluster.wait_for_epoch(epoch_no=vote_const_epoch + 1, padding_seconds=5)
        rat_const_gov_state = _check_rat_gov_state(
            name_template=f"{temp_template}_rat_const",
            action_txid=action_const_txid,
            action_ix=action_const_ix,
            epoch_no=rat_const_epoch,
        )
        next_rat_const_state = rat_const_gov_state["nextRatifyState"]
        _check_const_state(next_rat_const_state["nextEnactState"])
        assert next_rat_const_state["ratificationDelayed"], "Ratification not delayed"

        # Check enactment
        enact_const_epoch = cluster.wait_for_epoch(epoch_no=rat_const_epoch + 1, padding_seconds=5)
        enact_const_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=enact_const_gov_state,
            name_template=f"{temp_template}_enact_const_{enact_const_epoch}",
        )
        _check_const_state(enact_const_gov_state)

        reqc.cip008.success()

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(threshold=st.floats(min_value=1, exclude_min=True, allow_infinity=False))
    @common.hypothesis_settings(max_examples=100)
    def test_update_committee_threshold_out_of_range(
        self, cluster: clusterlib.ClusterLib, pool_user: clusterlib.PoolUser, threshold: float
    ):
        """Test update committee threshold with a value out of range [0,1].

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        deposit_amt = cluster.conway_genesis["govActionDeposit"]
        anchor_data = governance_utils.get_default_anchor_data()
        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.COMMITTEE,
            gov_state=cluster.g_query.get_gov_state(),
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_governance.action.update_committee(
                action_name=temp_template,
                deposit_amt=deposit_amt,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                threshold=str(threshold),
                prev_action_txid=prev_action_rec.txid,
                prev_action_ix=prev_action_rec.ix,
                deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
            )

        err_str = str(excinfo.value)
        assert "Please enter a value in the range [0,1]" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not configuration.HAS_CC, reason="Runs only on setup with CC")
    @pytest.mark.long
    def test_committee_zero_threshold(
        self,
        cluster_lock_governance: governance_utils.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test that actions that require CC approval can be ratified when threshold == 0.

        Even if the CC disapprove the action.

        * set CC threshold to zero
        * submit a "create constitution" action
        * vote to disapprove the action by the CC and approve by the DReps
        * check that the action is ratified
        * check that the action is enacted
        """
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        if conway_common.is_in_bootstrap(cluster_obj=cluster):
            pytest.skip("Cannot run during bootstrap period.")

        def _check_rat_enact_state(
            name_template: str,
            action_txid: str,
            action_type: governance_utils.PrevGovActionIds,
            approval_epoch: int,
        ) -> None:
            # Check ratification
            epoch_rat = cluster.wait_for_epoch(epoch_no=approval_epoch + 1, padding_seconds=5)

            rat_gov_state = cluster.g_query.get_gov_state()
            conway_common.save_gov_state(
                gov_state=rat_gov_state, name_template=f"{name_template}_rat_{epoch_rat}"
            )

            rat_action = governance_utils.lookup_ratified_actions(
                gov_state=rat_gov_state, action_txid=action_txid
            )
            assert rat_action, "Action not found in ratified actions"

            # Wait for enactment
            epoch_enact = cluster.wait_for_epoch(epoch_no=epoch_rat + 1, padding_seconds=5)

            # Check enactment
            enact_gov_state = cluster.g_query.get_gov_state()
            conway_common.save_gov_state(
                gov_state=enact_gov_state, name_template=f"{name_template}_enact_{epoch_enact}"
            )

            prev_action_rec = governance_utils.get_prev_action(
                action_type=action_type,
                gov_state=enact_gov_state,
            )

            assert prev_action_rec.txid == action_txid, "Action not enacted"

        deposit_amt = cluster.g_query.get_gov_action_deposit()
        anchor_data = governance_utils.get_default_anchor_data()
        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.COMMITTEE,
            gov_state=cluster.g_query.get_gov_state(),
        )

        # Set CC threshold to zero
        update_threshold_action = cluster.g_governance.action.update_committee(
            action_name=temp_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_data.url,
            anchor_data_hash=anchor_data.hash,
            threshold="0",
            prev_action_txid=prev_action_rec.txid,
            prev_action_ix=prev_action_rec.ix,
            deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
        )

        tx_files = clusterlib.TxFiles(
            proposal_files=[update_threshold_action.action_file],
            signing_key_files=[
                pool_user_lg.payment.skey_file,
            ],
        )

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_update_threshold",
            src_address=pool_user_lg.payment.address,
            use_build_cmd=True,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=pool_user_lg.payment.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
        ), f"Incorrect balance for source address `{pool_user_lg.payment.address}`"

        threshold_action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output.out_file)
        threshold_action_gov_state = cluster.g_query.get_gov_state()
        threshold_action_epoch = cluster.g_query.get_epoch()
        conway_common.save_gov_state(
            gov_state=threshold_action_gov_state,
            name_template=f"{temp_template}_action_{threshold_action_epoch}",
        )
        prop_action = governance_utils.lookup_proposal(
            gov_state=threshold_action_gov_state, action_txid=threshold_action_txid
        )
        assert prop_action, "Update committee action not found"
        action_ix = prop_action["actionId"]["govActionIx"]

        # Make sure the votes don't happen close to epoch boundary
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=10, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_threshold",
            payment_addr=pool_user_lg.payment,
            action_txid=threshold_action_txid,
            action_ix=action_ix,
            approve_drep=True,
            approve_spo=True,
        )
        vote_threshold_epoch = cluster.g_query.get_epoch()

        _check_rat_enact_state(
            name_template=f"{temp_template}_threshold",
            action_txid=threshold_action_txid,
            action_type=governance_utils.PrevGovActionIds.COMMITTEE,
            approval_epoch=vote_threshold_epoch,
        )

        # Try to ratify a "create constitution" action that is expecting approval from the CC
        anchor_data = governance_utils.get_default_anchor_data()
        constitution_file = pl.Path(f"{temp_template}_constitution.txt")
        constitution_file.write_text(data="Constitution is here", encoding="utf-8")
        constitution_url = web.publish(file_path=constitution_file)
        constitution_hash = cluster.g_governance.get_anchor_data_hash(file_text=constitution_file)
        (
            const_action,
            const_action_txid,
            const_action_ix,
        ) = conway_common.propose_change_constitution(
            cluster_obj=cluster,
            name_template=f"{temp_template}_constitution",
            anchor_url=anchor_data.url,
            anchor_data_hash=anchor_data.hash,
            constitution_url=constitution_url,
            constitution_hash=constitution_hash,
            pool_user=pool_user_lg,
        )

        # Make sure the votes don't happen close to epoch boundary
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=10, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_constitution",
            payment_addr=pool_user_lg.payment,
            action_txid=const_action_txid,
            action_ix=const_action_ix,
            approve_cc=False,
            approve_drep=True,
        )
        vote_const_epoch = cluster.g_query.get_epoch()

        _check_rat_enact_state(
            name_template=f"{temp_template}_constitution",
            action_txid=const_action_txid,
            action_type=governance_utils.PrevGovActionIds.CONSTITUTION,
            approval_epoch=vote_const_epoch,
        )

        # Reinstate the original CC data
        governance_setup.reinstate_committee(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_reinstate",
            pool_user=pool_user_lg,
        )
