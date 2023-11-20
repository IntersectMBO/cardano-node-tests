"""Tests for Conway governance DRep functionality."""
import logging
import pathlib as pl
import typing as tp

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


@pytest.fixture
def pool_users_disposable(
    cluster: clusterlib.ClusterLib,
) -> tp.List[clusterlib.PoolUser]:
    """Create function scoped pool users."""
    test_id = common.get_test_id(cluster)
    pool_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster,
        name_template=f"{test_id}_pool_user",
        no_of_addr=2,
    )
    return pool_users


class TestDReps:
    """Tests for DReps."""

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


class TestDelegDReps:
    """Tests for votes delegation to DReps."""

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    def test_always_abstain(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        pool_users_disposable: tp.List[clusterlib.PoolUser],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test delegating to always-abstain default DRep.

        * register stake address
        * delegate stake to always-abstain default DRep
        * check that stake address is registered
        """
        temp_template = common.get_test_id(cluster)
        pool_user = pool_users_disposable[0]
        deposit_amt = cluster.g_query.get_address_deposit()

        # Create stake address registration cert
        reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=deposit_amt,
            stake_vkey_file=pool_user.stake.vkey_file,
        )

        # Create vote delegation cert
        deleg_cert = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=temp_template,
            stake_vkey_file=pool_user.stake.vkey_file,
            always_abstain=True,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[reg_cert, deleg_cert],
            signing_key_files=[payment_addr.skey_file, pool_user.stake.skey_file],
        )

        if use_build_cmd:
            tx_output = cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=temp_template,
                tx_files=tx_files,
                witness_override=len(tx_files.signing_key_files),
            )
        else:
            fee = cluster.g_transaction.calculate_tx_fee(
                src_address=payment_addr.address,
                tx_name=temp_template,
                tx_files=tx_files,
                witness_count_add=len(tx_files.signing_key_files),
            )
            tx_output = cluster.g_transaction.build_raw_tx(
                src_address=payment_addr.address,
                tx_name=temp_template,
                tx_files=tx_files,
                fee=fee,
                deposit=deposit_amt,
            )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=tx_signed,
            txins=tx_output.txins,
        )

        assert cluster.g_query.get_stake_addr_info(
            pool_user.stake.address
        ).address, f"Stake address is NOT registered: {pool_user.stake.address}"

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        # Check that stake address is delegated to always-abstain default DRep
        # TODO
