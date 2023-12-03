"""Tests for Conway governance Constitutional Committee functionality."""
import logging
import pathlib as pl

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent / "data"

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

        * register CC Member
        * check that CC Member was registered
        * resign from CC Member position
        * check that CC Member resigned
        """
        temp_template = common.get_test_id(cluster)

        # Register CC Member

        reg_cc = clusterlib_utils.get_cc_member_reg_record(
            cluster_obj=cluster,
            name_template=temp_template,
        )

        tx_files_reg = clusterlib.TxFiles(
            certificate_files=[reg_cc.registration_cert],
            signing_key_files=[payment_addr.skey_file, reg_cc.cold_key_pair.skey_file],
        )

        tx_output_reg = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_reg",
            src_address=payment_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files_reg,
        )

        reg_out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_reg)
        assert (
            clusterlib.filter_utxos(utxos=reg_out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_reg.txins) - tx_output_reg.fee
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        reg_committee_state = cluster.g_conway_governance.query.committee_state()
        member_key = f"keyHash-{reg_cc.key_hash}"
        assert (
            reg_committee_state["committee"][member_key]["hotCredsAuthStatus"]["tag"]
            == "MemberAuthorized"
        ), "CC Member was not registered"

        # Resignation of CC Member

        res_cert = cluster.g_conway_governance.committee.gen_cold_key_resignation_cert(
            key_name=temp_template,
            cold_vkey_file=reg_cc.cold_key_pair.vkey_file,
            resignation_metadata_url="http://www.cc-resign.com",
            resignation_metadata_hash="5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d",
        )

        tx_files_res = clusterlib.TxFiles(
            certificate_files=[res_cert],
            signing_key_files=[payment_addr.skey_file, reg_cc.cold_key_pair.skey_file],
        )

        tx_output_res = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_res",
            src_address=payment_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files_res,
        )

        res_committee_state = cluster.g_conway_governance.query.committee_state()
        if member_key in res_committee_state["committee"]:
            assert (
                res_committee_state["committee"][member_key]["hotCredsAuthStatus"]["tag"]
                == "MemberResigned"
            ), "CC Member not resigned"

        res_out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_res)
        assert (
            clusterlib.filter_utxos(utxos=res_out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_res.txins) - tx_output_res.fee
        ), f"Incorrect balance for source address `{payment_addr.address}`"

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

        cc_reg_records = [
            clusterlib_utils.get_cc_member_reg_record(
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
            for r in cc_reg_records
        ]

        deposit_amt = cluster.conway_genesis["govActionDeposit"]
        anchor_url = "http://www.cc-update.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"
        update_action = cluster.g_conway_governance.action.update_committee(
            action_name=temp_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            quorum="2/3",
            add_cc_members=cc_members,
            deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[r.registration_cert for r in cc_reg_records],
            proposal_files=[update_action],
            signing_key_files=[
                pool_user.payment.skey_file,
                *[r.cold_key_pair.skey_file for r in cc_reg_records],
            ],
        )

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=pool_user.payment.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=pool_user.payment.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_output.out_file)
        proposals = cluster.g_conway_governance.query.gov_state()["proposals"]
        prop = {}
        for p in proposals:
            if p["actionId"]["txId"] == txid:
                prop = p
                break
        assert prop, "Update committee action not found"
        assert prop["action"]["tag"] == "UpdateCommittee", "Incorrect action tag"
