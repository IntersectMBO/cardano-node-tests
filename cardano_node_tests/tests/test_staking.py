import logging
from pathlib import Path
from typing import Any
from typing import Dict

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
        f"addr0_{temp_template}", cluster_obj=cluster_obj, stake_vkey_file=stake_addr_rec.vkey_file,
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
        deleg_kwargs["node_cold_vkey_file"] = node_cold.vkey_file

    stake_addr_deleg_cert_file = cluster_obj.gen_stake_addr_delegation_cert(**deleg_kwargs)

    # fund source address
    helpers.fund_from_faucet(
        payment_addr_rec, cluster_obj=cluster_obj, faucet_data=addrs_data["user1"], request=request,
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


def _withdraw_reward(cluster_obj: clusterlib.ClusterLib, pool_user: clusterlib.PoolUser) -> None:
    """Withdraw reward to payment address."""
    src_address = pool_user.payment.address
    src_init_balance = cluster_obj.get_address_balance(src_address)

    tx_files_withdrawal = clusterlib.TxFiles(
        signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
    )
    tx_raw_withdrawal_output = cluster_obj.send_tx(
        src_address=src_address,
        tx_files=tx_files_withdrawal,
        withdrawals=[clusterlib.TxOut(address=pool_user.stake.address, amount=-1)],
    )
    cluster_obj.wait_for_new_block(new_blocks=2)

    # check that reward is 0
    assert (
        cluster_obj.get_stake_addr_info(pool_user.stake.address).reward_account_balance == 0
    ), "Not all rewards were transfered"

    # check that reward was transfered
    src_reward_balance = cluster_obj.get_address_balance(src_address)
    assert (
        src_reward_balance
        == src_init_balance
        - tx_raw_withdrawal_output.fee
        + tx_raw_withdrawal_output.withdrawals[0].amount  # type: ignore
    ), f"Incorrect balance for source address `{src_address}`"


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

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
        )

        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=pool_user.stake.vkey_file
        )

        src_address = pool_user.payment.address

        # wait for first reward
        helpers.wait_for(
            lambda: cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance,
            delay=10,
            num_sec=3 * cluster.epoch_length_sec,
            message="receive rewards",
        )

        # files for de-registering stake address
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
        )

        # de-registration is expected to fail because there are rewards in the stake address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(src_address=src_address, tx_files=tx_files_deregister)
        assert "StakeKeyNonZeroAccountBalanceDELEG" in str(excinfo.value)

        # withdraw reward to payment address
        _withdraw_reward(cluster_obj=cluster, pool_user=pool_user)

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
            (init_epoch, cluster.get_stake_addr_info(rewards_address).reward_account_balance,)
        ]

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
            pool_name=pool_name,
        )

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

            prev_stake_reward_epoch, prev_stake_reward_amount = stake_rewards[-1]
            prev_owner_reward_epoch, prev_owner_reward_amount = owner_rewards[-1]

            stake_rewards.append((this_epoch, stake_reward))
            owner_rewards.append((this_epoch, owner_reward))

            # check that new reward was received by the pool owner
            assert (
                owner_reward > prev_owner_reward_amount
            ), "New reward was not received by pool owner"

            if prev_owner_reward_epoch == this_epoch - 1:
                assert (
                    prev_owner_reward_amount * 1.2 < owner_reward < prev_owner_reward_amount * 2
                ), "Unexpected reward amount for pool owner"

            # wait up to 3 epochs for first reward for stake address
            if this_epoch - init_epoch <= 3 and stake_reward == 0:
                continue

            # check that new reward was received by the stake address
            assert (
                stake_reward > prev_stake_reward_amount
            ), "New reward was not received by stake address"

            if prev_stake_reward_epoch == this_epoch - 1:
                assert (
                    prev_stake_reward_amount * 1.2 < stake_reward < prev_stake_reward_amount * 2
                ), "Unexpected reward amount for stake address"

        # withdraw reward to payment address
        _withdraw_reward(cluster_obj=cluster, pool_user=pool_user)

    def test_no_reward_high_pledge(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ):
        """Check that the stake address is not receiving rewards.

        When the pledge is higher than available funds, neither pool owners not those who
        delegate to that pool receive rewards.
        """
        cluster = cluster_session
        pool_name = "node-pool2"
        temp_template = "test_no_reward"

        helpers.wait_for_stake_distribution(cluster)

        node_cold = addrs_data_session[pool_name]["cold_key_pair"]
        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)

        # load and update original pool data
        loaded_data = helpers.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=stake_pool_id
        )
        pool_data_updated = loaded_data._replace(pool_pledge=loaded_data.pool_pledge * 9)

        # update the pool parameters by resubmitting the pool registration certificate
        pool_rec = addrs_data_session[pool_name]
        cluster.register_stake_pool(
            pool_data=pool_data_updated,
            pool_owners=[
                clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"],)
            ],
            node_vrf_vkey_file=pool_rec["vrf_key_pair"].vkey_file,
            node_cold_key_pair=pool_rec["cold_key_pair"],
            deposit=0,  # no additional deposit, the pool is already registered
        )

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
            pool_name=pool_name,
        )

        cluster.wait_for_new_epoch(3)

        orig_owner_reward = cluster.get_stake_addr_info(
            addrs_data_session[pool_name]["reward"].address
        ).reward_account_balance

        # check that no new rewards are received
        cluster.wait_for_new_epoch(3)

        assert not cluster.get_stake_addr_info(
            pool_user.stake.address
        ).reward_account_balance, "Received unexpected rewards"

        # check that pool owner is also not receiving rewards
        assert (
            orig_owner_reward
            == cluster.get_stake_addr_info(
                addrs_data_session[pool_name]["reward"].address
            ).reward_account_balance
        ), "Pool owner received unexpected rewards"
