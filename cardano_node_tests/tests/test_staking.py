import logging
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List

import allure
import pytest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import parallel_run

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir and change to it."""
    tmp_path = Path(tmp_path_factory.mktemp(helpers.get_id_for_mktemp(__file__)))
    with helpers.change_cwd(tmp_path):
        yield tmp_path


@pytest.fixture
def pool_users(
    cluster_manager: parallel_run.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.PoolUser]:
    """Create pool users."""
    data_key = id(pool_users)
    cached_value = cluster_manager.cache.test_data.get(data_key)
    if cached_value:
        return cached_value  # type: ignore

    created_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster,
        name_template="test_staking_pool_users",
        no_of_addr=2,
    )
    cluster_manager.cache.test_data[data_key] = created_users

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
    pool_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster,
        name_template=f"test_staking_pool_users_{clusterlib.get_rand_str(3)}",
        no_of_addr=2,
    )
    return pool_users


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


def _delegate_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    addrs_data: dict,
    temp_template: str,
    pool_name: str,
    delegate_with_pool_id: bool = False,
) -> clusterlib.PoolUser:
    """Submit registration certificate and delegate to pool."""
    node_cold = addrs_data[pool_name]["cold_key_pair"]
    stake_pool_id = cluster_obj.get_stake_pool_id(node_cold.vkey_file)

    # create key pairs and addresses
    stake_addr_rec = clusterlib_utils.create_stake_addr_records(
        f"{temp_template}_addr0", cluster_obj=cluster_obj
    )[0]
    payment_addr_rec = clusterlib_utils.create_payment_addr_records(
        f"{temp_template}_addr0",
        cluster_obj=cluster_obj,
        stake_vkey_file=stake_addr_rec.vkey_file,
    )[0]

    pool_user = clusterlib.PoolUser(payment=payment_addr_rec, stake=stake_addr_rec)

    # create stake address registration cert
    stake_addr_reg_cert_file = cluster_obj.gen_stake_addr_registration_cert(
        addr_name=f"{temp_template}_addr0", stake_vkey_file=stake_addr_rec.vkey_file
    )

    # create stake address delegation cert
    deleg_kwargs: Dict[str, Any] = {
        "addr_name": f"{temp_template}_addr0",
        "stake_vkey_file": stake_addr_rec.vkey_file,
    }
    if delegate_with_pool_id:
        deleg_kwargs["stake_pool_id"] = stake_pool_id
    else:
        deleg_kwargs["cold_vkey_file"] = node_cold.vkey_file

    stake_addr_deleg_cert_file = cluster_obj.gen_stake_addr_delegation_cert(**deleg_kwargs)

    # fund source address
    clusterlib_utils.fund_from_faucet(
        payment_addr_rec,
        cluster_obj=cluster_obj,
        faucet_data=addrs_data["user1"],
        amount=100_000_000,
    )

    src_address = payment_addr_rec.address
    src_init_balance = cluster_obj.get_address_balance(src_address)

    # register stake address and delegate it to pool
    tx_files = clusterlib.TxFiles(
        certificate_files=[stake_addr_reg_cert_file, stake_addr_deleg_cert_file],
        signing_key_files=[payment_addr_rec.skey_file, stake_addr_rec.skey_file],
    )
    tx_raw_output = cluster_obj.send_tx(
        src_address=src_address, tx_name=f"{temp_template}_reg_deleg", tx_files=tx_files
    )
    cluster_obj.wait_for_new_block(new_blocks=2)

    # check that the balance for source address was correctly updated
    assert (
        cluster_obj.get_address_balance(src_address)
        == src_init_balance - tx_raw_output.fee - cluster_obj.get_key_deposit()
    ), f"Incorrect balance for source address `{src_address}`"

    clusterlib_utils.wait_for_stake_distribution(cluster_obj)

    # check that the stake address was delegated
    stake_addr_info = cluster_obj.get_stake_addr_info(stake_addr_rec.address)
    assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"

    assert stake_pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

    return pool_user


class TestDelegateAddr:
    @allure.link(helpers.get_vcs_link())
    def test_delegate_using_pool_id(
        self,
        cluster_manager: parallel_run.ClusterManager,
    ):
        """Submit registration certificate and delegate to pool using pool id."""
        pool_name = "node-pool1"
        cluster = cluster_manager.get(use_resources=[pool_name])
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            delegate_with_pool_id=True,
            pool_name=pool_name,
        )

    @allure.link(helpers.get_vcs_link())
    def test_delegate_using_vkey(
        self,
        cluster_manager: parallel_run.ClusterManager,
    ):
        """Submit registration certificate and delegate to pool using cold vkey."""
        pool_name = "node-pool1"
        cluster = cluster_manager.get(use_resources=[pool_name])
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )

    @allure.link(helpers.get_vcs_link())
    def test_deregister(
        self,
        cluster_manager: parallel_run.ClusterManager,
    ):
        """Deregister stake address."""
        pool_name = "node-pool1"
        cluster = cluster_manager.get(use_resources=[pool_name])
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )
        clusterlib_utils.wait_for_stake_distribution(cluster)

        src_address = pool_user.payment.address

        # wait for first reward
        stake_reward = helpers.wait_for(
            lambda: cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance,
            delay=10,
            num_sec=4 * cluster.epoch_length_sec + 100,
            message="receive rewards",
            silent=True,
        )
        if not stake_reward:
            cluster_manager.set_needs_restart()
            pytest.skip(f"Pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # files for deregistering stake address
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=pool_user.stake.vkey_file
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
        )

        # deregistration is expected to fail because there are rewards in the stake address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=src_address,
                tx_name=f"{temp_template}_dereg_fail",
                tx_files=tx_files_deregister,
            )
        assert "StakeKeyNonZeroAccountBalanceDELEG" in str(excinfo.value)

        # withdraw rewards to payment address
        clusterlib_utils.withdraw_reward(
            cluster_obj=cluster, pool_user=pool_user, name_template=temp_template
        )

        # deregister stake address
        src_reward_balance = cluster.get_address_balance(src_address)

        tx_raw_deregister_output = cluster.send_tx(
            src_address=src_address,
            tx_name=f"{temp_template}_dereg",
            tx_files=tx_files_deregister,
        )
        cluster.wait_for_new_block(new_blocks=2)

        # check that the key deposit was returned
        assert (
            cluster.get_address_balance(src_address)
            == src_reward_balance - tx_raw_deregister_output.fee + cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        # check that the stake address is no longer delegated
        stake_addr_info = cluster.get_stake_addr_info(pool_user.stake.address)
        assert (
            not stake_addr_info.delegation
        ), f"Stake address is still delegated: {stake_addr_info}"

    @allure.link(helpers.get_vcs_link())
    def test_addr_registration_deregistration(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Submit registration and deregistration certificates in single TX."""
        temp_template = helpers.get_func_name()

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment
        src_init_balance = cluster.get_address_balance(user_payment.address)

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # create stake address deregistration cert
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register and deregister stake address in single TX
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file, stake_addr_dereg_cert],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )
        tx_raw_output = cluster.send_tx(
            src_address=user_payment.address,
            tx_name=f"{temp_template}_reg_dereg",
            tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output.fee
        ), f"Incorrect balance for source address `{user_payment.address}`"

    @allure.link(helpers.get_vcs_link())
    def test_addr_delegation_deregistration(
        self,
        cluster_manager: parallel_run.ClusterManager,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Submit delegation and deregistration certificates in single TX."""
        pool_name = "node-pool1"
        cluster = cluster_manager.get(use_resources=[pool_name])

        temp_template = helpers.get_func_name()
        node_cold = cluster_manager.cache.addrs_data[pool_name]["cold_key_pair"]

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment
        src_init_balance = cluster.get_address_balance(user_payment.address)

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # create stake address deregistration cert
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[user_payment.skey_file],
        )
        tx_raw_output_reg = cluster.send_tx(
            src_address=user_payment.address,
            tx_name=f"{temp_template}_reg",
            tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output_reg.fee - cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{user_payment.address}`"

        src_registered_balance = cluster.get_address_balance(user_payment.address)

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
        )

        # delegate and deregister stake address in single TX
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file, stake_addr_dereg_cert],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )
        tx_raw_output_deleg = cluster.send_tx(
            src_address=user_payment.address,
            tx_name=f"{temp_template}_deleg_dereg",
            tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(user_payment.address)
            == src_registered_balance - tx_raw_output_deleg.fee + cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{user_payment.address}`"

        clusterlib_utils.wait_for_stake_distribution(cluster)

        # check that the stake address was NOT delegated
        stake_addr_info = cluster.get_stake_addr_info(user_registered.stake.address)
        assert not stake_addr_info.delegation, f"Stake address was delegated: {stake_addr_info}"


class TestNegative:
    @allure.link(helpers.get_vcs_link())
    def test_registration_cert_with_wrong_key(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Generate stake address registration certificate using wrong key."""
        temp_template = helpers.get_func_name()

        # create stake address registration cert, use wrong stake vkey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.gen_stake_addr_registration_cert(
                addr_name=f"{temp_template}_addr0", stake_vkey_file=pool_users[0].payment.vkey_file
            )
        assert "Expected: StakeVerificationKeyShelley" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_delegation_cert_with_wrong_key(
        self,
        cluster_manager: parallel_run.ClusterManager,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Generate stake address delegation certificate using wrong key."""
        pool_name = "node-pool1"
        cluster = cluster_manager.get(use_resources=[pool_name])

        node_cold = cluster_manager.cache.addrs_data[pool_name]["cold_key_pair"]
        temp_template = helpers.get_func_name()

        # create stake address delegation cert, use wrong stake vkey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.gen_stake_addr_delegation_cert(
                addr_name=f"{temp_template}_addr0",
                stake_vkey_file=pool_users[0].payment.vkey_file,
                cold_vkey_file=node_cold.vkey_file,
            )
        assert "Expected: StakeVerificationKeyShelley" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_register_addr_with_wrong_key(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Register stake address using wrong key."""
        temp_template = helpers.get_func_name()

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register stake address, use wrong payment skey
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[pool_users[1].payment.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=user_payment.address, tx_name=temp_template, tx_files=tx_files
            )
        assert "MissingVKeyWitnessesUTXOW" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_delegate_addr_with_wrong_key(
        self,
        cluster_manager: parallel_run.ClusterManager,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Delegate stake address using wrong key."""
        pool_name = "node-pool1"
        cluster = cluster_manager.get(use_resources=[pool_name])

        temp_template = helpers.get_func_name()
        node_cold = cluster_manager.cache.addrs_data[pool_name]["cold_key_pair"]

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[user_payment.skey_file],
        )
        cluster.send_tx(
            src_address=user_payment.address, tx_name=f"{temp_template}_reg", tx_files=tx_files
        )
        cluster.wait_for_new_block(new_blocks=2)

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
        )

        # delegate stake address, use wrong payment skey
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file],
            signing_key_files=[pool_users[1].payment.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_deleg",
                tx_files=tx_files,
            )
        assert "MissingVKeyWitnessesUTXOW" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_delegate_unregistered_addr(
        self,
        cluster_manager: parallel_run.ClusterManager,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Delegate unregistered stake address."""
        pool_name = "node-pool1"
        cluster = cluster_manager.get(use_resources=[pool_name])

        temp_template = helpers.get_func_name()
        node_cold = cluster_manager.cache.addrs_data[pool_name]["cold_key_pair"]

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
        )

        # delegate unregistered stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file],
            signing_key_files=[user_payment.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_deleg_unreg",
                tx_files=tx_files,
            )
        assert "StakeDelegationImpossibleDELEG" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_unregister_not_registered_addr(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Unregistered not registered stake address."""
        temp_template = helpers.get_func_name()

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # files for deregistering stake address
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=user_registered.stake.vkey_file
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_dereg_fail",
                tx_files=tx_files,
            )
        assert "StakeKeyNonZeroAccountBalanceDELEG" in str(excinfo.value)


class TestRewards:
    @allure.link(helpers.get_vcs_link())
    def test_reward_amount(
        self,
        cluster_manager: parallel_run.ClusterManager,
    ):
        """Check that the stake address and pool owner are receiving rewards."""
        pool_name = "node-pool1"
        cluster = cluster_manager.get(use_resources=[pool_name])

        temp_template = helpers.get_func_name()
        rewards_address = cluster_manager.cache.addrs_data[pool_name]["reward"].address

        init_epoch = cluster.get_last_block_epoch()
        stake_rewards = [(init_epoch, 0)]
        owner_rewards = [
            (init_epoch, cluster.get_stake_addr_info(rewards_address).reward_account_balance)
        ]

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )
        delegate_epoch = cluster.get_last_block_epoch()

        # check that new reward is received every epoch
        LOGGER.info("Checking rewards for 5 epochs.")
        for __ in range(5):
            # let's wait for new epoch and then some more seconds
            cluster.wait_for_new_epoch(padding_seconds=30)

            this_epoch = cluster.get_last_block_epoch()
            stake_reward = cluster.get_stake_addr_info(
                pool_user.stake.address
            ).reward_account_balance
            owner_reward = cluster.get_stake_addr_info(rewards_address).reward_account_balance

            __, prev_stake_reward_amount = stake_rewards[-1]
            __, prev_owner_reward_amount = owner_rewards[-1]

            stake_rewards.append((this_epoch, stake_reward))
            owner_rewards.append((this_epoch, owner_reward))

            # check that new reward was received by the pool owner
            assert (
                owner_reward > prev_owner_reward_amount
            ), "New reward was not received by pool owner"

            # wait up to 3 epochs for first reward for stake address
            if this_epoch - delegate_epoch <= 3 and stake_reward == 0:
                continue

            # check that new reward was received by the stake address
            assert (
                stake_reward > prev_stake_reward_amount
            ), "New reward was not received by stake address"

        # withdraw rewards to payment address
        clusterlib_utils.withdraw_reward(
            cluster_obj=cluster, pool_user=pool_user, name_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_transfered_funds(
        self,
        cluster_manager: parallel_run.ClusterManager,
    ):
        """Check that the stake address is not receiving rewards when funds are transfered."""
        pool_name = "node-pool1"
        cluster = cluster_manager.get(use_resources=[pool_name])

        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )

        # create destination address for rewards
        dst_addr_record = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_dst_addr", cluster_obj=cluster
        )[0]

        # fund destination address
        clusterlib_utils.fund_from_faucet(
            dst_addr_record,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        # wait for first reward
        stake_reward = helpers.wait_for(
            lambda: cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance,
            delay=10,
            num_sec=4 * cluster.epoch_length_sec + 100,
            message="receive rewards",
            silent=True,
        )
        if not stake_reward:
            cluster_manager.set_needs_restart()
            pytest.skip(f"User '{pool_name}' hasn't received any rewards, cannot continue.")

        # transfer all funds back to faucet
        clusterlib_utils.return_funds_to_faucet(
            pool_user.payment,
            cluster_obj=cluster,
            faucet_addr=cluster_manager.cache.addrs_data["user1"]["payment"].address,
            tx_name=temp_template,
        )
        assert (
            cluster.get_address_balance(pool_user.payment.address) == 0
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        # make sure user is not receiving rewards
        for epoch in range(5):
            # withdraw rewards to destination address
            if cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance:
                clusterlib_utils.withdraw_reward(
                    cluster_obj=cluster,
                    pool_user=pool_user,
                    name_template=f"{temp_template}_ep{epoch}",
                    dst_addr_record=dst_addr_record,
                )
            cluster.wait_for_new_epoch(padding_seconds=30)

        # wait for two epochs and check that no new rewards were received
        cluster.wait_for_new_epoch(2, padding_seconds=30)
        assert cluster.get_stake_addr_info(
            pool_user.stake.address
        ).reward_account_balance, "Unexpected reward was received by stake address"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_unmet_pledge1(
        self,
        cluster_manager: parallel_run.ClusterManager,
    ):
        """Check that the stake pool is not receiving rewards when pledge is not met.

        When the pledge is higher than available funds, neither pool owners nor those who
        delegate to that pool receive rewards.

        Increase the needed pledge amount by changing pool parameters.
        """
        pool_name = "node-pool2"
        cluster = cluster_manager.get(lock_resources=[pool_name])

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )

        # wait for first reward
        stake_reward = helpers.wait_for(
            lambda: cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance,
            delay=10,
            num_sec=4 * cluster.epoch_length_sec + 100,
            message="receive rewards",
            silent=True,
        )
        if not stake_reward:
            cluster_manager.set_needs_restart()
            pytest.skip(f"Pool '{pool_name}' hasn't received any rewards, cannot continue.")

        node_cold = pool_rec["cold_key_pair"]
        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)

        # load and update original pool data
        loaded_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=stake_pool_id
        )
        pool_data_updated = loaded_data._replace(pool_pledge=loaded_data.pool_pledge * 9)

        # update the pool parameters by resubmitting the pool registration certificate
        cluster.register_stake_pool(
            pool_data=pool_data_updated,
            pool_owners=[pool_owner],
            vrf_vkey_file=pool_rec["vrf_key_pair"].vkey_file,
            cold_key_pair=pool_rec["cold_key_pair"],
            tx_name=f"{temp_template}_update_param",
            reward_account_vkey_file=pool_rec["reward"].vkey_file,
            deposit=0,  # no additional deposit, the pool is already registered
        )

        cluster.wait_for_new_epoch(4, padding_seconds=30)

        orig_owner_reward = cluster.get_stake_addr_info(
            pool_rec["reward"].address
        ).reward_account_balance
        orig_user_reward = cluster.get_stake_addr_info(
            pool_user.stake.address
        ).reward_account_balance

        cluster.wait_for_new_epoch(3)

        with cluster_manager.needs_restart_after_failure():
            # check that NO new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                == cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance
            ), "Received unexpected rewards"

            # check that pool owner is also NOT receiving rewards
            assert (
                orig_owner_reward
                == cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # fund source (pledge) address
            clusterlib_utils.fund_from_faucet(
                pool_owner,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=150_000_000,
                force=True,
            )

            # update the pool to original parameters by resubmitting
            # the pool registration certificate
            cluster.register_stake_pool(
                pool_data=loaded_data,
                pool_owners=[pool_owner],
                vrf_vkey_file=pool_rec["vrf_key_pair"].vkey_file,
                cold_key_pair=pool_rec["cold_key_pair"],
                tx_name=f"{temp_template}_update_to_orig",
                reward_account_vkey_file=pool_rec["reward"].vkey_file,
                deposit=0,  # no additional deposit, the pool is already registered
            )

            cluster.wait_for_new_epoch(5, padding_seconds=30)

            # check that new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                orig_owner_reward
                < cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "New reward was not received by pool reward address"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_unmet_pledge2(
        self,
        cluster_manager: parallel_run.ClusterManager,
    ):
        """Check that the stake pool is not receiving rewards when pledge is not met.

        When the pledge is higher than available funds, neither pool owners nor those who
        delegate to that pool receive rewards.

        Withdraw part of pledge so the funds are lower than what is needed by the stake pool.
        """
        pool_name = "node-pool2"
        cluster = cluster_manager.get(lock_resources=[pool_name])

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )

        # wait for first reward
        stake_reward = helpers.wait_for(
            lambda: cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance,
            delay=10,
            num_sec=4 * cluster.epoch_length_sec + 100,
            message="receive rewards",
            silent=True,
        )
        if not stake_reward:
            cluster_manager.set_needs_restart()
            pytest.skip(f"Pool '{pool_name}' hasn't received any rewards, cannot continue.")

        node_cold = pool_rec["cold_key_pair"]
        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)

        # load pool data
        loaded_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=stake_pool_id
        )

        pledge_amount = loaded_data.pool_pledge // 2

        # withdraw part of the pledge
        destinations = [clusterlib.TxOut(address=pool_user.payment.address, amount=pledge_amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_owner.payment.skey_file])
        cluster.send_funds(
            src_address=pool_owner.payment.address,
            destinations=destinations,
            tx_name=f"{temp_template}_withdraw_pledge",
            tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert cluster.get_address_balance(pool_owner.payment.address) < loaded_data.pool_pledge, (
            f"Pledge still high - pledge: {loaded_data.pool_pledge}, "
            f"funds: {cluster.get_address_balance(pool_owner.payment.address)}"
        )

        cluster.wait_for_new_epoch(4, padding_seconds=30)

        orig_owner_reward = cluster.get_stake_addr_info(
            pool_rec["reward"].address
        ).reward_account_balance
        orig_user_reward = cluster.get_stake_addr_info(
            pool_user.stake.address
        ).reward_account_balance

        cluster.wait_for_new_epoch(3)

        with cluster_manager.needs_restart_after_failure():
            # check that NO new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                == cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance
            ), "Received unexpected rewards"

            # check that pool owner is also NOT receiving rewards
            assert (
                orig_owner_reward
                == cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # fund user address so it has enough funds for fees etc.
            clusterlib_utils.fund_from_faucet(
                pool_user,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=150_000_000,
                force=True,
            )

            # return pledge
            destinations = [
                clusterlib.TxOut(
                    address=pool_owner.payment.address, amount=pledge_amount + 100_000_000
                )
            ]
            tx_files = clusterlib.TxFiles(signing_key_files=[pool_user.payment.skey_file])
            cluster.send_funds(
                src_address=pool_user.payment.address,
                destinations=destinations,
                tx_name=f"{temp_template}_return_pledge",
                tx_files=tx_files,
            )
            cluster.wait_for_new_block(new_blocks=2)

            assert (
                cluster.get_address_balance(pool_owner.payment.address) >= loaded_data.pool_pledge
            ), (
                f"Funds still low - pledge: {loaded_data.pool_pledge}, "
                f"funds: {cluster.get_address_balance(pool_owner.payment.address)}"
            )

            cluster.wait_for_new_epoch(5, padding_seconds=30)

            # check that new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                orig_owner_reward
                < cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "New reward was not received by pool reward address"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_unregistered_stake_addr(
        self,
        cluster_manager: parallel_run.ClusterManager,
    ):
        """Check that the pool is not receiving rewards when owner's stake address is unregistered.

        When the owner's stake address is unregistered (i.e. owner's stake is lower than pledge),
        neither pool owners nor those who delegate to that pool receive rewards.
        """
        pool_name = "node-pool2"
        cluster = cluster_manager.get(lock_resources=[pool_name])

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )

        # wait for first reward
        stake_reward = helpers.wait_for(
            lambda: cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance,
            delay=10,
            num_sec=4 * cluster.epoch_length_sec + 100,
            message="receive rewards",
            silent=True,
        )
        if not stake_reward:
            cluster_manager.set_needs_restart()
            pytest.skip(f"Pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # deregister stake address
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=pool_owner.stake.vkey_file
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[pool_owner.payment.skey_file, pool_owner.stake.skey_file],
        )

        src_init_balance = cluster.get_address_balance(pool_owner.payment.address)

        tx_raw_deregister_output = cluster.send_tx(
            src_address=pool_owner.payment.address,
            tx_name=f"{temp_template}_dereg",
            tx_files=tx_files_deregister,
        )
        cluster.wait_for_new_block(new_blocks=2)

        with cluster_manager.needs_restart_after_failure():
            # check that the key deposit was returned
            assert (
                cluster.get_address_balance(pool_owner.payment.address)
                == src_init_balance - tx_raw_deregister_output.fee + cluster.get_key_deposit()
            ), f"Incorrect balance for source address `{pool_owner.payment.address}`"

            cluster.wait_for_new_epoch(4, padding_seconds=30)

            # check that the stake address is no longer delegated
            assert not cluster.get_stake_addr_info(
                pool_owner.stake.address
            ), "Stake address still delegated"

            orig_owner_reward = cluster.get_stake_addr_info(
                pool_rec["reward"].address
            ).reward_account_balance
            orig_user_reward = cluster.get_stake_addr_info(
                pool_user.stake.address
            ).reward_account_balance

            cluster.wait_for_new_epoch(3)

            # check that NO new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                == cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance
            ), "Received unexpected rewards"

            # check that pool owner is also NOT receiving rewards
            assert (
                orig_owner_reward
                == cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # fund source address
            clusterlib_utils.fund_from_faucet(
                pool_owner,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=150_000_000,
                force=True,
            )

            src_updated_balance = cluster.get_address_balance(pool_owner.payment.address)

            # reregister stake address and delegate it to pool
            tx_files = clusterlib.TxFiles(
                certificate_files=[
                    pool_rec["stake_addr_registration_cert"],
                    pool_rec["stake_addr_delegation_cert"],
                ],
                signing_key_files=[pool_owner.payment.skey_file, pool_owner.stake.skey_file],
            )
            tx_raw_output = cluster.send_tx(
                src_address=pool_owner.payment.address,
                tx_name=f"{temp_template}_rereg_deleg",
                tx_files=tx_files,
            )
            cluster.wait_for_new_block(new_blocks=2)

            # check that the balance for source address was correctly updated
            assert (
                cluster.get_address_balance(pool_owner.payment.address)
                == src_updated_balance - tx_raw_output.fee - cluster.get_key_deposit()
            ), f"Incorrect balance for source address `{pool_owner.payment.address}`"

            cluster.wait_for_new_epoch(4, padding_seconds=30)

            # check that the stake address was delegated
            stake_addr_info = cluster.get_stake_addr_info(pool_owner.stake.address)
            assert (
                stake_addr_info.delegation
            ), f"Stake address was not delegated yet: {stake_addr_info}"

            node_cold = cluster_manager.cache.addrs_data[pool_name]["cold_key_pair"]
            stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)
            assert (
                stake_pool_id == stake_addr_info.delegation
            ), "Stake address delegated to wrong pool"

            # check that new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "New reward was not received by pool reward address"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_unregistered_reward_addr(
        self,
        cluster_manager: parallel_run.ClusterManager,
    ):
        """Check that the reward address is not receiving rewards when unregistered.

        The stake pool continues to operate normally and those who delegate to that pool receive
        rewards.
        """
        pool_name = "node-pool2"
        cluster = cluster_manager.get(lock_resources=[pool_name])

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )

        # wait for first reward
        stake_reward = helpers.wait_for(
            lambda: cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance,
            delay=10,
            num_sec=4 * cluster.epoch_length_sec + 100,
            message="receive rewards",
            silent=True,
        )
        if not stake_reward:
            cluster_manager.set_needs_restart()
            pytest.skip(f"Pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # withdraw rewards to payment address
        clusterlib_utils.withdraw_reward(
            cluster_obj=cluster, pool_user=pool_reward, name_template=temp_template
        )

        # deregister reward address
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=pool_reward.stake.vkey_file
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[pool_reward.payment.skey_file, pool_reward.stake.skey_file],
        )

        src_init_balance = cluster.get_address_balance(pool_reward.payment.address)

        tx_raw_deregister_output = cluster.send_tx(
            src_address=pool_reward.payment.address,
            tx_name=f"{temp_template}_dereg_reward",
            tx_files=tx_files_deregister,
        )
        cluster.wait_for_new_block(new_blocks=2)

        with cluster_manager.needs_restart_after_failure():
            # check that the key deposit was returned
            assert (
                cluster.get_address_balance(pool_reward.payment.address)
                == src_init_balance - tx_raw_deregister_output.fee + cluster.get_key_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            cluster.wait_for_new_epoch(4, padding_seconds=30)

            # check that the reward address is no longer delegated
            assert not cluster.get_stake_addr_info(
                pool_reward.stake.address
            ), "Stake address still delegated"

            orig_pool_reward = cluster.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance
            orig_user_reward = cluster.get_stake_addr_info(
                pool_user.stake.address
            ).reward_account_balance

            cluster.wait_for_new_epoch(3)

            # check that NO new rewards were received by pool owner
            assert (
                orig_pool_reward
                == cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # check that new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance
            ), "New reward was not received by stake address"

            # fund source address
            clusterlib_utils.fund_from_faucet(
                pool_reward,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=150_000_000,
                force=True,
            )

            src_updated_balance = cluster.get_address_balance(pool_reward.payment.address)

            node_cold = cluster_manager.cache.addrs_data[pool_name]["cold_key_pair"]
            stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)

            # reregister stake address and delegate it to pool
            reward_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
                addr_name=f"{temp_template}_addr0",
                stake_vkey_file=pool_reward.stake.vkey_file,
                cold_vkey_file=node_cold.vkey_file,
            )
            tx_files = clusterlib.TxFiles(
                certificate_files=[
                    pool_rec["reward_addr_registration_cert"],
                    reward_addr_deleg_cert_file,
                ],
                signing_key_files=[pool_reward.payment.skey_file, pool_reward.stake.skey_file],
            )
            tx_raw_output = cluster.send_tx(
                src_address=pool_reward.payment.address,
                tx_name=f"{temp_template}_rereg_deleg",
                tx_files=tx_files,
            )
            cluster.wait_for_new_block(new_blocks=2)

            # check that the balance for source address was correctly updated
            assert (
                cluster.get_address_balance(pool_reward.payment.address)
                == src_updated_balance - tx_raw_output.fee - cluster.get_key_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            cluster.wait_for_new_epoch(4, padding_seconds=30)

            # check that the stake address was delegated
            stake_addr_info = cluster.get_stake_addr_info(pool_reward.stake.address)
            assert (
                stake_addr_info.delegation
            ), f"Stake address was not delegated yet: {stake_addr_info}"

            assert (
                stake_pool_id == stake_addr_info.delegation
            ), "Stake address delegated to wrong pool"

            # check that new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "New reward was not received by pool reward address"
