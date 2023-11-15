"""Tests for Conway governance DRep functionality."""
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


class TestDReps:
    """Tests for DReps."""

    @pytest.fixture
    def payment_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.AddressRecord:
        """Create new payment address."""
        addr = clusterlib_utils.create_payment_addr_records(
            f"chain_tx_addr_ci{cluster_manager.cluster_instance_num}",
            cluster_obj=cluster,
        )[0]

        # Fund source address
        clusterlib_utils.fund_from_faucet(
            addr,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return addr

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    def test_register_and_retire_drep(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test DRep registration and retirement.

        * register DRep
        * check that DRep was registered
        * retire DRep
        * check that DRep was retired
        * check that deposit was returned to source address
        """
        temp_template = common.get_test_id(cluster)

        # Register DRep

        reg_drep = clusterlib_utils.register_drep(
            cluster_obj=cluster,
            name_template=temp_template,
            payment_addr=payment_addr,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
        )

        reg_out_utxos = cluster.g_query.get_utxo(tx_raw_output=reg_drep.tx_output)
        assert (
            clusterlib.filter_utxos(utxos=reg_out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(reg_drep.tx_output.txins)
            - reg_drep.tx_output.fee
            - reg_drep.deposit
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        reg_drep_state = cluster.g_conway_governance.query.drep_state(
            drep_vkey_file=reg_drep.key_pair.vkey_file
        )
        assert reg_drep_state[0][0]["keyHash"] == reg_drep.drep_id, "DRep was not registered"

        # Retire DRep

        ret_cert = cluster.g_conway_governance.drep.gen_retirement_cert(
            cert_name=temp_template,
            deposit_amt=reg_drep.deposit,
            drep_vkey_file=reg_drep.key_pair.vkey_file,
        )

        tx_files_ret = clusterlib.TxFiles(
            certificate_files=[ret_cert],
            signing_key_files=[payment_addr.skey_file, reg_drep.key_pair.skey_file],
        )

        if use_build_cmd:
            tx_output_ret = cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=temp_template,
                tx_files=tx_files_ret,
                witness_override=len(tx_files_ret.signing_key_files),
            )
        else:
            fee_ret = cluster.g_transaction.calculate_tx_fee(
                src_address=payment_addr.address,
                tx_name=temp_template,
                tx_files=tx_files_ret,
                witness_count_add=len(tx_files_ret.signing_key_files),
            )
            tx_output_ret = cluster.g_transaction.build_raw_tx(
                src_address=payment_addr.address,
                tx_name=temp_template,
                tx_files=tx_files_ret,
                fee=fee_ret,
                deposit=-reg_drep.deposit,
            )

        tx_signed_ret = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_ret.out_file,
            signing_key_files=tx_files_ret.signing_key_files,
            tx_name=temp_template,
        )
        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=tx_signed_ret,
            txins=tx_output_ret.txins,
        )

        ret_drep_state = cluster.g_conway_governance.query.drep_state(
            drep_vkey_file=reg_drep.key_pair.vkey_file
        )
        assert not ret_drep_state, "DRep was not retired"

        ret_out_utxos = cluster.g_query.get_utxo(tx_raw_output=reg_drep.tx_output)
        assert (
            clusterlib.filter_utxos(utxos=ret_out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_ret.txins)
            - tx_output_ret.fee
            + reg_drep.deposit
        ), f"Incorrect balance for source address `{payment_addr.address}`"
