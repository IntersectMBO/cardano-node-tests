"""Tests for stake address registration."""

import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_api
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def pool_users(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.PoolUser]:
    """Create pool users."""
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        created_users = clusterlib_utils.create_pool_users(
            cluster_obj=cluster,
            name_template=f"test_delegation_pool_user_ci{cluster_manager.cluster_instance_num}",
            no_of_addr=2,
        )
        fixture_cache.value = created_users

    # Fund source addresses
    clusterlib_utils.fund_from_faucet(
        created_users[0],
        cluster_obj=cluster,
        all_faucets=cluster_manager.cache.addrs_data,
    )

    return created_users


@pytest.fixture
def pool_users_disposable(
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.PoolUser]:
    """Create function scoped pool users."""
    test_id = common.get_test_id(cluster)
    pool_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster,
        name_template=f"{test_id}_pool_user",
        no_of_addr=2,
    )
    return pool_users


class TestRegisterAddr:
    """Tests for stake address registration and deregistration."""

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_deregister_registered(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        pool_users_disposable: list[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Deregister a registered stake address.

        * create stake address registration cert
        * register and stake address
        * create stake address deregistration cert
        * deregister stake address
        * check that the balance for source address was correctly updated
        * (optional) check records in db-sync
        """
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment
        src_init_balance = cluster.g_query.get_address_balance(user_payment.address)

        # Create stake address registration cert
        address_deposit = common.get_conway_address_deposit(cluster_obj=cluster)

        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=address_deposit,
            stake_vkey_file=user_registered.stake.vkey_file,
        )

        # Register stake address
        tx_files_reg = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        if use_build_cmd:
            tx_raw_output_reg = cluster.g_transaction.build_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_reg",
                tx_files=tx_files_reg,
                fee_buffer=2_000_000,
                witness_override=len(tx_files_reg.signing_key_files),
            )
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output_reg.out_file,
                signing_key_files=tx_files_reg.signing_key_files,
                tx_name=f"{temp_template}_reg",
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output_reg.txins)
        else:
            tx_raw_output_reg = cluster.g_transaction.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_reg",
                tx_files=tx_files_reg,
            )

        assert cluster.g_query.get_stake_addr_info(
            user_registered.stake.address
        ).address, f"Stake address is NOT registered: {user_registered.stake.address}"

        # Create stake address deregistration cert
        stake_addr_dereg_cert_file = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=address_deposit,
            stake_vkey_file=user_registered.stake.vkey_file,
        )

        # Deregister stake address
        tx_files_dereg = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert_file],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        if use_build_cmd:

            def _build_dereg() -> clusterlib.TxRawOutput:
                return cluster.g_transaction.build_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_dereg",
                    tx_files=tx_files_dereg,
                    fee_buffer=2_000_000,
                    witness_override=len(tx_files_dereg.signing_key_files),
                )

            tx_raw_output_dereg: clusterlib.TxRawOutput = common.match_blocker(func=_build_dereg)
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output_dereg.out_file,
                signing_key_files=tx_files_dereg.signing_key_files,
                tx_name=f"{temp_template}_dereg",
            )
            try:
                cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output_dereg.txins)
            except clusterlib.CLIError as exc:
                if "ValueNotConservedUTxO" in str(exc):
                    issues.cli_942.finish_test()
                raise
        else:
            tx_raw_output_dereg = cluster.g_transaction.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_dereg",
                tx_files=tx_files_dereg,
            )

        assert not cluster.g_query.get_stake_addr_info(
            user_registered.stake.address
        ).address, f"Stake address is registered: {user_registered.stake.address}"

        # Check that the balance for source address was correctly updated
        assert (
            cluster.g_query.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output_reg.fee - tx_raw_output_dereg.fee
        ), f"Incorrect balance for source address `{user_payment.address}`"

        # Check records in db-sync
        tx_db_record_reg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_raw_output_reg
        )
        if tx_db_record_reg:
            assert user_registered.stake.address in tx_db_record_reg.stake_registration

        tx_db_record_dereg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_raw_output_dereg
        )
        if tx_db_record_dereg:
            assert user_registered.stake.address in tx_db_record_dereg.stake_deregistration

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_addr_registration_deregistration(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        pool_users_disposable: list[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Submit registration and deregistration certificates in single TX.

        * create stake address registration cert
        * create stake address deregistration cert
        * register and deregister stake address in single TX
        * check that the balance for source address was correctly updated and that key deposit
          was not needed
        * (optional) check records in db-sync
        """
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment
        src_init_balance = cluster.g_query.get_address_balance(user_payment.address)

        # Create stake address registration cert
        address_deposit = common.get_conway_address_deposit(cluster_obj=cluster)

        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=address_deposit,
            stake_vkey_file=user_registered.stake.vkey_file,
        )

        # Create stake address deregistration cert
        stake_addr_dereg_cert_file = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=address_deposit,
            stake_vkey_file=user_registered.stake.vkey_file,
        )

        # Register and deregister stake address in single TX
        tx_files = clusterlib.TxFiles(
            certificate_files=[
                stake_addr_reg_cert_file,
                stake_addr_dereg_cert_file,
            ]
            * 3,
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        if use_build_cmd:
            tx_raw_output = cluster.g_transaction.build_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_reg_dereg",
                tx_files=tx_files,
                fee_buffer=2_000_000,
                deposit=0,
                witness_override=len(tx_files.signing_key_files),
            )
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_reg_dereg",
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
        else:
            tx_raw_output = cluster.g_transaction.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_reg_dereg",
                tx_files=tx_files,
                deposit=0,
            )

        # Check that the stake address is not registered
        assert not cluster.g_query.get_stake_addr_info(
            user_registered.stake.address
        ).address, f"Stake address is registered: {user_registered.stake.address}"

        # Check that the balance for source address was correctly updated and that key deposit
        # was not needed
        assert (
            cluster.g_query.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output.fee
        ), f"Incorrect balance for source address `{user_payment.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            assert user_registered.stake.address in tx_db_record.stake_registration
            assert user_registered.stake.address in tx_db_record.stake_deregistration

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_addr_registration_certificate_order(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        pool_users_disposable: list[clusterlib.PoolUser],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Submit (de)registration certificates in single TX and check that the order matter.

        * create stake address registration cert
        * create stake address deregistration cert
        * register, deregister, register, deregister and register stake address in single TX
        * check that the address is registered
        * check that the balance for source address was correctly updated and that key deposit
          was needed
        * (optional) check records in db-sync
        """
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment
        src_init_balance = cluster.g_query.get_address_balance(user_payment.address)

        # Create stake address registration cert
        address_deposit = common.get_conway_address_deposit(cluster_obj=cluster)

        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=address_deposit,
            stake_vkey_file=user_registered.stake.vkey_file,
        )

        # Create stake address deregistration cert
        stake_addr_dereg_cert_file = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=address_deposit,
            stake_vkey_file=user_registered.stake.vkey_file,
        )

        # Register, deregister, register, deregister and register stake address in single TX
        # prove that the order matters
        tx_files = clusterlib.TxFiles(
            certificate_files=[
                stake_addr_reg_cert_file,
                stake_addr_dereg_cert_file,
                stake_addr_reg_cert_file,
                stake_addr_dereg_cert_file,
                stake_addr_reg_cert_file,
            ],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        deposit = cluster.g_query.get_address_deposit()

        try:
            tx_raw_output = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=temp_template,
                src_address=user_payment.address,
                submit_method=submit_method,
                use_build_cmd=use_build_cmd,
                tx_files=tx_files,
                deposit=deposit,
            )
        except (clusterlib.CLIError, submit_api.SubmitApiError) as exc:
            if "(ValueNotConservedUTxO" in str(exc) and VERSIONS.transaction_era >= VERSIONS.CONWAY:
                issues.api_484.finish_test()
            raise

        # Check that the stake address is registered
        stake_addr_info = cluster.g_query.get_stake_addr_info(user_registered.stake.address)
        if not stake_addr_info and VERSIONS.transaction_era >= VERSIONS.CONWAY:
            issues.api_484.finish_test()
        assert stake_addr_info, f"Stake address is not registered: {user_registered.stake.address}"

        # Check that the balance for source address was correctly updated and that key deposit
        # was needed
        assert (
            cluster.g_query.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output.fee - deposit
        ), f"Incorrect balance for source address `{user_payment.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            assert user_registered.stake.address in tx_db_record.stake_registration
            assert user_registered.stake.address in tx_db_record.stake_deregistration


class TestNegative:
    """Tests that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_registration_cert_with_wrong_key(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to generate stake address registration certificate using wrong stake vkey.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        # Create stake address registration cert, use wrong stake vkey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_stake_address.gen_stake_addr_registration_cert(
                addr_name=f"{temp_template}_addr0",
                deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster),
                stake_vkey_file=pool_users[0].payment.vkey_file,
            )
        err_msg = str(excinfo.value)
        assert "Expected: StakeVerificationKeyShelley" in err_msg, err_msg

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_register_addr_with_wrong_key(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        pool_users_disposable: list[clusterlib.PoolUser],
    ):
        """Try to register stake address using wrong payment skey.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # Create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster),
            stake_vkey_file=user_registered.stake.vkey_file,
        )

        # Register stake address, use wrong payment skey
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[pool_users[1].payment.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=user_payment.address, tx_name=temp_template, tx_files=tx_files
            )
        err_msg = str(excinfo.value)
        assert "MissingVKeyWitnessesUTXOW" in err_msg, err_msg

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_deregister_not_registered_addr(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        pool_users_disposable: list[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Deregister not registered stake address."""
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # Files for deregistering stake address
        stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster),
            stake_vkey_file=user_registered.stake.vkey_file,
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:

                def _build_dereg() -> clusterlib.TxRawOutput:
                    return cluster.g_transaction.build_tx(
                        src_address=user_payment.address,
                        tx_name=f"{temp_template}_dereg_fail",
                        tx_files=tx_files,
                        fee_buffer=2_000_000,
                        witness_override=len(tx_files.signing_key_files),
                    )

                tx_raw_output: clusterlib.TxRawOutput = common.match_blocker(func=_build_dereg)
                tx_signed = cluster.g_transaction.sign_tx(
                    tx_body_file=tx_raw_output.out_file,
                    signing_key_files=tx_files.signing_key_files,
                    tx_name=f"{temp_template}_dereg_fail",
                )
                cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
            else:
                cluster.g_transaction.send_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_dereg_fail",
                    tx_files=tx_files,
                )
        err_msg = str(excinfo.value)
        assert "StakeKeyNotRegisteredDELEG" in err_msg, err_msg
