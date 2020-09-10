import logging
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List

import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir and change to it."""
    tmp_path = Path(tmp_path_factory.mktemp("test_staking"))
    with helpers.change_cwd(tmp_path):
        yield tmp_path


@pytest.fixture(scope="module")
def pool_users(
    cluster_session: clusterlib.ClusterLib,
    addrs_data_session: dict,
    request: FixtureRequest,
) -> List[clusterlib.PoolUser]:
    """Create pool users."""
    pool_users = helpers.create_pool_users(
        cluster_obj=cluster_session,
        name_template="test_staking_pool_users",
        no_of_addr=2,
    )

    # fund source addresses
    helpers.fund_from_faucet(
        pool_users[0],
        cluster_obj=cluster_session,
        faucet_data=addrs_data_session["user1"],
        request=request,
    )

    return pool_users


@pytest.fixture
def pool_users_disposable(
    cluster_session: clusterlib.ClusterLib,
) -> List[clusterlib.PoolUser]:
    """Create function scoped pool users."""
    pool_users = helpers.create_pool_users(
        cluster_obj=cluster_session,
        name_template=f"test_staking_pool_users_{clusterlib.get_rand_str(3)}",
        no_of_addr=2,
    )
    return pool_users


def _cleanup_deregister_stake_addr(
    cluster_obj: clusterlib.ClusterLib, pool_user: clusterlib.PoolUser, name_template: str
) -> None:
    try:
        helpers.deregister_stake_addr(
            cluster_obj=cluster_obj, pool_user=pool_user, name_template=name_template
        )
    except clusterlib.CLIError:
        pass


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


def _delegate_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    addrs_data: dict,
    temp_template: str,
    request: FixtureRequest,
    pool_name: str = "node-pool1",
    delegate_with_pool_id: bool = False,
) -> clusterlib.PoolUser:
    """Submit registration certificate and delegate to pool."""
    node_cold = addrs_data[pool_name]["cold_key_pair"]
    stake_pool_id = cluster_obj.get_stake_pool_id(node_cold.vkey_file)

    # create key pairs and addresses
    stake_addr_rec = helpers.create_stake_addr_records(
        f"addr0_{temp_template}", cluster_obj=cluster_obj
    )[0]
    payment_addr_rec = helpers.create_payment_addr_records(
        f"addr0_{temp_template}",
        cluster_obj=cluster_obj,
        stake_vkey_file=stake_addr_rec.vkey_file,
    )[0]

    pool_user = clusterlib.PoolUser(payment=payment_addr_rec, stake=stake_addr_rec)

    # create stake address registration cert
    stake_addr_reg_cert_file = cluster_obj.gen_stake_addr_registration_cert(
        addr_name=f"addr0_{temp_template}", stake_vkey_file=stake_addr_rec.vkey_file
    )

    # create stake address delegation cert
    deleg_kwargs: Dict[str, Any] = {
        "addr_name": f"addr0_{temp_template}",
        "stake_vkey_file": stake_addr_rec.vkey_file,
    }
    if delegate_with_pool_id:
        deleg_kwargs["stake_pool_id"] = stake_pool_id
    else:
        deleg_kwargs["cold_vkey_file"] = node_cold.vkey_file

    stake_addr_deleg_cert_file = cluster_obj.gen_stake_addr_delegation_cert(**deleg_kwargs)

    # fund source address
    helpers.fund_from_faucet(
        payment_addr_rec,
        cluster_obj=cluster_obj,
        faucet_data=addrs_data["user1"],
        amount=100_000_000,
        request=request,
    )

    src_address = payment_addr_rec.address
    src_init_balance = cluster_obj.get_address_balance(src_address)

    # register stake address and delegate it to pool
    tx_files = clusterlib.TxFiles(
        certificate_files=[stake_addr_reg_cert_file, stake_addr_deleg_cert_file],
        signing_key_files=[payment_addr_rec.skey_file, stake_addr_rec.skey_file],
    )
    tx_raw_output = cluster_obj.send_tx(src_address=src_address, tx_files=tx_files)
    cluster_obj.wait_for_new_block(new_blocks=2)

    # deregister stake address in post-test cleanup
    # TODO: change if this is supposed to run outside of devops cluster
    if pool_name in ("node-pool1", "node-pool2"):
        request.addfinalizer(
            lambda: _cleanup_deregister_stake_addr(
                cluster_obj=cluster_obj, pool_user=pool_user, name_template=temp_template
            )
        )

    # check that the balance for source address was correctly updated
    assert (
        cluster_obj.get_address_balance(src_address)
        == src_init_balance - tx_raw_output.fee - cluster_obj.get_key_deposit()
    ), f"Incorrect balance for source address `{src_address}`"

    helpers.wait_for_stake_distribution(cluster_obj)

    # check that the stake address was delegated
    stake_addr_info = cluster_obj.get_stake_addr_info(stake_addr_rec.address)
    assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"

    assert stake_pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

    return pool_user


class TestDelegateAddr:
    def test_delegate_using_pool_id(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ):
        """Submit registration certificate and delegate to pool using pool id."""
        cluster = cluster_session
        temp_template = "test_delegate_using_addr"

        # submit registration certificate and delegate to pool
        _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
            delegate_with_pool_id=True,
        )

    def test_delegate_using_vkey(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ):
        """Submit registration certificate and delegate to pool using cold vkey."""
        cluster = cluster_session
        temp_template = "test_delegate_using_cert"

        # submit registration certificate and delegate to pool
        _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
        )

    def test_deregister(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ):
        """De-register stake address."""
        cluster = cluster_session
        temp_template = "test_deregister_addr"
        pool_name = "node-pool1"
        pool_rewards_address = addrs_data_session[pool_name]["reward"].address

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
            pool_name=pool_name,
        )
        helpers.wait_for_stake_distribution(cluster)

        src_address = pool_user.payment.address
        init_pool_reward_balance = cluster.get_stake_addr_info(
            pool_rewards_address
        ).reward_account_balance

        # wait for first reward
        stake_reward = helpers.wait_for(
            lambda: cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance,
            delay=10,
            num_sec=4 * cluster.epoch_length_sec,
            message="receive rewards",
            silent=True,
        )

        if (
            not stake_reward
            and cluster.get_stake_addr_info(pool_rewards_address).reward_account_balance
            == init_pool_reward_balance
        ):
            pytest.skip(f"Pool '{pool_name}' hasn't received any rewards, cannot continue.")

        assert stake_reward, "Expected rewards were not received"

        # files for de-registering stake address
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=pool_user.stake.vkey_file
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
        )

        # de-registration is expected to fail because there are rewards in the stake address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(src_address=src_address, tx_files=tx_files_deregister)
        assert "StakeKeyNonZeroAccountBalanceDELEG" in str(excinfo.value)

        # withdraw rewards to payment address
        helpers.withdraw_reward(cluster_obj=cluster, pool_user=pool_user)

        # de-register stake address
        src_reward_balance = cluster.get_address_balance(src_address)

        tx_raw_deregister_output = cluster.send_tx(
            src_address=src_address, tx_files=tx_files_deregister
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

    def test_addr_registration_deregistration(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Submit registration and deregistration certificates in single TX."""
        cluster = cluster_session
        temp_template = "test_addr_registration_deregistration"

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment
        src_init_balance = cluster.get_address_balance(user_payment.address)

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=user_registered.stake.vkey_file
        )

        # create stake address deregistration cert
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register and deregister stake address in single TX
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file, stake_addr_dereg_cert],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )
        tx_raw_output = cluster.send_tx(src_address=user_payment.address, tx_files=tx_files)
        cluster.wait_for_new_block(new_blocks=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output.fee
        ), f"Incorrect balance for source address `{user_payment.address}`"

    def test_addr_delegation_deregistration(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
        addrs_data_session: dict,
    ):
        """Submit delegation and deregistration certificates in single TX."""
        cluster = cluster_session
        pool_name = "node-pool1"
        temp_template = "test_addr_delegation_deregistration"
        node_cold = addrs_data_session[pool_name]["cold_key_pair"]

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment
        src_init_balance = cluster.get_address_balance(user_payment.address)

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=user_registered.stake.vkey_file
        )

        # create stake address deregistration cert
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[user_payment.skey_file],
        )
        tx_raw_output_reg = cluster.send_tx(src_address=user_payment.address, tx_files=tx_files)
        cluster.wait_for_new_block(new_blocks=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output_reg.fee - cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{user_payment.address}`"

        src_registered_balance = cluster.get_address_balance(user_payment.address)

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            addr_name=f"addr0_{temp_template}",
            stake_vkey_file=user_registered.stake.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
        )

        # delegate and deregister stake address in single TX
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file, stake_addr_dereg_cert],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )
        tx_raw_output_deleg = cluster.send_tx(src_address=user_payment.address, tx_files=tx_files)
        cluster.wait_for_new_block(new_blocks=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(user_payment.address)
            == src_registered_balance - tx_raw_output_deleg.fee + cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{user_payment.address}`"

        helpers.wait_for_stake_distribution(cluster)

        # check that the stake address was NOT delegated
        stake_addr_info = cluster.get_stake_addr_info(user_registered.stake.address)
        assert not stake_addr_info.delegation, f"Stake address was delegated: {stake_addr_info}"


class TestNegative:
    def test_registration_cert_with_wrong_key(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Generate stake address registration certificate using wrong key."""
        temp_template = "test_registration_cert_with_wrong_key"

        # create stake address registration cert, use wrong stake vkey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_session.gen_stake_addr_registration_cert(
                addr_name=f"addr0_{temp_template}", stake_vkey_file=pool_users[0].payment.vkey_file
            )
        assert "Expected: StakeVerificationKeyShelley" in str(excinfo.value)

    def test_delegation_cert_with_wrong_key(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addrs_data_session: dict,
    ):
        """Generate stake address delegation certificate using wrong key."""
        pool_name = "node-pool1"
        node_cold = addrs_data_session[pool_name]["cold_key_pair"]
        temp_template = "test_delegation_cert_with_wrong_key"

        # create stake address delegation cert, use wrong stake vkey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_session.gen_stake_addr_delegation_cert(
                addr_name=f"addr0_{temp_template}",
                stake_vkey_file=pool_users[0].payment.vkey_file,
                cold_vkey_file=node_cold.vkey_file,
            )
        assert "Expected: StakeVerificationKeyShelley" in str(excinfo.value)

    def test_register_addr_with_wrong_key(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Register stake address using wrong key."""
        cluster = cluster_session
        temp_template = "test_register_addr_with_wrong_key"

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register stake address, use wrong payment skey
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[pool_users[1].payment.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(src_address=user_payment.address, tx_files=tx_files)
        assert "MissingVKeyWitnessesUTXOW" in str(excinfo.value)

    def test_delegate_addr_with_wrong_key(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
        addrs_data_session: dict,
    ):
        """Delegate stake address using wrong key."""
        cluster = cluster_session
        pool_name = "node-pool1"
        temp_template = "test_delegate_addr_with_wrong_key"
        node_cold = addrs_data_session[pool_name]["cold_key_pair"]

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=user_registered.stake.vkey_file
        )

        # register stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[user_payment.skey_file],
        )
        cluster.send_tx(src_address=user_payment.address, tx_files=tx_files)
        cluster.wait_for_new_block(new_blocks=2)

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            addr_name=f"addr0_{temp_template}",
            stake_vkey_file=user_registered.stake.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
        )

        # delegate stake address, use wrong payment skey
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file],
            signing_key_files=[pool_users[1].payment.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(src_address=user_payment.address, tx_files=tx_files)
        assert "MissingVKeyWitnessesUTXOW" in str(excinfo.value)

    def test_delegate_unregistered_addr(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
        addrs_data_session: dict,
    ):
        """Delegate unregistered stake address."""
        cluster = cluster_session
        pool_name = "node-pool1"
        temp_template = "test_delegate_unregistered_addr"
        node_cold = addrs_data_session[pool_name]["cold_key_pair"]

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            addr_name=f"addr0_{temp_template}",
            stake_vkey_file=user_registered.stake.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
        )

        # delegate unregistered stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file],
            signing_key_files=[user_payment.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(src_address=user_payment.address, tx_files=tx_files)
        assert "StakeDelegationImpossibleDELEG" in str(excinfo.value)

    def test_unregister_not_registered_addr(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Unregistered not registered stake address."""
        cluster = cluster_session
        temp_template = "test_unregister_not_registered_addr"

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # files for deregistering stake address
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=user_registered.stake.vkey_file
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(src_address=user_payment.address, tx_files=tx_files)
        assert "StakeKeyNonZeroAccountBalanceDELEG" in str(excinfo.value)


class TestRewards:
    def test_reward_amount(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ):
        """Check that the stake address and pool owner are receiving rewards."""
        cluster = cluster_session
        pool_name = "node-pool1"
        temp_template = "test_reward_amount"
        rewards_address = addrs_data_session[pool_name]["reward"].address

        init_epoch = cluster.get_last_block_epoch()
        stake_rewards = [(init_epoch, 0)]
        owner_rewards = [
            (init_epoch, cluster.get_stake_addr_info(rewards_address).reward_account_balance)
        ]

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
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
        helpers.withdraw_reward(cluster_obj=cluster, pool_user=pool_user)

    @pytest.mark.first
    def test_no_reward_unmet_pledge(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ):
        """Check that the stake pool is not receiving rewards when pledge is not met.

        When the pledge is higher than available funds, neither pool owners nor those who
        delegate to that pool receive rewards.
        """
        cluster = cluster_session
        pool_name = "node-pool2"
        pool_rec = addrs_data_session[pool_name]
        temp_template = "test_no_reward_unmet_pledge"

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
            pool_name=pool_name,
        )

        node_cold = pool_rec["cold_key_pair"]
        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)

        # load and update original pool data
        loaded_data = helpers.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=stake_pool_id
        )
        pool_data_updated = loaded_data._replace(pool_pledge=loaded_data.pool_pledge * 9)

        # update the pool parameters by resubmitting the pool registration certificate
        cluster.register_stake_pool(
            pool_data=pool_data_updated,
            pool_owners=[clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])],
            vrf_vkey_file=pool_rec["vrf_key_pair"].vkey_file,
            cold_key_pair=pool_rec["cold_key_pair"],
            deposit=0,  # no additional deposit, the pool is already registered
        )

        cluster.wait_for_new_epoch(3)

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

    @pytest.mark.first
    def test_no_reward_stake_unregistered(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ):
        """Check that the pool is not receiving rewards when owner's stake address is unregistered.

        When the owner's stake address is unregistered (i.e. owner's stake is lower than pledge),
        neither pool owners nor those who delegate to that pool receive rewards.
        """
        cluster = cluster_session
        pool_name = "node-pool2"
        pool_rec = addrs_data_session[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        temp_template = "test_no_reward_stake_unregistered"

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
            pool_name=pool_name,
        )

        # deregister stake address
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=pool_owner.stake.vkey_file
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[pool_owner.payment.skey_file, pool_owner.stake.skey_file],
        )

        src_init_balance = cluster.get_address_balance(pool_owner.payment.address)

        tx_raw_deregister_output = cluster.send_tx(
            src_address=pool_owner.payment.address, tx_files=tx_files_deregister
        )
        cluster.wait_for_new_block(new_blocks=2)

        # check that the key deposit was returned
        assert (
            cluster.get_address_balance(pool_owner.payment.address)
            == src_init_balance - tx_raw_deregister_output.fee + cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{pool_owner.payment.address}`"

        cluster.wait_for_new_epoch(3)

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

    @pytest.mark.first
    def test_reward_unregistered(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ):
        """Check that the reward address is not receiving rewards when unregistered.

        The stake pool continues to operate normally and those who delegate to that pool receive
        rewards.
        """
        cluster = cluster_session
        temp_template = "test_no_reward_stake_unregistered"
        pool_name = "node-pool2"
        pool_rec = addrs_data_session[pool_name]
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
            pool_name=pool_name,
        )
        # wait for first reward
        helpers.wait_for(
            lambda: cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance,
            delay=10,
            num_sec=4 * cluster.epoch_length_sec,
            message="receive rewards",
            silent=True,
        )

        # withdraw rewards to payment address
        helpers.withdraw_reward(cluster_obj=cluster, pool_user=pool_reward)

        # deregister reward address
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=pool_reward.stake.vkey_file
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[pool_reward.payment.skey_file, pool_reward.stake.skey_file],
        )

        src_init_balance = cluster.get_address_balance(pool_reward.payment.address)

        tx_raw_deregister_output = cluster.send_tx(
            src_address=pool_reward.payment.address, tx_files=tx_files_deregister
        )
        cluster.wait_for_new_block(new_blocks=2)

        # check that the key deposit was returned
        assert (
            cluster.get_address_balance(pool_reward.payment.address)
            == src_init_balance - tx_raw_deregister_output.fee + cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

        cluster.wait_for_new_epoch(3)

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
