"""Tests for Conway governance Constitutional Committee functionality."""
# pylint: disable=expression-not-assigned
import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
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
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        test_id = common.get_test_id(cluster)
        pool_user = clusterlib_utils.create_pool_users(
            cluster_obj=cluster,
            name_template=f"{test_id}_pool_user",
            no_of_addr=1,
        )[0]
        fixture_cache.value = pool_user

    # Fund the payment address with some ADA
    clusterlib_utils.fund_from_faucet(
        pool_user.payment,
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )
    return pool_user


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
