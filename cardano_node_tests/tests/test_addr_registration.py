"""Tests for stake address registration."""

import logging
import pathlib as pl

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
    created_users = common.get_pool_users(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=2,
        fund_idx=[0],
        caching_key=helpers.get_current_line_str(),
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
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_deregister_registered(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        pool_users_disposable: list[clusterlib.PoolUser],
        build_method: str,
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

        tx_output_reg = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_reg",
            src_address=user_payment.address,
            tx_files=tx_files_reg,
            build_method=build_method,
            witness_override=len(tx_files_reg.signing_key_files),
        )

        assert cluster.g_query.get_stake_addr_info(user_registered.stake.address).address, (
            f"Stake address is NOT registered: {user_registered.stake.address}"
        )

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

        try:
            tx_output_dereg = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_dereg",
                src_address=user_payment.address,
                tx_files=tx_files_dereg,
                build_method=build_method,
                witness_override=len(tx_files_dereg.signing_key_files),
            )
        except clusterlib.CLIError as exc:
            if "ValueNotConservedUTxO" in str(exc):
                issues.cli_942.finish_test()
            raise

        assert not cluster.g_query.get_stake_addr_info(user_registered.stake.address).address, (
            f"Stake address is registered: {user_registered.stake.address}"
        )

        # Check that the balance for source address was correctly updated
        assert (
            cluster.g_query.get_address_balance(user_payment.address)
            == src_init_balance - tx_output_reg.fee - tx_output_dereg.fee
        ), f"Incorrect balance for source address `{user_payment.address}`"

        # Check records in db-sync
        tx_db_record_reg = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_reg)
        if tx_db_record_reg:
            assert user_registered.stake.address in tx_db_record_reg.stake_registration

        tx_db_record_dereg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_output_dereg
        )
        if tx_db_record_dereg:
            assert user_registered.stake.address in tx_db_record_dereg.stake_deregistration

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_addr_registration_deregistration(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        pool_users_disposable: list[clusterlib.PoolUser],
        build_method: str,
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

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_reg_dereg",
            src_address=user_payment.address,
            tx_files=tx_files,
            build_method=build_method,
            deposit=0,
            witness_override=len(tx_files.signing_key_files),
        )

        # Check that the stake address is not registered
        assert not cluster.g_query.get_stake_addr_info(user_registered.stake.address).address, (
            f"Stake address is registered: {user_registered.stake.address}"
        )

        # Check that the balance for source address was correctly updated and that key deposit
        # was not needed
        assert (
            cluster.g_query.get_address_balance(user_payment.address)
            == src_init_balance - tx_output.fee
        ), f"Incorrect balance for source address `{user_payment.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            assert user_registered.stake.address in tx_db_record.stake_registration
            assert user_registered.stake.address in tx_db_record.stake_deregistration

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_addr_registration_certificate_order(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        pool_users_disposable: list[clusterlib.PoolUser],
        submit_method: str,
        build_method: str,
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
                build_method=build_method,
                tx_files=tx_files,
                deposit=deposit,
            )
        except (clusterlib.CLIError, submit_api.SubmitApiError) as exc:
            if "(ValueNotConservedUTxO" in str(exc) and VERSIONS.transaction_era >= VERSIONS.CONWAY:
                issues.api_484.finish_test()
            if (
                build_method == clusterlib_utils.BuildMethods.BUILD_EST
                and "does not balance in its use of assets" in str(exc)
            ):
                issues.cli_1199.finish_test()
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

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.parametrize("key_type", ("stake", "payment"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_multisig_deregister_registered(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        build_method: str,
        key_type: str,
    ):
        """Deregister a registered multisig stake address.

        * Create a multisig script to be used as stake credentials
        * Create stake address registration certificate
        * Create a Tx for the registration certificate
        * Incrementally sign the Tx and submit the registration certificate
        * Check that the address is registered
        * Create stake address deregistration certificate
        * Create a Tx for the deregistration certificate
        * Incrementally sign the Tx and submit the deregistration certificate
        * Check that the address is no longer registered
        * (optional) check records in db-sync
        """
        temp_template = common.get_test_id(cluster)
        payment_addr = pool_users[0].payment

        # Create a multisig script to be used as stake credentials
        if key_type == "stake":
            stake_key_recs = [
                cluster.g_stake_address.gen_stake_key_pair(key_name=f"{temp_template}_sig_{i}")
                for i in range(1, 6)
            ]
            multisig_script = clusterlib_utils.build_stake_multisig_script(
                cluster_obj=cluster,
                script_name=temp_template,
                script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
                stake_vkey_files=[r.vkey_file for r in stake_key_recs],
            )
        else:
            stake_key_recs = [
                cluster.g_address.gen_payment_key_pair(key_name=f"{temp_template}_sig_{i}")
                for i in range(1, 6)
            ]
            multisig_script = cluster.g_transaction.build_multisig_script(
                script_name=temp_template,
                script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
                payment_vkey_files=[r.vkey_file for r in stake_key_recs],
            )

        stake_address = cluster.g_stake_address.gen_stake_addr(
            addr_name=temp_template, stake_script_file=multisig_script
        )

        # Create stake address registration cert
        address_deposit = common.get_conway_address_deposit(cluster_obj=cluster)

        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=address_deposit,
            stake_script_file=multisig_script,
        )
        reg_cert_script = clusterlib.ComplexCert(
            certificate_file=stake_addr_reg_cert_file,
            script_file=multisig_script,
        )

        signing_key_files = [payment_addr.skey_file, *[r.skey_file for r in stake_key_recs]]
        witness_len = len(signing_key_files)

        def _submit_tx(
            name_template: str, complex_certs: list[clusterlib.ComplexCert]
        ) -> clusterlib.TxRawOutput:
            if build_method == clusterlib_utils.BuildMethods.BUILD:
                tx_output = cluster.g_transaction.build_tx(
                    src_address=payment_addr.address,
                    tx_name=name_template,
                    complex_certs=complex_certs,
                    fee_buffer=2_000_000,
                    witness_override=witness_len,
                )

            elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
                fee = cluster.g_transaction.calculate_tx_fee(
                    src_address=payment_addr.address,
                    tx_name=name_template,
                    complex_certs=complex_certs,
                    witness_count_add=witness_len,
                )
                tx_output = cluster.g_transaction.build_raw_tx(
                    src_address=payment_addr.address,
                    tx_name=name_template,
                    complex_certs=complex_certs,
                    fee=fee,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_EST:
                tx_output = cluster.g_transaction.build_estimate_tx(
                    src_address=payment_addr.address,
                    tx_name=name_template,
                    complex_certs=complex_certs,
                    witness_count_add=witness_len,
                )
            else:
                err = f"Invalid build_method: {build_method}"
                raise ValueError(err)

            # Create witness file for each key
            witness_files = [
                cluster.g_transaction.witness_tx(
                    tx_body_file=tx_output.out_file,
                    witness_name=f"{name_template}_skey{idx}",
                    signing_key_files=[skey],
                )
                for idx, skey in enumerate(signing_key_files, start=1)
            ]

            # Sign TX using witness files
            tx_witnessed_file = cluster.g_transaction.assemble_tx(
                tx_body_file=tx_output.out_file,
                witness_files=witness_files,
                tx_name=name_template,
            )

            # Submit signed TX
            cluster.g_transaction.submit_tx(tx_file=tx_witnessed_file, txins=tx_output.txins)

            return tx_output

        # Build a Tx with the registration certificate
        src_init_balance = cluster.g_query.get_address_balance(payment_addr.address)

        tx_output_reg = _submit_tx(
            name_template=f"{temp_template}_reg", complex_certs=[reg_cert_script]
        )

        # Check that the stake address is registered
        stake_addr_info = cluster.g_query.get_stake_addr_info(stake_address)
        assert stake_addr_info, f"Stake address is not registered: {stake_address}"

        # Create stake address deregistration cert
        stake_addr_dereg_cert_file = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=address_deposit,
            stake_script_file=multisig_script,
        )
        dereg_cert_script = clusterlib.ComplexCert(
            certificate_file=stake_addr_dereg_cert_file,
            script_file=multisig_script,
        )

        # Build a Tx with the deregistration certificate
        tx_output_dereg = _submit_tx(
            name_template=f"{temp_template}_dereg", complex_certs=[dereg_cert_script]
        )

        # Check that the balance for source address was correctly updated and that key deposit
        # was needed
        assert (
            cluster.g_query.get_address_balance(payment_addr.address)
            == src_init_balance - tx_output_reg.fee - tx_output_dereg.fee
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        # Check records in db-sync
        tx_db_record_reg = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_reg)
        if tx_db_record_reg:
            assert stake_address in tx_db_record_reg.stake_registration

        tx_db_record_dereg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_output_dereg
        )
        if tx_db_record_dereg:
            assert stake_address in tx_db_record_dereg.stake_deregistration


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
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_deregister_not_registered_addr(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        pool_users_disposable: list[clusterlib.PoolUser],
        build_method: str,
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
            if build_method == clusterlib_utils.BuildMethods.BUILD_RAW:

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
            elif build_method == clusterlib_utils.BuildMethods.BUILD:
                cluster.g_transaction.send_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_dereg_fail",
                    tx_files=tx_files,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_EST:
                cluster.g_transaction.build_estimate_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_dereg_fail",
                    tx_files=tx_files,
                    witness_count_add=len(tx_files.signing_key_files),
                )

        err_msg = str(excinfo.value)
        assert "StakeKeyNotRegisteredDELEG" in err_msg, err_msg

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.parametrize("issue", ("missing_script", "missing_skey"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_incomplete_multisig(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        build_method: str,
        issue: str,
    ):
        """Try to register a multisig stake address while missing either a script or an skey.

        Expect failure.

        * Create a multisig script to be used as stake credentials
        * Create stake address registration certificate
        * Create a Tx for the registration certificate
        * Scenario1: Build a Tx with the registration certificate, without passing the the script
          to the transaction build command
        * Scenario 2: One skey is missing when witnesing the Tx
        * Incrementally sign the Tx and submit the registration certificate
        * Check the expected failure
        """
        temp_template = common.get_test_id(cluster)
        payment_addr = pool_users[0].payment

        # Create a multisig script to be used as stake credentials
        stake_key_recs = [
            cluster.g_stake_address.gen_stake_key_pair(key_name=f"{temp_template}_sig_{i}")
            for i in range(1, 6)
        ]
        multisig_script = clusterlib_utils.build_stake_multisig_script(
            cluster_obj=cluster,
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            stake_vkey_files=[r.vkey_file for r in stake_key_recs],
        )

        # Create stake address registration cert
        address_deposit = common.get_conway_address_deposit(cluster_obj=cluster)

        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=address_deposit,
            stake_script_file=multisig_script,
        )

        signing_key_files = [payment_addr.skey_file, *[r.skey_file for r in stake_key_recs]]

        def _submit_tx(
            name_template: str,
            tx_files: clusterlib.TxFiles | None = None,
            complex_certs: list[clusterlib.ComplexCert] | tuple[()] = (),
            signing_key_files: list[pl.Path] | tuple[()] = (),
        ) -> clusterlib.TxRawOutput:
            witness_len = len(signing_key_files)

            if build_method == clusterlib_utils.BuildMethods.BUILD:
                tx_output = cluster.g_transaction.build_tx(
                    src_address=payment_addr.address,
                    tx_name=name_template,
                    tx_files=tx_files,
                    complex_certs=complex_certs,
                    fee_buffer=2_000_000,
                    witness_override=witness_len,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
                fee = cluster.g_transaction.calculate_tx_fee(
                    src_address=payment_addr.address,
                    tx_name=name_template,
                    tx_files=tx_files,
                    complex_certs=complex_certs,
                    witness_count_add=witness_len,
                )
                tx_output = cluster.g_transaction.build_raw_tx(
                    src_address=payment_addr.address,
                    tx_name=name_template,
                    tx_files=tx_files,
                    complex_certs=complex_certs,
                    fee=fee,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_EST:
                tx_output = cluster.g_transaction.build_estimate_tx(
                    src_address=payment_addr.address,
                    tx_name=name_template,
                    tx_files=tx_files,
                    complex_certs=complex_certs,
                    witness_count_add=witness_len,
                )
            else:
                err = f"Invalid build_method: {build_method}"
                raise ValueError(err)

            # Create witness file for each key
            witness_files = [
                cluster.g_transaction.witness_tx(
                    tx_body_file=tx_output.out_file,
                    witness_name=f"{name_template}_skey{idx}",
                    signing_key_files=[skey],
                )
                for idx, skey in enumerate(signing_key_files, start=1)
            ]

            # Sign TX using witness files
            tx_witnessed_file = cluster.g_transaction.assemble_tx(
                tx_body_file=tx_output.out_file,
                witness_files=witness_files,
                tx_name=name_template,
            )

            # Submit signed TX
            cluster.g_transaction.submit_tx(tx_file=tx_witnessed_file, txins=tx_output.txins)

            return tx_output

        # Scenario1: Build a Tx with the registration certificate, without passing the the script
        # to the transaction build command.
        if issue == "missing_script":
            tx_files_script = clusterlib.TxFiles(certificate_files=[stake_addr_reg_cert_file])
            with pytest.raises(clusterlib.CLIError) as excinfo:
                _submit_tx(
                    name_template=temp_template,
                    tx_files=tx_files_script,
                    signing_key_files=signing_key_files,
                )
            err_msg = str(excinfo.value)
            assert "MissingScriptWitnessesUTXOW" in err_msg, err_msg

        # Scenario2: One skey is missing when witnesing the Tx
        elif issue == "missing_skey":
            reg_cert_script = clusterlib.ComplexCert(
                certificate_file=stake_addr_reg_cert_file,
                script_file=multisig_script,
            )
            with pytest.raises(clusterlib.CLIError) as excinfo:
                _submit_tx(
                    name_template=temp_template,
                    complex_certs=[reg_cert_script],
                    signing_key_files=signing_key_files[:-1],
                )
            err_msg = str(excinfo.value)
            assert "ScriptWitnessNotValidatingUTXOW" in err_msg, err_msg

        else:
            err = f"Invalid issue: {issue}"
            raise ValueError(err)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("era", ("shelley", "allegra", "mary", "alonzo", "babbage"))
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_legacy_stake_addr_registration_rejected_in_conway(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        era: str,
    ):
        """Reject legacy stake address registration in Conway.

        * Generate a stake address registration certificate using the compatible CLI
        for a legacy era.
        * Attempt to submit the legacy certificate in a Conway-era transaction.
        * Expect the transaction submission to fail with a TextEnvelope type error.
        """
        temp_template = common.get_test_id(cluster)

        pool_users = common.get_pool_users(
            name_template=f"{temp_template}_{era}_legacy",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=1,
            fund_idx=[0],
            amount=600_000_000,
        )

        era_api = getattr(cluster.g_compatible, era)

        legacy_stake_reg_cert = era_api.stake_address.gen_registration_cert(
            name=f"{temp_template}_{era}_stake",
            stake_vkey_file=pool_users[0].stake.vkey_file,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[legacy_stake_reg_cert],
            signing_key_files=[
                pool_users[0].payment.skey_file,
                pool_users[0].stake.skey_file,
            ],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=pool_users[0].payment.address,
                tx_name=f"{temp_template}_{era}_legacy_stake_reg",
                tx_files=tx_files,
            )

        err = str(excinfo.value)

        assert "TextEnvelope type error" in err, err
