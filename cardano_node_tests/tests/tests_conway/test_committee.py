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


class TestCommittee:
    """Tests for Constitutional Committee."""

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
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
