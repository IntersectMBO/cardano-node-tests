"""Tests for stake address registration and delegation."""
import logging
from typing import List
from typing import Tuple

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def cluster_and_pool(
    cluster_manager: cluster_management.ClusterManager,
) -> Tuple[clusterlib.ClusterLib, str]:
    return delegation.cluster_and_pool(cluster_manager=cluster_manager)


@pytest.fixture
def pool_users(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.PoolUser]:
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

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        created_users[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )

    return created_users


@pytest.fixture
def pool_users_disposable(
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.PoolUser]:
    """Create function scoped pool users."""
    test_id = common.get_test_id(cluster)
    pool_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster,
        name_template=f"{test_id}_pool_user",
        no_of_addr=2,
    )
    return pool_users


@pytest.fixture
def pool_users_cluster_and_pool(
    cluster_manager: cluster_management.ClusterManager,
    cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
) -> List[clusterlib.PoolUser]:
    """Create pool users using `cluster_and_pool` fixture.

    .. warning::
       The cached addresses can be used only for payments, not for delegation!
       The pool can be different every time the fixture is called.
    """
    cluster, *__ = cluster_and_pool
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        created_users = clusterlib_utils.create_pool_users(
            cluster_obj=cluster,
            name_template=f"test_delegation_pool_user_cap_ci{cluster_manager.cluster_instance_num}",
            no_of_addr=2,
        )
        fixture_cache.value = created_users

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        created_users[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )

    return created_users


@pytest.fixture
def pool_users_disposable_cluster_and_pool(
    cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
) -> List[clusterlib.PoolUser]:
    """Create function scoped pool users using `cluster_and_pool` fixture."""
    cluster, *__ = cluster_and_pool
    test_id = common.get_test_id(cluster)
    pool_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster,
        name_template=f"{test_id}_pool_user_cap",
        no_of_addr=2,
    )
    return pool_users


@pytest.fixture(scope="class")
def stake_address_option_unusable() -> bool:
    return not (
        clusterlib_utils.cli_has("stake-address registration-certificate --stake-address")
        or clusterlib_utils.cli_has("stake-address deregistration-certificate --stake-address")
        or clusterlib_utils.cli_has("stake-address delegation-certificate --stake-address")
    )


@pytest.mark.testnets
@pytest.mark.order(8)
class TestDelegateAddr:
    """Tests for address delegation to stake pools."""

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    @pytest.mark.smoke
    def test_delegate_using_pool_id(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
        use_build_cmd: bool,
    ):
        """Submit registration certificate and delegate to pool using pool id.

        * register stake address and delegate it to pool
        * check that the stake address was delegated
        * (optional) check records in db-sync
        """
        cluster, pool_id = cluster_and_pool
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
            use_build_cmd=use_build_cmd,
        )

        tx_db_record = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out.tx_raw_output
        )
        delegation.db_check_delegation(
            pool_user=delegation_out.pool_user,
            db_record=tx_db_record,
            deleg_epoch=init_epoch,
            pool_id=delegation_out.pool_id,
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    @pytest.mark.smoke
    @pytest.mark.skipif(
        cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.TESTNET_NOPOOLS,
        reason="supposed to run on cluster with pools",
    )
    def test_delegate_using_vkey(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_use_pool: Tuple[clusterlib.ClusterLib, str],
        use_build_cmd: bool,
    ):
        """Submit registration certificate and delegate to pool using cold vkey.

        * register stake address and delegate it to pool
        * check that the stake address was delegated
        * (optional) check records in db-sync
        """
        cluster, pool_name = cluster_use_pool
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # submit registration certificate and delegate to pool
        node_cold = cluster_manager.cache.addrs_data[pool_name]["cold_key_pair"]
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            cold_vkey=node_cold.vkey_file,
            use_build_cmd=use_build_cmd,
        )

        tx_db_record = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out.tx_raw_output
        )
        delegation.db_check_delegation(
            pool_user=delegation_out.pool_user,
            db_record=tx_db_record,
            deleg_epoch=init_epoch,
            pool_id=delegation_out.pool_id,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(7)
    @pytest.mark.dbsync
    @pytest.mark.long
    def test_deregister(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
    ):
        """Deregister stake address.

        * create two payment addresses that share single stake address
        * register and delegate the stake address to pool
        * attempt to deregister the stake address - deregistration is expected to fail
          because there are rewards in the stake address
        * withdraw rewards to payment address and deregister stake address
        * check that the key deposit was returned and rewards withdrawn
        * check that the stake address is no longer delegated
        * (optional) check records in db-sync
        """
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)

        # create two payment addresses that share single stake address (just to test that
        # delegation works as expected even under such circumstances)
        stake_addr_rec = clusterlib_utils.create_stake_addr_records(
            f"{temp_template}_addr0", cluster_obj=cluster
        )[0]
        payment_addr_recs = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_addr0",
            f"{temp_template}_addr1",
            cluster_obj=cluster,
            stake_vkey_file=stake_addr_rec.vkey_file,
        )

        # fund payment address
        clusterlib_utils.fund_from_faucet(
            *payment_addr_recs,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        pool_user = clusterlib.PoolUser(payment=payment_addr_recs[1], stake=stake_addr_rec)

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_user=pool_user,
            pool_id=pool_id,
        )

        assert (
            cluster.g_query.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        tx_db_deleg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out.tx_raw_output
        )
        if tx_db_deleg:
            # check in db-sync that both payment addresses share single stake address
            assert (
                dbsync_utils.get_utxo(address=payment_addr_recs[0].address).stake_address
                == stake_addr_rec.address
            )
            assert (
                dbsync_utils.get_utxo(address=payment_addr_recs[1].address).stake_address
                == stake_addr_rec.address
            )

        delegation.db_check_delegation(
            pool_user=delegation_out.pool_user,
            db_record=tx_db_deleg,
            deleg_epoch=init_epoch,
            pool_id=delegation_out.pool_id,
        )

        src_address = delegation_out.pool_user.payment.address

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance:
            pytest.skip(f"User of pool '{pool_id}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # files for deregistering stake address
        stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=delegation_out.pool_user.stake.vkey_file,
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[
                delegation_out.pool_user.payment.skey_file,
                delegation_out.pool_user.stake.skey_file,
            ],
        )

        # attempt to deregister the stake address - deregistration is expected to fail
        # because there are rewards in the stake address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=src_address,
                tx_name=f"{temp_template}_dereg_fail",
                tx_files=tx_files_deregister,
            )
        err_msg = str(excinfo.value)
        assert "StakeKeyNonZeroAccountBalanceDELEG" in err_msg, err_msg

        src_payment_balance = cluster.g_query.get_address_balance(src_address)
        reward_balance = cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance

        # withdraw rewards to payment address, deregister stake address
        tx_raw_deregister_output = cluster.g_transaction.send_tx(
            src_address=src_address,
            tx_name=f"{temp_template}_dereg_withdraw",
            tx_files=tx_files_deregister,
            withdrawals=[
                clusterlib.TxOut(address=delegation_out.pool_user.stake.address, amount=-1)
            ],
        )

        # check that the key deposit was returned and rewards withdrawn
        assert (
            cluster.g_query.get_address_balance(src_address)
            == src_payment_balance
            - tx_raw_deregister_output.fee
            + reward_balance
            + cluster.g_query.get_address_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        # check that the stake address is no longer delegated
        stake_addr_info = cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        )
        assert (
            not stake_addr_info.delegation
        ), f"Stake address is still delegated: {stake_addr_info}"

        tx_db_dereg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_raw_deregister_output
        )
        if tx_db_dereg:
            assert delegation_out.pool_user.stake.address in tx_db_dereg.stake_deregistration
            assert (
                cluster.g_query.get_address_balance(src_address)
                == dbsync_utils.get_utxo(address=src_address).amount_sum
            ), f"Unexpected balance for source address `{src_address}` in db-sync"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(7)
    @pytest.mark.dbsync
    @pytest.mark.long
    def test_undelegate(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
    ):
        """Undelegate stake address.

        * submit registration certificate and delegate to pool
        * wait for first reward
        * undelegate stake address:

           - withdraw rewards to payment address
           - deregister stake address
           - re-register stake address

        * check that the key deposit was not returned
        * check that rewards were withdrawn
        * check that the stake address is still registered
        * check that the stake address is no longer delegated
        * (optional) check records in db-sync
        """
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        assert (
            cluster.g_query.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        # check records in db-sync
        tx_db_deleg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out.tx_raw_output
        )
        delegation.db_check_delegation(
            pool_user=delegation_out.pool_user,
            db_record=tx_db_deleg,
            deleg_epoch=init_epoch,
            pool_id=delegation_out.pool_id,
        )

        src_address = delegation_out.pool_user.payment.address

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance:
            pytest.skip(f"User of pool '{pool_id}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # files for deregistering / re-registering stake address
        stake_addr_dereg_cert_file = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_undeleg_addr0",
            stake_vkey_file=delegation_out.pool_user.stake.vkey_file,
        )
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_undeleg_addr0",
            stake_vkey_file=delegation_out.pool_user.stake.vkey_file,
        )
        tx_files_undeleg = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert_file, stake_addr_reg_cert_file],
            signing_key_files=[
                delegation_out.pool_user.payment.skey_file,
                delegation_out.pool_user.stake.skey_file,
            ],
        )

        src_payment_balance = cluster.g_query.get_address_balance(src_address)
        reward_balance = cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance

        # withdraw rewards to payment address; deregister and re-register stake address
        tx_raw_undeleg = cluster.g_transaction.send_tx(
            src_address=src_address,
            tx_name=f"{temp_template}_undeleg_withdraw",
            tx_files=tx_files_undeleg,
            withdrawals=[
                clusterlib.TxOut(address=delegation_out.pool_user.stake.address, amount=-1)
            ],
        )

        # check that the key deposit was NOT returned and rewards were withdrawn
        assert (
            cluster.g_query.get_address_balance(src_address)
            == src_payment_balance - tx_raw_undeleg.fee + reward_balance
        ), f"Incorrect balance for source address `{src_address}`"

        # check that the stake address is no longer delegated
        stake_addr_info = cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        )
        assert stake_addr_info.address, f"Reward address is not registered: {stake_addr_info}"
        assert (
            not stake_addr_info.delegation
        ), f"Stake address is still delegated: {stake_addr_info}"

        this_epoch = cluster.wait_for_new_epoch(padding_seconds=20)
        assert cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance, "No reward was received next epoch after undelegation"

        # check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_undeleg)

        # check records in db-sync
        tx_db_undeleg = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_undeleg)
        if tx_db_undeleg:
            assert delegation_out.pool_user.stake.address in tx_db_undeleg.stake_deregistration
            assert delegation_out.pool_user.stake.address in tx_db_undeleg.stake_registration

            db_rewards = dbsync_utils.check_address_reward(
                address=delegation_out.pool_user.stake.address, epoch_from=init_epoch
            )
            assert db_rewards
            db_reward_epochs = sorted(r.spendable_epoch for r in db_rewards.rewards)
            assert db_reward_epochs[0] == init_epoch + 4
            assert this_epoch in db_reward_epochs

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    @pytest.mark.smoke
    def test_addr_registration_deregistration(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
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
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment
        src_init_balance = cluster.g_query.get_address_balance(user_payment.address)

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # create stake address deregistration cert
        stake_addr_dereg_cert_file = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register and deregister stake address in single TX
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
                tx_name=f"{temp_template}_reg_deleg",
                tx_files=tx_files,
                fee_buffer=2_000_000,
                deposit=0,
                witness_override=len(tx_files.signing_key_files),
            )
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_reg_deleg",
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
        else:
            tx_raw_output = cluster.g_transaction.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_reg_dereg",
                tx_files=tx_files,
                deposit=0,
            )

        # check that the stake address is not registered
        assert not cluster.g_query.get_stake_addr_info(
            user_registered.stake.address
        ).address, f"Stake address is registered: {user_registered.stake.address}"

        # check that the balance for source address was correctly updated and that key deposit
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
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    @pytest.mark.parametrize(
        "stake_cert",
        ("vkey_file", "stake_address"),
    )
    @pytest.mark.smoke
    def test_addr_delegation_deregistration(
        self,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
        pool_users_cluster_and_pool: List[clusterlib.PoolUser],
        pool_users_disposable_cluster_and_pool: List[clusterlib.PoolUser],
        stake_cert: str,
        use_build_cmd: bool,
        stake_address_option_unusable: bool,
    ):
        """Submit delegation and deregistration certificates in single TX.

        * create stake address registration cert
        * create stake address deregistration cert
        * register stake address
        * create stake address delegation cert
        * delegate and deregister stake address in single TX
        * check that the balance for source address was correctly updated and that the key
          deposit was returned
        * check that the stake address was NOT delegated
        * (optional) check records in db-sync
        """
        if stake_cert == "stake_address" and stake_address_option_unusable:
            pytest.skip(
                "`stake-address` option is not available on `stake-address` certificates commands"
            )

        cluster, pool_id = cluster_and_pool
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        user_registered = pool_users_disposable_cluster_and_pool[0]
        user_payment = pool_users_cluster_and_pool[0].payment
        src_init_balance = cluster.g_query.get_address_balance(user_payment.address)

        stake_vkey_file = user_registered.stake.vkey_file if stake_cert == "vkey_file" else None
        stake_address = user_registered.stake.address if stake_cert == "stake_address" else None

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=stake_vkey_file,
            stake_address=stake_address,
        )

        # create stake address deregistration cert
        stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=stake_vkey_file,
            stake_address=stake_address,
        )

        # register stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[user_payment.skey_file],
        )
        tx_raw_output_reg = cluster.g_transaction.send_tx(
            src_address=user_payment.address,
            tx_name=f"{temp_template}_reg",
            tx_files=tx_files,
        )

        # check that the stake address is registered
        assert cluster.g_query.get_stake_addr_info(
            user_registered.stake.address
        ).address, f"Stake address is not registered: {user_registered.stake.address}"

        tx_db_reg = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_reg)
        if tx_db_reg:
            assert user_registered.stake.address in tx_db_reg.stake_registration

        # check that the balance for source address was correctly updated
        assert (
            cluster.g_query.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output_reg.fee - cluster.g_query.get_address_deposit()
        ), f"Incorrect balance for source address `{user_payment.address}`"

        src_registered_balance = cluster.g_query.get_address_balance(user_payment.address)

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.g_stake_address.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=stake_vkey_file,
            stake_address=stake_address,
            stake_pool_id=pool_id,
        )

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # delegate and deregister stake address in single TX
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file, stake_addr_dereg_cert],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        if use_build_cmd:
            tx_raw_output_deleg = cluster.g_transaction.build_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_deleg_dereg",
                tx_files=tx_files,
                fee_buffer=2_000_000,
                witness_override=len(tx_files.signing_key_files),
            )
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output_deleg.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_deleg_dereg",
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output_deleg.txins)
        else:
            tx_raw_output_deleg = cluster.g_transaction.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_deleg_dereg",
                tx_files=tx_files,
            )

        # check that the balance for source address was correctly updated and that the key
        # deposit was returned
        assert (
            cluster.g_query.get_address_balance(user_payment.address)
            == src_registered_balance
            - tx_raw_output_deleg.fee
            + cluster.g_query.get_address_deposit()
        ), f"Incorrect balance for source address `{user_payment.address}`"

        # check that the stake address was NOT delegated
        stake_addr_info = cluster.g_query.get_stake_addr_info(user_registered.stake.address)
        assert not stake_addr_info.delegation, f"Stake address was delegated: {stake_addr_info}"

        tx_db_deleg = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_deleg)
        if tx_db_deleg:
            assert user_registered.stake.address in tx_db_deleg.stake_deregistration
            assert user_registered.stake.address == tx_db_deleg.stake_delegation[0].address
            assert tx_db_deleg.stake_delegation[0].active_epoch_no == init_epoch + 2
            assert pool_id == tx_db_deleg.stake_delegation[0].pool_id

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    @pytest.mark.smoke
    def test_addr_registration_certificate_order(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
        use_build_cmd: bool,
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
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment
        src_init_balance = cluster.g_query.get_address_balance(user_payment.address)

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # create stake address deregistration cert
        stake_addr_dereg_cert_file = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register, deregister, register, deregister and register stake address in single TX
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

        if use_build_cmd:
            tx_raw_output = cluster.g_transaction.build_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_reg_dereg_cert_order",
                tx_files=tx_files,
                fee_buffer=2_000_000,
                witness_override=len(tx_files.signing_key_files),
                deposit=deposit,
            )
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_reg_dereg_cert_order",
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
        else:
            tx_raw_output = cluster.g_transaction.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_reg_dereg",
                tx_files=tx_files,
                deposit=deposit,
            )

        # check that the stake address is registered
        assert cluster.g_query.get_stake_addr_info(
            user_registered.stake.address
        ).address, f"Stake address is not registered: {user_registered.stake.address}"

        # check that the balance for source address was correctly updated and that key deposit
        # was needed
        assert (
            cluster.g_query.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output.fee - deposit
        ), f"Incorrect balance for source address `{user_payment.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            assert user_registered.stake.address in tx_db_record.stake_registration
            assert user_registered.stake.address in tx_db_record.stake_deregistration


@pytest.mark.testnets
@pytest.mark.smoke
class TestNegative:
    """Tests that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    def test_registration_cert_with_wrong_key(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to generate stake address registration certificate using wrong stake vkey.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        # create stake address registration cert, use wrong stake vkey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_stake_address.gen_stake_addr_registration_cert(
                addr_name=f"{temp_template}_addr0", stake_vkey_file=pool_users[0].payment.vkey_file
            )
        err_msg = str(excinfo.value)
        assert "Expected: StakeVerificationKeyShelley" in err_msg, err_msg

    @allure.link(helpers.get_vcs_link())
    def test_delegation_cert_with_wrong_key(
        self,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
        pool_users_cluster_and_pool: List[clusterlib.PoolUser],
    ):
        """Try to generate stake address delegation certificate using wrong stake vkey.

        Expect failure.
        """
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)

        # create stake address delegation cert, use wrong stake vkey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_stake_address.gen_stake_addr_delegation_cert(
                addr_name=f"{temp_template}_addr0",
                stake_vkey_file=pool_users_cluster_and_pool[0].payment.vkey_file,
                stake_pool_id=pool_id,
            )
        err_msg = str(excinfo.value)
        assert "Expected: StakeVerificationKeyShelley" in err_msg, err_msg

    @allure.link(helpers.get_vcs_link())
    def test_register_addr_with_wrong_key(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Try to register stake address using wrong payment skey.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register stake address, use wrong payment skey
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
    def test_delegate_addr_with_wrong_key(
        self,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
        pool_users_cluster_and_pool: List[clusterlib.PoolUser],
        pool_users_disposable_cluster_and_pool: List[clusterlib.PoolUser],
    ):
        """Try to delegate stake address using wrong payment skey.

        Expect failure.
        """
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable_cluster_and_pool[0]
        user_payment = pool_users_cluster_and_pool[0].payment

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[user_payment.skey_file],
        )
        cluster.g_transaction.send_tx(
            src_address=user_payment.address, tx_name=f"{temp_template}_reg", tx_files=tx_files
        )

        # check that the stake address is registered
        assert cluster.g_query.get_stake_addr_info(
            user_registered.stake.address
        ).address, f"Stake address is not registered: {user_registered.stake.address}"

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.g_stake_address.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            stake_pool_id=pool_id,
        )

        # delegate stake address, use wrong payment skey
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file],
            signing_key_files=[pool_users_cluster_and_pool[1].payment.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_deleg",
                tx_files=tx_files,
            )
        err_msg = str(excinfo.value)
        assert "MissingVKeyWitnessesUTXOW" in err_msg, err_msg

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_delegate_unknown_addr(
        self,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
        pool_users_cluster_and_pool: List[clusterlib.PoolUser],
        pool_users_disposable_cluster_and_pool: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to delegate unknown stake address.

        Expect failure.
        """
        cluster, pool_id = cluster_and_pool
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        user_registered = pool_users_disposable_cluster_and_pool[0]
        user_payment = pool_users_cluster_and_pool[0].payment

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.g_stake_address.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            stake_pool_id=pool_id,
        )

        # delegate unknown stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                tx_raw_output = cluster.g_transaction.build_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_deleg_unknown",
                    tx_files=tx_files,
                    fee_buffer=2_000_000,
                    witness_override=len(tx_files.signing_key_files),
                )
                tx_signed = cluster.g_transaction.sign_tx(
                    tx_body_file=tx_raw_output.out_file,
                    signing_key_files=tx_files.signing_key_files,
                    tx_name=f"{temp_template}_deleg_unknown",
                )
                cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
            else:
                cluster.g_transaction.send_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_deleg_unknown",
                    tx_files=tx_files,
                )
        err_msg = str(excinfo.value)
        assert "StakeDelegationImpossibleDELEG" in err_msg, err_msg

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_delegate_deregistered_addr(
        self,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
        pool_users_cluster_and_pool: List[clusterlib.PoolUser],
        pool_users_disposable_cluster_and_pool: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to delegate deregistered stake address.

        Expect failure.
        """
        cluster, pool_id = cluster_and_pool
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        user_registered = pool_users_disposable_cluster_and_pool[0]
        user_payment = pool_users_cluster_and_pool[0].payment

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[user_payment.skey_file],
        )
        cluster.g_transaction.send_tx(
            src_address=user_payment.address, tx_name=f"{temp_template}_reg", tx_files=tx_files
        )

        # check that the stake address is registered
        assert cluster.g_query.get_stake_addr_info(
            user_registered.stake.address
        ).address, f"Stake address is not registered: {user_registered.stake.address}"

        # deregister stake address
        stake_addr_dereg_cert_file = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert_file],
            signing_key_files=[
                user_payment.skey_file,
                user_registered.stake.skey_file,
            ],
        )
        cluster.g_transaction.send_tx(
            src_address=user_payment.address,
            tx_name=f"{temp_template}_dereg",
            tx_files=tx_files_deregister,
        )

        # check that the stake address is not registered
        assert not cluster.g_query.get_stake_addr_info(
            user_registered.stake.address
        ).address, f"Stake address is registered: {user_registered.stake.address}"

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.g_stake_address.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            stake_pool_id=pool_id,
        )

        # delegate deregistered stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                tx_raw_output = cluster.g_transaction.build_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_deleg_dereg",
                    tx_files=tx_files,
                    fee_buffer=2_000_000,
                    witness_override=len(tx_files.signing_key_files),
                )
                tx_signed = cluster.g_transaction.sign_tx(
                    tx_body_file=tx_raw_output.out_file,
                    signing_key_files=tx_files.signing_key_files,
                    tx_name=f"{temp_template}_deleg_dereg",
                )
                cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
            else:
                cluster.g_transaction.send_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_deleg_dereg",
                    tx_files=tx_files,
                )
        err_msg = str(excinfo.value)
        assert "StakeDelegationImpossibleDELEG" in err_msg, err_msg

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_deregister_not_registered_addr(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Deregister not registered stake address."""
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # files for deregistering stake address
        stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                tx_raw_output = cluster.g_transaction.build_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_dereg_fail",
                    tx_files=tx_files,
                    fee_buffer=2_000_000,
                    witness_override=len(tx_files.signing_key_files),
                )
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

    @allure.link(helpers.get_vcs_link())
    def test_delegatee_not_registered(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Try to delegate stake address to unregistered pool.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[user_payment.skey_file],
        )
        cluster.g_transaction.send_tx(
            src_address=user_payment.address, tx_name=f"{temp_template}_reg", tx_files=tx_files
        )

        # check that the stake address is registered
        assert cluster.g_query.get_stake_addr_info(
            user_registered.stake.address
        ).address, f"Stake address is not registered: {user_registered.stake.address}"

        # create pool cold keys and ceritifcate, but don't register the pool
        node_cold = cluster.g_node.gen_cold_key_pair_and_counter(node_name=f"{temp_template}_pool")

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.g_stake_address.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
        )

        # delegate stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file],
            signing_key_files=[pool_users[0].payment.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_deleg",
                tx_files=tx_files,
            )
        err_msg = str(excinfo.value)
        assert "DelegateeNotRegisteredDELEG" in err_msg, err_msg
