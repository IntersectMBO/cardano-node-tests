"""Tests for Conway governance Constitutional Committee functionality."""
# pylint: disable=expression-not-assigned
import logging
import pathlib as pl
import typing as tp

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import blockers
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
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
def payment_addr(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> clusterlib.AddressRecord:
    """Create new payment address."""
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        addr = clusterlib_utils.create_payment_addr_records(
            f"committee_addr_ci{cluster_manager.cluster_instance_num}",
            cluster_obj=cluster,
        )[0]
        fixture_cache.value = addr

    # Fund source address
    clusterlib_utils.fund_from_faucet(
        addr,
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )

    return addr


@pytest.fixture
def pool_user(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> clusterlib.PoolUser:
    """Create a pool user."""
    key = helpers.get_current_line_str()
    return conway_common.get_pool_user(
        cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key
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


class TestCommittee:
    """Tests for Constitutional Committee."""

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_register_and_resign_committee_member(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test Constitutional Committee Member registration and resignation.

        * register a potential CC Member
        * check that CC Member was registered
        * resign from CC Member position
        * check that CC Member resigned
        """
        temp_template = common.get_test_id(cluster)

        # Linked user stories
        req_cli3 = requirements.Req(id="CLI003", group=requirements.GroupsKnown.CHANG_US)
        req_cli4 = requirements.Req(id="CLI004", group=requirements.GroupsKnown.CHANG_US)
        req_cli5 = requirements.Req(id="CLI005", group=requirements.GroupsKnown.CHANG_US)
        req_cli6 = requirements.Req(id="CLI006", group=requirements.GroupsKnown.CHANG_US)
        req_cli7 = requirements.Req(id="CLI007", group=requirements.GroupsKnown.CHANG_US)
        req_cli32 = requirements.Req(id="CLI032", group=requirements.GroupsKnown.CHANG_US)
        req_cip2 = requirements.Req(id="CIP002", group=requirements.GroupsKnown.CHANG_US)
        req_cip3 = requirements.Req(id="CIP003", group=requirements.GroupsKnown.CHANG_US)
        req_cip4 = requirements.Req(id="CIP004", group=requirements.GroupsKnown.CHANG_US)
        req_cip12 = requirements.Req(id="CIP012", group=requirements.GroupsKnown.CHANG_US)

        # Register a potential CC Member

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (req_cli3, req_cli4, req_cli5, req_cli6, req_cip3)]
        cc_auth_record = governance_utils.get_cc_member_auth_record(
            cluster_obj=cluster,
            name_template=temp_template,
        )
        [r.success() for r in (req_cli3, req_cli4, req_cli5, req_cli6)]

        tx_files_auth = clusterlib.TxFiles(
            certificate_files=[cc_auth_record.auth_cert],
            signing_key_files=[payment_addr.skey_file, cc_auth_record.cold_key_pair.skey_file],
        )

        tx_output_auth = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_auth",
            src_address=payment_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files_auth,
        )
        req_cip3.success()

        auth_out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_auth)
        assert (
            clusterlib.filter_utxos(utxos=auth_out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_auth.txins) - tx_output_auth.fee
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (req_cli32, req_cip2, req_cip4)]
        auth_committee_state = cluster.g_conway_governance.query.committee_state()
        member_key = f"keyHash-{cc_auth_record.key_hash}"
        member_rec = auth_committee_state["committee"][member_key]
        assert (
            member_rec["hotCredsAuthStatus"]["tag"] == "MemberAuthorized"
        ), "CC Member was NOT authorized"
        assert not member_rec["expiration"], "CC Member should not be elected"
        assert member_rec["status"] == "Unrecognized", "CC Member should not be recognized"
        [r.success() for r in (req_cli32, req_cip2, req_cip4)]

        # Resignation of CC Member

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (req_cli7, req_cip12)]
        res_cert = cluster.g_conway_governance.committee.gen_cold_key_resignation_cert(
            key_name=temp_template,
            cold_vkey_file=cc_auth_record.cold_key_pair.vkey_file,
            resignation_metadata_url="http://www.cc-resign.com",
            resignation_metadata_hash="5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d",
        )
        req_cli7.success()

        tx_files_res = clusterlib.TxFiles(
            certificate_files=[res_cert],
            signing_key_files=[payment_addr.skey_file, cc_auth_record.cold_key_pair.skey_file],
        )

        tx_output_res = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_res",
            src_address=payment_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files_res,
        )

        cluster.wait_for_new_block(new_blocks=2)
        res_committee_state = cluster.g_conway_governance.query.committee_state()
        assert (
            res_committee_state["committee"][member_key]["hotCredsAuthStatus"]["tag"]
            == "MemberResigned"
        ), "CC Member not resigned"
        req_cip12.success()

        res_out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_res)
        assert (
            clusterlib.filter_utxos(utxos=res_out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_res.txins) - tx_output_res.fee
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        # Check CC member in db-sync
        dbsync_utils.check_committee_member_registration(
            cc_member_cold_key=cc_auth_record.key_hash, committee_state=auth_committee_state
        )
        dbsync_utils.check_committee_member_deregistration(
            cc_member_cold_key=cc_auth_record.key_hash
        )

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    def test_update_commitee_action(
        self,
        cluster: clusterlib.ClusterLib,
        pool_user: clusterlib.PoolUser,
        use_build_cmd: bool,
        submit_method: str,
    ):
        temp_template = common.get_test_id(cluster)
        cc_size = 3

        # Linked user stories
        req_cip7 = requirements.Req(id="CIP007", group=requirements.GroupsKnown.CHANG_US)

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
                hot_vkey_file=r.hot_key_pair.vkey_file,
                hot_skey_file=r.hot_key_pair.skey_file,
            )
            for r in cc_auth_records
        ]

        deposit_amt = cluster.conway_genesis["govActionDeposit"]
        anchor_url = "http://www.cc-update.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"
        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.COMMITTEE,
            gov_state=cluster.g_conway_governance.query.gov_state(),
        )

        req_cip7.start(url=helpers.get_vcs_link())
        update_action = cluster.g_conway_governance.action.update_committee(
            action_name=temp_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            quorum="2/3",
            add_cc_members=cc_members,
            prev_action_txid=prev_action_rec.txid,
            prev_action_ix=prev_action_rec.ix,
            deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[r.auth_cert for r in cc_auth_records],
            proposal_files=[update_action.action_file],
            signing_key_files=[
                pool_user.payment.skey_file,
                *[r.cold_key_pair.skey_file for r in cc_auth_records],
            ],
        )

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
        gov_state = cluster.g_conway_governance.query.gov_state()
        prop = governance_utils.lookup_proposal(gov_state=gov_state, action_txid=txid)
        assert prop, "Update committee action not found"
        assert prop["action"]["tag"] == "UpdateCommittee", "Incorrect action tag"
        assert prop["action"]["contents"][3] == 2 / 3
        req_cip7.success()

    @allure.link(helpers.get_vcs_link())
    def test_add_rm_committee_members(  # noqa: C901
        self,
        cluster_lock_governance: governance_setup.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
        testfile_temp_dir: pl.Path,
        request: FixtureRequest,
    ):
        """Test adding and removing CC members.

        * authorize hot keys of 3 new potential CC members
        * create an "update committee" action to add 2 of the 3 new potential CC members

            - vote to disapprove the action
            - vote to approve the action
            - check that CC members votes have no effect
            - check that the action is ratified
            - check that the action is enacted
            - check that the new CC members were added
            - check that it's not possible to vote on enacted action

        * create an "update committee" action to remove 1 CC member

            - vote to disapprove the action
            - vote to approve the action
            - check that CC members votes have no effect
            - check that the action is ratified
            - check that the action is enacted
            - check that the CC member was removed
            - check that it's not possible to vote on enacted action

        * check output of votes and action `view` commands
        """
        # pylint: disable=too-many-locals,too-many-statements,too-many-branches
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.conway_genesis["govActionDeposit"]

        # Linked user stories
        req_cli14 = requirements.Req(id="CLI014", group=requirements.GroupsKnown.CHANG_US)
        req_cip9 = requirements.Req(id="CIP009", group=requirements.GroupsKnown.CHANG_US)
        req_cip5 = requirements.Req(id="CIP005", group=requirements.GroupsKnown.CHANG_US)
        req_cip10 = requirements.Req(id="CIP010", group=requirements.GroupsKnown.CHANG_US)

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

        cc_auth_record3 = governance_utils.get_cc_member_auth_record(
            cluster_obj=cluster,
            name_template=f"{temp_template}_member3",
        )
        cc_member3_key = f"keyHash-{cc_auth_record3.key_hash}"

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

        auth_committee_state = cluster.g_conway_governance.query.committee_state()
        _cur_epoch = cluster.g_query.get_epoch()
        conway_common.save_committee_state(
            committee_state=auth_committee_state,
            name_template=f"{temp_template}_auth_{_cur_epoch}",
        )
        for mk in (cc_member1_key, cc_member2_key, cc_member3_key):
            auth_member_rec = auth_committee_state["committee"][mk]
            assert (
                auth_member_rec["hotCredsAuthStatus"]["tag"] == "MemberAuthorized"
            ), "CC Member was NOT authorized"
            assert not auth_member_rec["expiration"], "CC Member should not be elected"
            assert auth_member_rec["status"] == "Unrecognized", "CC Member should not be recognized"

        # New CC members
        cc_members = [
            clusterlib.CCMember(
                epoch=cluster.g_query.get_epoch() + 5,
                cold_vkey_file=r.cold_key_pair.vkey_file,
                cold_skey_file=r.cold_key_pair.skey_file,
                hot_vkey_file=r.hot_key_pair.vkey_file,
                hot_skey_file=r.hot_key_pair.skey_file,
            )
            for r in (cc_auth_record1, cc_auth_record2)
        ]

        def _add_members() -> tp.Tuple[clusterlib.ActionUpdateCommittee, str, int]:
            """Add new CC members."""
            anchor_url_add = "http://www.cc-add.com"
            anchor_data_hash_add = (
                "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"
            )
            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.COMMITTEE,
                gov_state=cluster.g_conway_governance.query.gov_state(),
            )

            req_cli14.start(url=helpers.get_vcs_link())
            add_cc_action = cluster.g_conway_governance.action.update_committee(
                action_name=f"{temp_template}_add",
                deposit_amt=deposit_amt,
                anchor_url=anchor_url_add,
                anchor_data_hash=anchor_data_hash_add,
                quorum=str(cluster.conway_genesis["committee"]["quorum"]),
                add_cc_members=cc_members,
                prev_action_txid=prev_action_rec.txid,
                prev_action_ix=prev_action_rec.ix,
                deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
            )
            req_cli14.success()

            tx_files_action_add = clusterlib.TxFiles(
                proposal_files=[add_cc_action.action_file],
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
            action_add_gov_state = cluster.g_conway_governance.query.gov_state()
            _cur_epoch = cluster.g_query.get_epoch()
            conway_common.save_gov_state(
                gov_state=action_add_gov_state,
                name_template=f"{temp_template}_action_add_{_cur_epoch}",
            )
            prop_action_add = governance_utils.lookup_proposal(
                gov_state=action_add_gov_state, action_txid=action_add_txid
            )
            assert prop_action_add, "Update committee action not found"
            assert (
                prop_action_add["action"]["tag"]
                == governance_utils.ActionTags.UPDATE_COMMITTEE.value
            ), "Incorrect action tag"

            action_add_ix = prop_action_add["actionId"]["govActionIx"]

            return add_cc_action, action_add_txid, action_add_ix

        def _rem_member() -> tp.Tuple[clusterlib.ActionUpdateCommittee, str, int]:
            """Remove the CC member."""
            anchor_url_rem = "http://www.cc-rem.com"
            anchor_data_hash_rem = (
                "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"
            )
            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.COMMITTEE,
                gov_state=cluster.g_conway_governance.query.gov_state(),
            )

            req_cip5.start(url=helpers.get_vcs_link())
            rem_cc_action = cluster.g_conway_governance.action.update_committee(
                action_name=f"{temp_template}_rem",
                deposit_amt=deposit_amt,
                anchor_url=anchor_url_rem,
                anchor_data_hash=anchor_data_hash_rem,
                quorum=str(cluster.conway_genesis["committee"]["quorum"]),
                rem_cc_members=[cc_members[1]],
                prev_action_txid=prev_action_rec.txid,
                prev_action_ix=prev_action_rec.ix,
                deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
            )
            req_cip5.success()

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
            action_rem_gov_state = cluster.g_conway_governance.query.gov_state()
            _cur_epoch = cluster.g_query.get_epoch()
            conway_common.save_gov_state(
                gov_state=action_rem_gov_state,
                name_template=f"{temp_template}_action_rem_{_cur_epoch}",
            )
            prop_action_rem = governance_utils.lookup_proposal(
                gov_state=action_rem_gov_state, action_txid=action_rem_txid
            )
            assert prop_action_rem, "Update committee action not found"
            assert (
                prop_action_rem["action"]["tag"]
                == governance_utils.ActionTags.UPDATE_COMMITTEE.value
            ), "Incorrect action tag"

            action_rem_ix = prop_action_rem["actionId"]["govActionIx"]

            return rem_cc_action, action_rem_txid, action_rem_ix

        def _resign():
            """Resign the CC members so it doesn't affect voting."""
            with helpers.change_cwd(testfile_temp_dir):
                res_certs = [
                    cluster.g_conway_governance.committee.gen_cold_key_resignation_cert(
                        key_name=f"{temp_template}_res{i}",
                        cold_vkey_file=r.cold_key_pair.vkey_file,
                        resignation_metadata_url="http://www.cc-resign.com",
                        resignation_metadata_hash="5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d",
                    )
                    for i, r in enumerate((cc_auth_record1, cc_auth_record2))
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
                    name_template=f"{temp_template}_res",
                    src_address=pool_user_lg.payment.address,
                    use_build_cmd=True,
                    tx_files=tx_files_res,
                )

        resign_requested = [False]

        def _cast_vote(
            approve: bool,
            vote_id: str,
            action_txid: str,
            action_ix: int,
            add_cc_votes: bool = False,
        ) -> conway_common.VotedVotes:
            """Cast a vote."""
            _votes_drep = [
                None  # This DRep doesn't vote, his votes count as "No"
                if i % 3 == 0
                else cluster.g_conway_governance.vote.create_drep(
                    vote_name=f"{temp_template}_{vote_id}_drep{i}",
                    action_txid=action_txid,
                    action_ix=action_ix,
                    vote=conway_common.get_yes_abstain_vote(i) if approve else clusterlib.Votes.NO,
                    drep_vkey_file=d.key_pair.vkey_file,
                )
                for i, d in enumerate(governance_data.dreps_reg, start=1)
            ]
            votes_drep = [r for r in _votes_drep if r]
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
            conway_common.save_gov_state(
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

            return conway_common.VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo)

        # Add new CC members

        # Create an action to add new CC members
        add_cc_action, action_add_txid, action_add_ix = _add_members()

        # Vote & disapprove the action
        _cast_vote(
            approve=False, vote_id="add_no", action_txid=action_add_txid, action_ix=action_add_ix
        )

        # Vote & approve the action
        _cast_vote(
            approve=True, vote_id="add_yes", action_txid=action_add_txid, action_ix=action_add_ix
        )

        # Check that CC members votes on "update committee" action are ignored
        voted_votes_add = _cast_vote(
            approve=True,
            vote_id="add_with_ccs",
            add_cc_votes=True,
            action_txid=action_add_txid,
            action_ix=action_add_ix,
        )

        def _check_add_state(state: dict):
            for i, _cc_member_key in enumerate((cc_member1_key, cc_member2_key)):
                cc_member_val = state["committee"]["members"].get(_cc_member_key)
                assert cc_member_val, "New committee member not found"
                assert cc_member_val == cc_members[i].epoch

        # Check ratification
        xfail_ledger_3979_msgs = set()
        for __ in range(3):
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            rat_add_gov_state = cluster.g_conway_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=rat_add_gov_state, name_template=f"{temp_template}_rat_add_{_cur_epoch}"
            )
            rem_action = governance_utils.lookup_removed_actions(
                gov_state=rat_add_gov_state, action_txid=action_add_txid
            )
            if rem_action:
                break

            # Known ledger issue where only one expired action gets removed in one epoch.
            # See https://github.com/IntersectMBO/cardano-ledger/issues/3979
            if not rem_action and conway_common.possible_rem_issue(
                gov_state=rat_add_gov_state, epoch=_cur_epoch
            ):
                xfail_ledger_3979_msgs.add("Only single expired action got removed")
                continue

            raise AssertionError("Action not found in removed actions")

        next_rat_add_state = rat_add_gov_state["nextRatifyState"]
        _check_add_state(next_rat_add_state["nextEnactState"])
        assert next_rat_add_state["ratificationDelayed"], "Ratification not delayed"

        # Check committee state after ratification
        rat_add_committee_state = cluster.g_conway_governance.query.committee_state()
        conway_common.save_committee_state(
            committee_state=rat_add_committee_state,
            name_template=f"{temp_template}_rat_add_{_cur_epoch}",
        )

        has_ledger_issue_4001 = False
        for _cc_member_key in (cc_member1_key, cc_member2_key):
            rat_add_member_rec = rat_add_committee_state["committee"].get(_cc_member_key) or {}
            if rat_add_member_rec:
                assert (
                    rat_add_member_rec["hotCredsAuthStatus"]["tag"] == "MemberAuthorized"
                ), "CC Member is no longer authorized"
            else:
                has_ledger_issue_4001 = True

        assert not rat_add_committee_state["committee"].get(
            cc_member3_key
        ), "Non-elected unrecognized CC member was not removed"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_add_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=enact_add_gov_state, name_template=f"{temp_template}_enact_add_{_cur_epoch}"
        )
        _check_add_state(enact_add_gov_state["enactState"])

        # Check committee state after enactment
        enact_add_committee_state = cluster.g_conway_governance.query.committee_state()
        conway_common.save_committee_state(
            committee_state=enact_add_committee_state,
            name_template=f"{temp_template}_enact_add_{_cur_epoch}",
        )
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (req_cip9, req_cip10)]
        for i, _cc_member_key in enumerate((cc_member1_key, cc_member2_key)):
            enact_add_member_rec = enact_add_committee_state["committee"][_cc_member_key]
            assert (
                has_ledger_issue_4001
                or enact_add_member_rec["hotCredsAuthStatus"]["tag"] == "MemberAuthorized"
            ), "CC Member was NOT authorized"
            assert enact_add_member_rec["status"] == "Active", "CC Member should be active"
            assert (
                enact_add_member_rec["expiration"] == cc_members[i].epoch
            ), "Expiration epoch is incorrect"
        [r.success() for r in (req_cip9, req_cip10)]

        # Try to vote on enacted action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _cast_vote(
                approve=False,
                vote_id="add_enacted",
                action_txid=action_add_txid,
                action_ix=action_add_ix,
            )
        err_str = str(excinfo.value)
        assert "(GovActionsDoNotExist" in err_str, err_str

        # Remove a CC member

        # Create an action to remove CC member
        rem_cc_action, action_rem_txid, action_rem_ix = _rem_member()

        # Vote & disapprove the action
        _cast_vote(
            approve=False, vote_id="rem_no", action_txid=action_rem_txid, action_ix=action_rem_ix
        )

        # Vote & approve the action
        _cast_vote(
            approve=True, vote_id="rem_yes", action_txid=action_rem_txid, action_ix=action_rem_ix
        )

        # Check that CC members votes on "update committee" action are ignored
        voted_votes_rem = _cast_vote(
            approve=True,
            vote_id="rem_with_ccs",
            add_cc_votes=True,
            action_txid=action_rem_txid,
            action_ix=action_rem_ix,
        )

        def _check_rem_state(state: dict):
            cc_member_val = state["committee"]["members"].get(cc_member2_key)
            assert not cc_member_val, "Removed committee member still present"

        # Check ratification
        for __ in range(3):
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            rat_rem_gov_state = cluster.g_conway_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=rat_rem_gov_state, name_template=f"{temp_template}_rat_rem_{_cur_epoch}"
            )
            rem_action = governance_utils.lookup_removed_actions(
                gov_state=rat_rem_gov_state, action_txid=action_rem_txid
            )
            if rem_action:
                break

            # Known ledger issue where only one expired action gets removed in one epoch.
            # See https://github.com/IntersectMBO/cardano-ledger/issues/3979
            if not rem_action and conway_common.possible_rem_issue(
                gov_state=rat_rem_gov_state, epoch=_cur_epoch
            ):
                xfail_ledger_3979_msgs.add("Only single expired action got removed")
                continue

            raise AssertionError("Action not found in removed actions")

        next_rat_rem_state = rat_rem_gov_state["nextRatifyState"]
        _check_rem_state(next_rat_rem_state["nextEnactState"])
        assert next_rat_rem_state["ratificationDelayed"], "Ratification not delayed"

        # Check committee state after ratification
        rat_rem_committee_state = cluster.g_conway_governance.query.committee_state()
        conway_common.save_committee_state(
            committee_state=rat_rem_committee_state,
            name_template=f"{temp_template}_rat_rem_{_cur_epoch}",
        )

        rat_rem_member_rec = rat_rem_committee_state["committee"][cc_member2_key]
        assert (
            rat_rem_member_rec["hotCredsAuthStatus"]["tag"] == "MemberNotAuthorized"
        ), "CC Member is still authorized"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_rem_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=enact_rem_gov_state, name_template=f"{temp_template}_enact_rem_{_cur_epoch}"
        )
        _check_rem_state(enact_rem_gov_state["enactState"])

        # Check committee state after enactment
        enact_rem_committee_state = cluster.g_conway_governance.query.committee_state()
        conway_common.save_committee_state(
            committee_state=enact_rem_committee_state,
            name_template=f"{temp_template}_enact_rem_{_cur_epoch}",
        )
        enact_rem_member_rec = enact_rem_committee_state["committee"].get(cc_member2_key)
        assert not enact_rem_member_rec, "Removed committee member still present"

        # Try to vote on enacted action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _cast_vote(
                approve=False,
                vote_id="rem_enacted",
                action_txid=action_rem_txid,
                action_ix=action_rem_ix,
            )
        err_str = str(excinfo.value)
        assert "(GovActionsDoNotExist" in err_str, err_str

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

        known_issues = []
        if xfail_ledger_3979_msgs:
            known_issues.append(
                blockers.GH(
                    issue=3979,
                    repo="IntersectMBO/cardano-ledger",
                    message="; ".join(xfail_ledger_3979_msgs),
                    check_on_devel=False,
                )
            )
        if has_ledger_issue_4001:
            known_issues.append(
                blockers.GH(
                    issue=4001,
                    repo="IntersectMBO/cardano-ledger",
                    message="Newly elected CC members are removed during ratification",
                    check_on_devel=False,
                )
            )
        if known_issues:
            blockers.finish_test(issues=known_issues)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not configuration.HAS_CC, reason="Runs only on setup with CC")
    def test_empty_committee(  # noqa: C901
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_governance: governance_setup.GovClusterT,
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
        # pylint: disable=too-many-locals,too-many-statements,too-many-branches
        __: tp.Any  # mypy workaround
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.conway_genesis["govActionDeposit"]

        # Linked user stories
        req_cip8 = requirements.Req(id="CIP008", group=requirements.GroupsKnown.CHANG_US)

        xfail_ledger_3979_msgs = set()

        def _set_zero_committee_pparam() -> (
            tp.Tuple[tp.List[clusterlib_utils.UpdateProposal], str, int]
        ):
            """Set the `committeeMinSize` pparam to 0."""
            anchor_url = "http://www.pparam-cc-min-size.com"
            anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.PPARAM_UPDATE,
                gov_state=cluster.g_conway_governance.query.gov_state(),
            )

            update_proposals = [
                clusterlib_utils.UpdateProposal(
                    arg="--min-committee-size",
                    value=0,
                    name="committeeMinSize",
                )
            ]
            update_args = clusterlib_utils.get_pparams_update_args(
                update_proposals=update_proposals
            )

            pparams_action = cluster.g_conway_governance.action.create_pparams_update(
                action_name=f"{temp_template}_zero_cc",
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
                name_template=f"{temp_template}_zero_cc_action",
                src_address=pool_user_lg.payment.address,
                use_build_cmd=True,
                tx_files=tx_files_action,
            )

            out_utxos_action = cluster.g_query.get_utxo(tx_raw_output=tx_output_action)
            assert (
                clusterlib.filter_utxos(
                    utxos=out_utxos_action, address=pool_user_lg.payment.address
                )[0].amount
                == clusterlib.calculate_utxos_balance(tx_output_action.txins)
                - tx_output_action.fee
                - deposit_amt
            ), f"Incorrect balance for source address `{pool_user_lg.payment.address}`"

            action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
            action_gov_state = cluster.g_conway_governance.query.gov_state()
            _cur_epoch = cluster.g_query.get_epoch()
            conway_common.save_gov_state(
                gov_state=action_gov_state,
                name_template=f"{temp_template}_zero_cc_action_{_cur_epoch}",
            )
            prop_action = governance_utils.lookup_proposal(
                gov_state=action_gov_state, action_txid=action_txid
            )
            assert prop_action, "Param update action not found"
            assert (
                prop_action["action"]["tag"] == governance_utils.ActionTags.PARAMETER_CHANGE.value
            ), "Incorrect action tag"

            action_ix = prop_action["actionId"]["govActionIx"]

            return update_proposals, action_txid, action_ix

        def _rem_committee() -> tp.Tuple[clusterlib.ActionUpdateCommittee, str, int]:
            """Remove all CC members."""
            anchor_url_rem = "http://www.cc-rem-all.com"
            anchor_data_hash_rem = (
                "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"
            )
            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.COMMITTEE,
                gov_state=cluster.g_conway_governance.query.gov_state(),
            )

            rem_cc_action = cluster.g_conway_governance.action.update_committee(
                action_name=f"{temp_template}_rem",
                deposit_amt=deposit_amt,
                anchor_url=anchor_url_rem,
                anchor_data_hash=anchor_data_hash_rem,
                quorum="0.0",
                rem_cc_members=governance_data.cc_members,
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
            action_rem_gov_state = cluster.g_conway_governance.query.gov_state()
            _cur_epoch = cluster.g_query.get_epoch()
            conway_common.save_gov_state(
                gov_state=action_rem_gov_state,
                name_template=f"{temp_template}_action_rem_{_cur_epoch}",
            )
            prop_action_rem = governance_utils.lookup_proposal(
                gov_state=action_rem_gov_state, action_txid=action_rem_txid
            )
            assert prop_action_rem, "Update committee action not found"
            assert (
                prop_action_rem["action"]["tag"]
                == governance_utils.ActionTags.UPDATE_COMMITTEE.value
            ), "Incorrect action tag"

            action_rem_ix = prop_action_rem["actionId"]["govActionIx"]

            return rem_cc_action, action_rem_txid, action_rem_ix

        def _change_constitution() -> tp.Tuple[clusterlib.ActionConstitution, str, int]:
            """Change constitution."""
            anchor_url = "http://www.const-action.com"
            anchor_data_hash = cluster.g_conway_governance.get_anchor_data_hash(text=anchor_url)

            constitution_url = "http://www.const-new.com"
            constitution_hash = cluster.g_conway_governance.get_anchor_data_hash(
                text=constitution_url
            )

            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.CONSTITUTION,
                gov_state=cluster.g_conway_governance.query.gov_state(),
            )

            constitution_action = cluster.g_conway_governance.action.create_constitution(
                action_name=f"{temp_template}_constitution",
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
                name_template=f"{temp_template}_constitution_action",
                src_address=pool_user_lg.payment.address,
                use_build_cmd=True,
                tx_files=tx_files_action,
            )

            out_utxos_action = cluster.g_query.get_utxo(tx_raw_output=tx_output_action)
            assert (
                clusterlib.filter_utxos(
                    utxos=out_utxos_action, address=pool_user_lg.payment.address
                )[0].amount
                == clusterlib.calculate_utxos_balance(tx_output_action.txins)
                - tx_output_action.fee
                - deposit_amt
            ), f"Incorrect balance for source address `{pool_user_lg.payment.address}`"

            action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
            action_gov_state = cluster.g_conway_governance.query.gov_state()
            _cur_epoch = cluster.g_query.get_epoch()
            conway_common.save_gov_state(
                gov_state=action_gov_state,
                name_template=f"{temp_template}_constitution_action_{_cur_epoch}",
            )
            prop_action = governance_utils.lookup_proposal(
                gov_state=action_gov_state, action_txid=action_txid
            )
            assert prop_action, "Create constitution action not found"
            assert (
                prop_action["action"]["tag"] == governance_utils.ActionTags.NEW_CONSTITUTION.value
            ), "Incorrect action tag"

            action_ix = prop_action["actionId"]["govActionIx"]

            return constitution_action, action_txid, action_ix

        def _cast_vote(
            approve: bool,
            vote_id: str,
            action_txid: str,
            action_ix: int,
            add_cc_votes: bool,
            add_spo_votes: bool,
        ) -> conway_common.VotedVotes:
            """Cast a vote."""
            votes_drep = [
                cluster.g_conway_governance.vote.create_drep(
                    vote_name=f"{temp_template}_{vote_id}_drep{i}",
                    action_txid=action_txid,
                    action_ix=action_ix,
                    vote=conway_common.get_yes_abstain_vote(i) if approve else clusterlib.Votes.NO,
                    drep_vkey_file=d.key_pair.vkey_file,
                )
                for i, d in enumerate(governance_data.dreps_reg, start=1)
            ]

            votes_cc = []
            if add_cc_votes:
                votes_cc = [
                    cluster.g_conway_governance.vote.create_committee(
                        vote_name=f"{temp_template}_{vote_id}_cc{i}",
                        action_txid=action_txid,
                        action_ix=action_ix,
                        vote=conway_common.get_yes_abstain_vote(i)
                        if approve
                        else clusterlib.Votes.NO,
                        cc_hot_vkey_file=m.hot_vkey_file,
                        anchor_url="http://www.cc-vote.com",
                        anchor_data_hash="5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d",
                    )
                    for i, m in enumerate(governance_data.cc_members, start=1)
                ]

            votes_spo = []
            if add_spo_votes:
                votes_spo = [
                    cluster.g_conway_governance.vote.create_spo(
                        vote_name=f"{temp_template}_{vote_id}_pool{i}",
                        action_txid=action_txid,
                        action_ix=action_ix,
                        vote=conway_common.get_yes_abstain_vote(i)
                        if approve
                        else clusterlib.Votes.NO,
                        cold_vkey_file=p.vkey_file,
                    )
                    for i, p in enumerate(governance_data.pools_cold, start=1)
                ]

            cc_keys = [r.hot_skey_file for r in governance_data.cc_members] if votes_cc else []
            spo_keys = [r.skey_file for r in governance_data.pools_cold] if votes_spo else []
            tx_files_vote = clusterlib.TxFiles(
                vote_files=[
                    *[r.vote_file for r in votes_drep],
                    *[r.vote_file for r in votes_cc],
                    *[r.vote_file for r in votes_spo],
                ],
                signing_key_files=[
                    pool_user_lg.payment.skey_file,
                    *[r.key_pair.skey_file for r in governance_data.dreps_reg],
                    *cc_keys,
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
            assert prop_vote["dRepVotes"], "No DRep votes"
            if votes_cc:
                assert prop_vote["committeeVotes"], "No committee votes"
            if votes_spo:
                assert prop_vote["stakePoolVotes"], "No stake pool votes"

            return conway_common.VotedVotes(cc=[], drep=votes_drep, spo=votes_spo)

        def _check_rat_gov_state(
            name_template: str, action_txid: str, action_ix: int
        ) -> tp.Dict[str, tp.Any]:
            for __ in range(3):
                _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
                gov_state = cluster.g_conway_governance.query.gov_state()
                conway_common.save_gov_state(
                    gov_state=gov_state, name_template=f"{name_template}_{_cur_epoch}"
                )
                rem_action = governance_utils.lookup_removed_actions(
                    gov_state=gov_state, action_txid=action_txid, action_ix=action_ix
                )
                if rem_action:
                    return gov_state

                # Known ledger issue where only one expired action gets removed in one epoch.
                # See https://github.com/IntersectMBO/cardano-ledger/issues/3979
                if not rem_action and conway_common.possible_rem_issue(
                    gov_state=gov_state, epoch=_cur_epoch
                ):
                    xfail_ledger_3979_msgs.add("Only single expired action got removed")
                    continue

                raise AssertionError("Action not found in removed actions")

            return {}

        req_cip8.start(url=helpers.get_vcs_link())

        # Set `committeeMinSize` to 0

        # Create an action to set the pparam
        zero_cc_update_proposals, zero_cc_txid, zero_cc_ix = _set_zero_committee_pparam()

        # Vote & approve the action
        _cast_vote(
            approve=True,
            vote_id="zero_cc_yes",
            action_txid=zero_cc_txid,
            action_ix=zero_cc_ix,
            add_cc_votes=True,
            add_spo_votes=False,
        )

        def _check_zero_cc_state(state: dict):
            pparams = state["curPParams"]
            clusterlib_utils.check_updated_params(
                update_proposals=zero_cc_update_proposals, protocol_params=pparams
            )

        # Check ratification
        rat_zero_cc_gov_state = _check_rat_gov_state(
            name_template=f"{temp_template}_rat_zero_cc",
            action_txid=zero_cc_txid,
            action_ix=zero_cc_ix,
        )
        next_rat_zero_cc_state = rat_zero_cc_gov_state["nextRatifyState"]
        _check_zero_cc_state(next_rat_zero_cc_state["nextEnactState"])

        # The cluster needs respin after this point
        cluster_manager.set_needs_respin()

        assert not next_rat_zero_cc_state[
            "ratificationDelayed"
        ], "Ratification is delayed unexpectedly"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_zero_cc_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=enact_zero_cc_gov_state,
            name_template=f"{temp_template}_enact_zero_cc_{_cur_epoch}",
        )
        _check_zero_cc_state(enact_zero_cc_gov_state["enactState"])

        # Remove all CC members

        # Create an action to remove CC member
        __, action_rem_txid, action_rem_ix = _rem_committee()

        # Vote & approve the action
        _cast_vote(
            approve=True,
            vote_id="rem_yes",
            action_txid=action_rem_txid,
            action_ix=action_rem_ix,
            add_cc_votes=False,
            add_spo_votes=True,
        )

        # Check ratification
        rat_rem_gov_state = _check_rat_gov_state(
            name_template=f"{temp_template}_rat_rem",
            action_txid=action_rem_txid,
            action_ix=action_rem_ix,
        )
        next_rat_rem_state = rat_rem_gov_state["nextRatifyState"]
        assert not next_rat_rem_state["nextEnactState"]["committee"][
            "members"
        ], "Removed committee members still present"
        assert next_rat_rem_state["ratificationDelayed"], "Ratification not delayed"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_rem_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=enact_rem_gov_state, name_template=f"{temp_template}_enact_rem_{_cur_epoch}"
        )
        assert not enact_rem_gov_state["enactState"]["committee"][
            "members"
        ], "Removed committee members still present"

        # Check committee state after enactment
        enact_rem_committee_state = cluster.g_conway_governance.query.committee_state()
        conway_common.save_committee_state(
            committee_state=enact_rem_committee_state,
            name_template=f"{temp_template}_enact_rem_{_cur_epoch}",
        )
        assert not enact_rem_committee_state["committee"], "Removed committee members still present"

        # Change Constitution without needing CC votes

        # Create an action to change Constitution
        const_action, action_const_txid, action_const_ix = _change_constitution()

        # Vote & approve the action
        _cast_vote(
            approve=True,
            vote_id="const_yes",
            action_txid=action_const_txid,
            action_ix=action_const_ix,
            add_cc_votes=False,
            add_spo_votes=False,
        )

        def _check_const_state(state: dict):
            anchor = state["constitution"]["anchor"]
            assert (
                anchor["dataHash"] == const_action.constitution_hash
            ), "Incorrect constitution data hash"
            assert anchor["url"] == const_action.constitution_url, "Incorrect constitution data URL"

        # Check ratification
        rat_const_gov_state = _check_rat_gov_state(
            name_template=f"{temp_template}_rat_const",
            action_txid=action_const_txid,
            action_ix=action_const_ix,
        )
        next_rat_const_state = rat_const_gov_state["nextRatifyState"]
        _check_const_state(next_rat_const_state["nextEnactState"])
        assert next_rat_const_state["ratificationDelayed"], "Ratification not delayed"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_const_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=enact_const_gov_state,
            name_template=f"{temp_template}_enact_const_{_cur_epoch}",
        )
        _check_const_state(enact_const_gov_state["enactState"])

        req_cip8.success()

        known_issues = []
        if xfail_ledger_3979_msgs:
            known_issues.append(
                blockers.GH(
                    issue=3979,
                    repo="IntersectMBO/cardano-ledger",
                    message="; ".join(xfail_ledger_3979_msgs),
                    check_on_devel=False,
                )
            )
        if known_issues:
            blockers.finish_test(issues=known_issues)
