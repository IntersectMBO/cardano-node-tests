"""Tests for checking staking scenarios where no rewards are expected."""
import logging
from pathlib import Path
from typing import Any
from typing import List
from typing import Tuple

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import kes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@pytest.mark.order(6)
@pytest.mark.long
class TestNoRewards:
    @allure.link(helpers.get_vcs_link())
    def test_no_reward_unmet_pledge1(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool: Tuple[clusterlib.ClusterLib, str],
    ):
        """Check that the stake pool is not receiving rewards when pledge is not met.

        When the pledge is higher than available funds, neither pool owners nor those who
        delegate to that pool receive rewards.

        * delegate stake address
        * wait for first reward
        * increase the needed pledge amount - update the pool parameters by resubmitting the pool
          registration certificate - the funds are now lower than what is needed by the stake pool
        * check that NO new rewards were received by those delegating to the pool
        * check that pool owner is also NOT receiving rewards
        * return the pool to the original state - restore pledge settings
        * check that new rewards were received by those delegating to the pool
        * check that pool owner is also receiving rewards
        """
        cluster, pool_name = cluster_lock_pool

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        temp_template = common.get_test_id(cluster)

        pool_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

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

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish the pool update in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # load and update original pool data
        loaded_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=pool_id
        )
        pool_data_updated = loaded_data._replace(pool_pledge=loaded_data.pool_pledge * 9)

        # increase the needed pledge amount - update the pool parameters by resubmitting the pool
        # registration certificate
        cluster.g_stake_pool.register_stake_pool(
            pool_data=pool_data_updated,
            pool_owners=[pool_owner],
            vrf_vkey_file=pool_rec["vrf_key_pair"].vkey_file,
            cold_key_pair=pool_rec["cold_key_pair"],
            tx_name=f"{temp_template}_update_param",
            reward_account_vkey_file=pool_rec["reward"].vkey_file,
            deposit=0,  # no additional deposit, the pool is already registered
        )

        cluster.wait_for_new_epoch(4, padding_seconds=30)

        orig_owner_reward = cluster.g_query.get_stake_addr_info(
            pool_rec["reward"].address
        ).reward_account_balance
        orig_user_reward = cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance

        cluster.wait_for_new_epoch(3)

        with cluster_manager.respin_on_failure():
            # check that NO new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                == cluster.g_query.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "Received unexpected rewards"

            # check that pool owner is also NOT receiving rewards
            assert (
                orig_owner_reward
                == cluster.g_query.get_stake_addr_info(
                    pool_rec["reward"].address
                ).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # Return the pool to the original state - restore pledge settings.

            # fund pool owner's addresses so balance keeps higher than pool pledge after fees etc.
            # are deducted
            clusterlib_utils.fund_from_faucet(
                pool_owner,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=900_000_000,
                force=True,
            )

            # update the pool to original parameters by resubmitting
            # the pool registration certificate
            cluster.g_stake_pool.register_stake_pool(
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
                < cluster.g_query.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                orig_owner_reward
                < cluster.g_query.get_stake_addr_info(
                    pool_rec["reward"].address
                ).reward_account_balance
            ), "New reward was not received by pool reward address"

        # check that pledge is still met after the owner address was used to pay for Txs
        pool_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=pool_name, pool_id=pool_id
        )
        owner_payment_balance = cluster.g_query.get_address_balance(pool_owner.payment.address)
        assert (
            owner_payment_balance >= pool_data.pool_pledge
        ), f"Pledge is not met for pool '{pool_name}'!"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_unmet_pledge2(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool: Tuple[clusterlib.ClusterLib, str],
    ):
        """Check that the stake pool is not receiving rewards when pledge is not met.

        When the pledge is higher than available funds, neither pool owners nor those who
        delegate to that pool receive rewards.

        * delegate stake address
        * wait for first reward
        * withdraw part of the pledge - the funds are lower than what is needed by the stake pool
        * check that NO new rewards were received by those delegating to the pool
        * check that pool owner is also NOT receiving rewards
        * return the pool to the original state - restore pledge funds
        * check that new rewards were received by those delegating to the pool
        * check that pool owner is also receiving rewards
        """
        cluster, pool_name = cluster_lock_pool

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        temp_template = common.get_test_id(cluster)

        pool_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

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

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to withdraw the pledge in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # load pool data
        loaded_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=pool_id
        )

        pledge_amount = loaded_data.pool_pledge // 2

        # withdraw part of the pledge
        destinations = [
            clusterlib.TxOut(address=delegation_out.pool_user.payment.address, amount=pledge_amount)
        ]
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_owner.payment.skey_file])
        cluster.g_transaction.send_funds(
            src_address=pool_owner.payment.address,
            destinations=destinations,
            tx_name=f"{temp_template}_withdraw_pledge",
            tx_files=tx_files,
        )

        assert (
            cluster.g_query.get_address_balance(pool_owner.payment.address)
            < loaded_data.pool_pledge
        ), (
            f"Pledge still high - pledge: {loaded_data.pool_pledge}, "
            f"funds: {cluster.g_query.get_address_balance(pool_owner.payment.address)}"
        )

        cluster.wait_for_new_epoch(4, padding_seconds=30)

        orig_owner_reward = cluster.g_query.get_stake_addr_info(
            pool_rec["reward"].address
        ).reward_account_balance
        orig_user_reward = cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance

        cluster.wait_for_new_epoch(3)

        with cluster_manager.respin_on_failure():
            # check that NO new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                == cluster.g_query.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "Received unexpected rewards"

            # check that pool owner is also NOT receiving rewards
            assert (
                orig_owner_reward
                == cluster.g_query.get_stake_addr_info(
                    pool_rec["reward"].address
                ).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # Return the pool to the original state - restore pledge funds.

            # fund user address so it has enough funds for fees etc.
            clusterlib_utils.fund_from_faucet(
                delegation_out.pool_user,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=900_000_000,
                force=True,
            )

            # return pledge
            destinations = [
                clusterlib.TxOut(
                    address=pool_owner.payment.address, amount=pledge_amount + 100_000_000
                )
            ]
            tx_files = clusterlib.TxFiles(
                signing_key_files=[delegation_out.pool_user.payment.skey_file]
            )
            cluster.g_transaction.send_funds(
                src_address=delegation_out.pool_user.payment.address,
                destinations=destinations,
                tx_name=f"{temp_template}_return_pledge",
                tx_files=tx_files,
            )

            assert (
                cluster.g_query.get_address_balance(pool_owner.payment.address)
                >= loaded_data.pool_pledge
            ), (
                f"Funds still low - pledge: {loaded_data.pool_pledge}, "
                f"funds: {cluster.g_query.get_address_balance(pool_owner.payment.address)}"
            )

            cluster.wait_for_new_epoch(5, padding_seconds=30)

            # check that new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.g_query.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                orig_owner_reward
                < cluster.g_query.get_stake_addr_info(
                    pool_rec["reward"].address
                ).reward_account_balance
            ), "New reward was not received by pool reward address"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_deregistered_stake_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool: Tuple[clusterlib.ClusterLib, str],
    ):
        """Check that the pool is not receiving rewards when owner's stake address is deregistered.

        When the owner's stake address is deregistered (i.e. owner's stake is lower than pledge),
        neither pool owners nor those who delegate to that pool receive rewards.

        * delegate stake address
        * wait for first reward
        * deregister stake address - owner's stake is lower than pledge
        * check that the key deposit was returned
        * check that NO new rewards were received by those delegating to the pool
        * check that pool owner is also NOT receiving rewards
        * return the pool to the original state - reregister stake address and
          delegate it to the pool
        * check that new rewards were received by those delegating to the pool
        * check that pool owner is also receiving rewards
        """
        cluster, pool_name = cluster_lock_pool

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        temp_template = common.get_test_id(cluster)

        pool_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

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

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # withdraw rewards from owner's stake address if there are any
        if cluster.g_query.get_stake_addr_info(pool_owner.stake.address).reward_account_balance:
            cluster.g_stake_address.withdraw_reward(
                stake_addr_record=pool_owner.stake,
                dst_addr_record=pool_owner.payment,
                tx_name=temp_template,
            )

        # deregister stake address - owner's stake is lower than pledge
        stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=pool_owner.stake.vkey_file
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[pool_owner.payment.skey_file, pool_owner.stake.skey_file],
        )

        src_init_balance = cluster.g_query.get_address_balance(pool_owner.payment.address)

        tx_raw_deregister_output = cluster.g_transaction.send_tx(
            src_address=pool_owner.payment.address,
            tx_name=f"{temp_template}_dereg",
            tx_files=tx_files_deregister,
        )

        with cluster_manager.respin_on_failure():
            # check that the key deposit was returned
            assert (
                cluster.g_query.get_address_balance(pool_owner.payment.address)
                == src_init_balance
                - tx_raw_deregister_output.fee
                + cluster.g_query.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_owner.payment.address}`"

            # check that the stake address is no longer delegated
            assert not cluster.g_query.get_stake_addr_info(
                pool_owner.stake.address
            ), "Stake address still delegated"

            cluster.wait_for_new_epoch(4, padding_seconds=30)

            orig_owner_reward = cluster.g_query.get_stake_addr_info(
                pool_rec["reward"].address
            ).reward_account_balance
            orig_user_reward = cluster.g_query.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance

            cluster.wait_for_new_epoch(3)

            # check that NO new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                == cluster.g_query.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "Received unexpected rewards"

            # check that pool owner is also NOT receiving rewards
            assert (
                orig_owner_reward
                == cluster.g_query.get_stake_addr_info(
                    pool_rec["reward"].address
                ).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # Return the pool to the original state - reregister stake address and
            # delegate it to the pool.

            # fund pool owner's addresses so balance keeps higher than pool pledge after fees etc.
            # are deducted
            clusterlib_utils.fund_from_faucet(
                pool_owner,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=900_000_000,
                force=True,
            )

            src_updated_balance = cluster.g_query.get_address_balance(pool_owner.payment.address)

            # reregister stake address and delegate it to pool
            tx_files = clusterlib.TxFiles(
                certificate_files=[
                    pool_rec["stake_addr_registration_cert"],
                    pool_rec["stake_addr_delegation_cert"],
                ],
                signing_key_files=[pool_owner.payment.skey_file, pool_owner.stake.skey_file],
            )
            tx_raw_output = cluster.g_transaction.send_tx(
                src_address=pool_owner.payment.address,
                tx_name=f"{temp_template}_rereg_deleg",
                tx_files=tx_files,
            )

            # check that the balance for source address was correctly updated
            assert (
                cluster.g_query.get_address_balance(pool_owner.payment.address)
                == src_updated_balance - tx_raw_output.fee - cluster.g_query.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_owner.payment.address}`"

            cluster.wait_for_new_epoch(4, padding_seconds=30)

            # check that the stake address was delegated
            stake_addr_info = cluster.g_query.get_stake_addr_info(pool_owner.stake.address)
            assert (
                stake_addr_info.delegation
            ), f"Stake address was not delegated yet: {stake_addr_info}"

            assert pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

            # check that new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.g_query.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                orig_user_reward
                < cluster.g_query.get_stake_addr_info(
                    pool_rec["reward"].address
                ).reward_account_balance
            ), "New reward was not received by pool reward address"

        # check that pledge is still met after the owner address was used to pay for Txs
        pool_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=pool_name, pool_id=pool_id
        )
        owner_payment_balance = cluster.g_query.get_address_balance(pool_owner.payment.address)
        assert (
            owner_payment_balance >= pool_data.pool_pledge
        ), f"Pledge is not met for pool '{pool_name}'!"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_deregistered_reward_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool: Tuple[clusterlib.ClusterLib, str],
    ):
        """Check that the reward address is not receiving rewards when deregistered.

        The stake pool continues to operate normally and those who delegate to that pool receive
        rewards.

        * delegate stake address
        * wait for first reward
        * withdraw pool rewards to payment address
        * deregister the pool reward address
        * check that the key deposit was returned
        * check that pool owner is NOT receiving rewards
        * check that new rewards are received by those delegating to the pool
        * return the pool to the original state - reregister reward address
        * check that pool owner is receiving rewards
        """
        cluster, pool_name = cluster_lock_pool

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
        temp_template = common.get_test_id(cluster)

        pool_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

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

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # withdraw pool rewards to payment address
        # use `transaction build` if possible
        if common.BUILD_UNUSABLE:
            cluster.g_stake_address.withdraw_reward(
                stake_addr_record=pool_reward.stake,
                dst_addr_record=pool_reward.payment,
                tx_name=temp_template,
            )
        else:
            clusterlib_utils.withdraw_reward_w_build(
                cluster_obj=cluster,
                stake_addr_record=pool_reward.stake,
                dst_addr_record=pool_reward.payment,
                tx_name=temp_template,
            )

        # deregister the pool reward address
        stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=pool_reward.stake.vkey_file
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[pool_reward.payment.skey_file, pool_reward.stake.skey_file],
        )

        src_init_balance = cluster.g_query.get_address_balance(pool_reward.payment.address)

        tx_raw_deregister_output = cluster.g_transaction.send_tx(
            src_address=pool_reward.payment.address,
            tx_name=f"{temp_template}_dereg_reward",
            tx_files=tx_files_deregister,
        )

        with cluster_manager.respin_on_failure():
            # check that the key deposit was returned
            assert (
                cluster.g_query.get_address_balance(pool_reward.payment.address)
                == src_init_balance
                - tx_raw_deregister_output.fee
                + cluster.g_query.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            # check that the reward address is no longer delegated
            assert not cluster.g_query.get_stake_addr_info(
                pool_reward.stake.address
            ), "Stake address still delegated"

            orig_user_reward = cluster.g_query.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance

            cluster.wait_for_new_epoch(3)

            # check that pool owner is NOT receiving rewards
            assert (
                cluster.g_query.get_stake_addr_info(
                    pool_reward.stake.address
                ).reward_account_balance
                == 0
            ), "Pool owner received unexpected rewards"

            # check that new rewards are received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.g_query.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "New reward was not received by stake address"

            # Return the pool to the original state - reregister reward address.

            # fund pool owner's addresses so balance keeps higher than pool pledge after fees etc.
            # are deducted
            clusterlib_utils.fund_from_faucet(
                pool_reward,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=900_000_000,
                force=True,
            )

            src_updated_balance = cluster.g_query.get_address_balance(pool_reward.payment.address)

            # reregister reward address
            tx_files = clusterlib.TxFiles(
                certificate_files=[
                    pool_rec["reward_addr_registration_cert"],
                ],
                signing_key_files=[pool_reward.payment.skey_file, pool_reward.stake.skey_file],
            )
            tx_raw_output = cluster.g_transaction.send_tx(
                src_address=pool_reward.payment.address,
                tx_name=f"{temp_template}_rereg_deleg",
                tx_files=tx_files,
            )

            # check that the balance for source address was correctly updated
            assert (
                cluster.g_query.get_address_balance(pool_reward.payment.address)
                == src_updated_balance - tx_raw_output.fee - cluster.g_query.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            cluster.wait_for_new_epoch(4, padding_seconds=30)

            # check that new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.g_query.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                cluster.g_query.get_stake_addr_info(
                    pool_reward.stake.address
                ).reward_account_balance
                > 0
            ), "New reward was not received by pool reward address"

        # check that pledge is still met after the owner address was used to pay for Txs
        pool_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=pool_name, pool_id=pool_id
        )
        owner_payment_balance = cluster.g_query.get_address_balance(pool_reward.payment.address)
        assert (
            owner_payment_balance >= pool_data.pool_pledge
        ), f"Pledge is not met for pool '{pool_name}'!"

    @allure.link(helpers.get_vcs_link())
    def test_deregister_reward_addr_retire_pool(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool: Tuple[clusterlib.ClusterLib, str],
    ):
        """Test deregistering reward address and retiring stake pool.

        The pool deposit is lost when reward address is deregistered before the pool is retired.

        * wait for first reward for the pool
        * withdraw pool rewards to payment address
        * deregister the pool reward address
        * check that the key deposit was returned
        * check that pool owner is NOT receiving rewards
        * deregister stake pool
        * check that the pool deposit was NOT returned to reward or stake address
        * return the pool to the original state - reregister the pool, register
          the reward address, delegate the stake address to the pool
        * check that pool deposit was needed
        * check that pool owner is receiving rewards
        """
        # pylint: disable=too-many-statements,too-many-locals
        __: Any  # mypy workaround
        cluster, pool_name = cluster_lock_pool
        pool_num = int(pool_name.replace("node-pool", ""))

        kes_period_info_errors_list: List[str] = []

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        pool_opcert_file: Path = pool_rec["pool_operational_cert"]
        temp_template = common.get_test_id(cluster)

        LOGGER.info("Waiting up to 4 full epochs for first reward.")
        for i in range(5):
            if i > 0:
                cluster.wait_for_new_epoch(padding_seconds=10)
            if cluster.g_query.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance:
                break
        else:
            pytest.skip(f"Pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish reward address deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # withdraw pool rewards to payment address
        cluster.g_stake_address.withdraw_reward(
            stake_addr_record=pool_reward.stake,
            dst_addr_record=pool_reward.payment,
            tx_name=temp_template,
        )

        # deregister the pool reward address
        stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0", stake_vkey_file=pool_reward.stake.vkey_file
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[pool_reward.payment.skey_file, pool_reward.stake.skey_file],
        )

        src_init_balance = cluster.g_query.get_address_balance(pool_reward.payment.address)

        tx_raw_deregister_output = cluster.g_transaction.send_tx(
            src_address=pool_reward.payment.address,
            tx_name=f"{temp_template}_dereg_reward",
            tx_files=tx_files_deregister,
        )

        with cluster_manager.respin_on_failure():
            # check that the key deposit was returned
            assert (
                cluster.g_query.get_address_balance(pool_reward.payment.address)
                == src_init_balance
                - tx_raw_deregister_output.fee
                + cluster.g_query.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            # check that the reward address is no longer delegated
            assert not cluster.g_query.get_stake_addr_info(
                pool_reward.stake.address
            ), "Stake address still delegated"

            cluster.wait_for_new_epoch(3)

            # check that pool owner is NOT receiving rewards
            assert (
                cluster.g_query.get_stake_addr_info(
                    pool_reward.stake.address
                ).reward_account_balance
                == 0
            ), "Pool owner received unexpected rewards"

            # fund pool owner's addresses so balance keeps higher than pool pledge after fees etc.
            # are deducted
            clusterlib_utils.fund_from_faucet(
                pool_owner,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=900_000_000,
                force=True,
            )

            # make sure we have enough time to finish pool deregistration in one epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
            )

            src_dereg_balance = cluster.g_query.get_address_balance(pool_owner.payment.address)
            stake_acount_balance = cluster.g_query.get_stake_addr_info(
                pool_owner.stake.address
            ).reward_account_balance
            reward_acount_balance = cluster.g_query.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance

            node_cold = pool_rec["cold_key_pair"]
            pool_id = cluster.g_stake_pool.get_stake_pool_id(node_cold.vkey_file)

            # deregister stake pool
            depoch = cluster.g_query.get_epoch() + 1
            __, tx_raw_output = cluster.g_stake_pool.deregister_stake_pool(
                pool_owners=[pool_owner],
                cold_key_pair=node_cold,
                epoch=depoch,
                pool_name=pool_name,
                tx_name=temp_template,
            )
            assert (
                clusterlib_utils.get_pool_state(cluster_obj=cluster, pool_id=pool_id).retiring
                == depoch
            )

            # check that the pool was deregistered
            cluster.wait_for_new_epoch()
            assert not clusterlib_utils.get_pool_state(
                cluster_obj=cluster, pool_id=pool_id
            ).pool_params, f"The pool {pool_id} was not deregistered"

            # check command kes-period-info case: de-register pool
            kes_period_info = cluster.g_query.get_kes_period_info(pool_opcert_file)
            kes_period_info_errors_list.extend(
                kes.check_kes_period_info_result(
                    cluster_obj=cluster,
                    kes_output=kes_period_info,
                    expected_scenario=kes.KesScenarios.ALL_VALID,
                    check_id="1",
                    pool_num=pool_num,
                )
            )

            # check that the balance for source address was correctly updated
            assert src_dereg_balance - tx_raw_output.fee == cluster.g_query.get_address_balance(
                pool_owner.payment.address
            )

            # check that the pool deposit was NOT returned to reward or stake address
            assert (
                cluster.g_query.get_stake_addr_info(pool_owner.stake.address).reward_account_balance
                == stake_acount_balance
            )
            assert (
                cluster.g_query.get_stake_addr_info(
                    pool_reward.stake.address
                ).reward_account_balance
                == reward_acount_balance
            )

            # Return the pool to the original state - reregister the pool, register
            # the reward address, delegate the stake address to the pool.

            src_updated_balance = cluster.g_query.get_address_balance(pool_reward.payment.address)

            # reregister the pool by resubmitting the pool registration certificate,
            # delegate stake address to pool again, reregister reward address
            tx_files = clusterlib.TxFiles(
                certificate_files=[
                    pool_rec["reward_addr_registration_cert"],
                    pool_rec["pool_registration_cert"],
                    pool_rec["stake_addr_delegation_cert"],
                ],
                signing_key_files=[
                    pool_rec["payment"].skey_file,
                    pool_rec["stake"].skey_file,
                    pool_rec["reward"].skey_file,
                    node_cold.skey_file,
                ],
            )
            tx_raw_output = cluster.g_transaction.send_tx(
                src_address=pool_reward.payment.address,
                tx_name=f"{temp_template}_rereg_pool",
                tx_files=tx_files,
            )

            # check command kes-period-info case: re-register pool, check without
            # waiting to take effect
            kes_period_info = cluster.g_query.get_kes_period_info(pool_opcert_file)
            kes_period_info_errors_list.extend(
                kes.check_kes_period_info_result(
                    cluster_obj=cluster,
                    kes_output=kes_period_info,
                    expected_scenario=kes.KesScenarios.ALL_VALID,
                    check_id="2",
                    pool_num=pool_num,
                )
            )

            # check that the balance for source address was correctly updated and that the
            # pool deposit was needed
            assert (
                cluster.g_query.get_address_balance(pool_reward.payment.address)
                == src_updated_balance
                - tx_raw_output.fee
                - cluster.g_query.get_pool_deposit()
                - cluster.g_query.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            LOGGER.info("Waiting up to 5 epochs for stake pool to be reregistered.")
            for __ in range(5):
                cluster.wait_for_new_epoch(padding_seconds=10)
                if pool_id in cluster.g_query.get_stake_distribution():
                    break
            else:
                raise AssertionError(f"Stake pool `{pool_id}` not registered even after 5 epochs.")

            # check command kes-period-info case: re-register pool
            kes_period_info = cluster.g_query.get_kes_period_info(pool_opcert_file)
            kes_period_info_errors_list.extend(
                kes.check_kes_period_info_result(
                    cluster_obj=cluster,
                    kes_output=kes_period_info,
                    expected_scenario=kes.KesScenarios.ALL_VALID,
                    check_id="3",
                    pool_num=pool_num,
                )
            )

            # wait before checking delegation and rewards
            cluster.wait_for_new_epoch(3, padding_seconds=30)

            # check that the stake address was delegated
            stake_addr_info = cluster.g_query.get_stake_addr_info(pool_owner.stake.address)
            assert (
                stake_addr_info.delegation
            ), f"Stake address was not delegated yet: {stake_addr_info}"

            assert pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

            # check that pool owner is receiving rewards
            assert cluster.g_query.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance, "New reward was not received by pool reward address"

        # check that pledge is still met after the owner address was used to pay for Txs
        pool_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=pool_name, pool_id=pool_id
        )
        owner_payment_balance = cluster.g_query.get_address_balance(pool_owner.payment.address)
        assert (
            owner_payment_balance >= pool_data.pool_pledge
        ), f"Pledge is not met for pool '{pool_name}'!"

        err_joined = "\n".join(e for e in kes_period_info_errors_list if e)
        if err_joined:
            xfails = kes.get_xfails(errors=kes_period_info_errors_list)
            if xfails:
                pytest.xfail(" ".join(xfails))
            else:
                raise AssertionError(f"Failed checks on `kes-period-info` command:\n{err_joined}.")
