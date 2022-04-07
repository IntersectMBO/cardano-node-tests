"""Tests of effect of pool saturation on rewards and blocks production."""
import logging
import pickle
from typing import Dict
from typing import List
from typing import NamedTuple

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


class RewardRecord(NamedTuple):
    epoch_no: int
    reward_total: int
    reward_per_epoch: int
    stake_total: int


class PoolRecord(NamedTuple):
    # pylint: disable=invalid-name
    name: str
    id: str
    id_dec: str
    reward_addr: clusterlib.PoolUser
    delegation_out: delegation.DelegationOut
    user_rewards: List[RewardRecord]
    owner_rewards: List[RewardRecord]
    blocks_minted: Dict[int, int]
    saturation_amounts: Dict[int, int]


@pytest.fixture
def cluster_lock_pools(cluster_manager: cluster_management.ClusterManager) -> clusterlib.ClusterLib:
    return cluster_manager.get(
        lock_resources=[
            cluster_management.Resources.POOL1,
            cluster_management.Resources.POOL2,
            cluster_management.Resources.POOL3,
        ]
    )


def _get_saturation_threshold(
    cluster_obj: clusterlib.ClusterLib, ledger_state: dict, pool_id: str
) -> int:
    """Calculate how much Lovelace is needed to reach saturation threshold."""
    account_state = ledger_state["stateBefore"]["esAccountState"]
    active_supply = (
        cluster_obj.genesis["maxLovelaceSupply"]
        - account_state["reserves"]
        - account_state["treasury"]
    )
    k_param = cluster_obj.get_protocol_params()["stakePoolTargetNum"]
    saturation_amount = int(active_supply / k_param)

    pool_stake = int(cluster_obj.get_stake_snapshot(pool_id)["poolStakeMark"])
    saturation_threshold = saturation_amount - pool_stake
    return saturation_threshold


def _get_reward_per_block(pool_record: PoolRecord, owner_rewards: bool = False) -> Dict[int, float]:
    """For each epoch calculate reward per block per staked Lovelace."""
    results: Dict[int, float] = {}

    rew_db = pool_record.user_rewards
    if owner_rewards:
        rew_db = pool_record.owner_rewards

    if not rew_db:
        return results

    first_ep = rew_db[0].epoch_no

    for idx, rew_received in enumerate(rew_db):
        if not rew_received.reward_per_epoch:
            continue

        for_epoch = rew_received.epoch_no - 3
        if for_epoch < first_ep:
            continue

        rew_for = rew_db[idx - 3]
        assert for_epoch == rew_for.epoch_no
        results[for_epoch] = (
            rew_received.reward_per_epoch
            / pool_record.blocks_minted[for_epoch]
            / rew_for.stake_total
        )

    return results


def _withdraw_rewards(
    *pool_users: clusterlib.PoolUser,
    cluster_obj: clusterlib.ClusterLib,
    tx_name: str,
) -> clusterlib.TxRawOutput:
    """Withdraw rewards from multiple stake addresses to corresponding payment addresses."""
    src_addr = pool_users[0].payment

    tx_files_withdrawal = clusterlib.TxFiles(
        signing_key_files=[src_addr.skey_file, *[p.stake.skey_file for p in pool_users]],
    )

    tx_raw_withdrawal_output = cluster_obj.send_tx(
        src_address=src_addr.address,
        tx_name=f"{tx_name}_reward_withdrawals",
        tx_files=tx_files_withdrawal,
        withdrawals=[clusterlib.TxOut(address=p.stake.address, amount=-1) for p in pool_users],
    )

    return tx_raw_withdrawal_output


@pytest.mark.order(5)
@pytest.mark.long
class TestPoolSaturation:
    @allure.link(helpers.get_vcs_link())
    def test_oversaturated(  # noqa: C901
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pools: clusterlib.ClusterLib,
    ):
        """Check diminished rewards when stake pool is oversaturated.

        The stake pool continues to operate normally and those who delegate to that pool receive
        rewards, but the rewards are proportionally lower than those received from stake pool
        that is not oversaturated.

        * register and delegate stake address in "init epoch", for all available pools
        * in "init epoch" + 2, saturate all available pools (block distribution remains balanced
          among pools)
        * in "init epoch" + 3, oversaturate one pool
        * in "init epoch" + 5, for all available pools, withdraw rewards and transfer funds
          from delegated addresses so pools are no longer (over)saturated
        * while doing the steps above, collect rewards data for 9 epochs
        * compare proportionality of rewards in epochs where pools were non-saturated,
          saturated and oversaturated
        """
        # pylint: disable=too-many-statements,too-many-locals,too-many-branches
        epoch_saturate = 2
        epoch_oversaturate = 4
        epoch_withdrawal = 6

        cluster = cluster_lock_pools
        temp_template = common.get_test_id(cluster)
        initial_balance = 1_000_000_000

        faucet_rec = cluster_manager.cache.addrs_data["byron000"]
        pool_records: Dict[int, PoolRecord] = {}

        # make sure we have enough time to finish the delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(cluster_obj=cluster, start=5, stop=-40)
        init_epoch = cluster.get_epoch()

        # submit registration certificates and delegate to pools
        for idx, res in enumerate(
            [
                cluster_management.Resources.POOL1,
                cluster_management.Resources.POOL2,
                cluster_management.Resources.POOL3,
            ],
            start=1,
        ):
            pool_addrs_data = cluster_manager.cache.addrs_data[res]
            reward_addr = clusterlib.PoolUser(
                payment=pool_addrs_data["payment"], stake=pool_addrs_data["reward"]
            )
            pool_id = delegation.get_pool_id(
                cluster_obj=cluster,
                addrs_data=cluster_manager.cache.addrs_data,
                pool_name=res,
            )
            pool_id_dec = helpers.decode_bech32(bech32=pool_id)

            delegation_out = delegation.delegate_stake_addr(
                cluster_obj=cluster,
                addrs_data=cluster_manager.cache.addrs_data,
                temp_template=f"{temp_template}_pool{idx}",
                pool_id=pool_id,
                amount=initial_balance,
            )

            pool_records[idx] = PoolRecord(
                name=res,
                id=pool_id,
                id_dec=pool_id_dec,
                reward_addr=reward_addr,
                delegation_out=delegation_out,
                user_rewards=[],
                owner_rewards=[],
                blocks_minted={},
                saturation_amounts={},
            )

        # record initial reward balance for each pool
        for pool_rec in pool_records.values():
            user_payment_balance = cluster.get_address_balance(
                pool_rec.delegation_out.pool_user.payment.address
            )
            owner_payment_balance = cluster.get_address_balance(
                pool_rec.reward_addr.payment.address
            )
            pool_rec.user_rewards.append(
                RewardRecord(
                    epoch_no=init_epoch,
                    reward_total=0,
                    reward_per_epoch=0,
                    stake_total=user_payment_balance,
                )
            )
            pool_rec.owner_rewards.append(
                RewardRecord(
                    epoch_no=init_epoch,
                    reward_total=cluster.get_stake_addr_info(
                        pool_rec.reward_addr.stake.address
                    ).reward_account_balance,
                    reward_per_epoch=0,
                    stake_total=owner_payment_balance,
                )
            )

        assert (
            cluster.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        LOGGER.info("Checking rewards for 10 epochs.")
        for __ in range(10):
            # wait for new epoch
            if cluster.get_epoch() == pool_records[2].owner_rewards[-1].epoch_no:
                cluster.wait_for_new_epoch()

            # sleep till the end of epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=-50, stop=-40, force_epoch=True
            )
            this_epoch = cluster.get_epoch()

            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster,
                state_name=f"{temp_template}_{this_epoch}",
                ledger_state=ledger_state,
            )

            for pool_rec in pool_records.values():
                # reward balance in previous epoch
                prev_user_reward = pool_rec.user_rewards[-1].reward_total
                prev_owner_reward = pool_rec.owner_rewards[-1].reward_total

                pool_rec.blocks_minted[this_epoch - 1] = (
                    ledger_state["blocksBefore"].get(pool_rec.id_dec) or 0
                )

                # current reward balance
                user_reward = cluster.get_stake_addr_info(
                    pool_rec.delegation_out.pool_user.stake.address
                ).reward_account_balance
                owner_reward = cluster.get_stake_addr_info(
                    pool_rec.reward_addr.stake.address
                ).reward_account_balance

                # total reward amounts received this epoch
                owner_reward_epoch = owner_reward - prev_owner_reward
                # We cannot compare with previous rewards in epochs where
                # `this_epoch >= init_epoch + epoch_withdrawal`.
                # There's a withdrawal of rewards at the end of these epochs.
                if this_epoch > init_epoch + epoch_withdrawal:
                    user_reward_epoch = user_reward
                else:
                    user_reward_epoch = user_reward - prev_user_reward

                # store collected rewards info
                user_payment_balance = cluster.get_address_balance(
                    pool_rec.delegation_out.pool_user.payment.address
                )
                owner_payment_balance = cluster.get_address_balance(
                    pool_rec.reward_addr.payment.address
                )
                pool_rec.user_rewards.append(
                    RewardRecord(
                        epoch_no=this_epoch,
                        reward_total=user_reward,
                        reward_per_epoch=user_reward_epoch,
                        stake_total=user_payment_balance + user_reward,
                    )
                )
                pool_rec.owner_rewards.append(
                    RewardRecord(
                        epoch_no=this_epoch,
                        reward_total=owner_reward,
                        reward_per_epoch=owner_reward_epoch,
                        stake_total=owner_payment_balance,
                    )
                )

                pool_rec.saturation_amounts[this_epoch] = _get_saturation_threshold(
                    cluster_obj=cluster, ledger_state=ledger_state, pool_id=pool_rec.id
                )

            # fund the delegated addresses - saturate all pools
            if this_epoch == init_epoch + epoch_saturate:
                clusterlib_utils.fund_from_faucet(
                    *[p.delegation_out.pool_user.payment for p in pool_records.values()],
                    cluster_obj=cluster,
                    faucet_data=faucet_rec,
                    amount=[
                        p.saturation_amounts[this_epoch] - 100_000_000_000
                        for p in pool_records.values()
                    ],
                    tx_name=f"{temp_template}_saturate_pools_ep{this_epoch}",
                    force=True,
                )

            with cluster_manager.restart_on_failure():
                # fund the address delegated to "pool2" - oversaturate the pool
                if this_epoch == init_epoch + epoch_oversaturate:
                    assert (
                        pool_records[2].saturation_amounts[this_epoch] > 0
                    ), "Pool is already saturated"
                    clusterlib_utils.fund_from_faucet(
                        pool_records[2].delegation_out.pool_user.payment,
                        cluster_obj=cluster,
                        faucet_data=faucet_rec,
                        amount=pool_records[2].saturation_amounts[this_epoch] + 70_000_000_000_000,
                        tx_name=f"{temp_template}_oversaturate_pool2",
                        force=True,
                    )

                # wait 4 epochs for first rewards
                if this_epoch >= init_epoch + 4:
                    assert (
                        owner_reward > prev_owner_reward
                    ), "New reward was not received by pool owner"

                # transfer funds back to faucet so the pools are no longer (over)saturated
                # and staked amount is +- same as the `initial_balance`
                if this_epoch >= init_epoch + epoch_withdrawal:
                    _withdraw_rewards(
                        *[p.delegation_out.pool_user for p in pool_records.values()],
                        cluster_obj=cluster,
                        tx_name=f"{temp_template}_ep{this_epoch}",
                    )

                    return_to_addrs = []
                    return_amounts = []
                    for idx, pool_rec in pool_records.items():
                        deleg_payment_balance = cluster.get_address_balance(
                            pool_rec.delegation_out.pool_user.payment.address
                        )
                        if deleg_payment_balance > initial_balance + 10_000_000:
                            return_to_addrs.append(pool_rec.delegation_out.pool_user.payment)
                            return_amounts.append(deleg_payment_balance - initial_balance)

                    clusterlib_utils.return_funds_to_faucet(
                        *return_to_addrs,
                        cluster_obj=cluster,
                        faucet_addr=faucet_rec["payment"].address,
                        amount=return_amounts,
                        tx_name=f"{temp_template}_ep{this_epoch}",
                    )

                    for return_addr in return_to_addrs:
                        deleg_payment_balance = cluster.get_address_balance(return_addr.address)
                        assert (
                            deleg_payment_balance <= initial_balance
                        ), "Unexpected funds in payment address '{return_addr}'"

                assert (
                    cluster.get_epoch() == this_epoch
                ), "Failed to finish actions in single epoch, it would affect other checks"

        pool1_user_rewards_per_block = _get_reward_per_block(pool_records[1])
        pool2_user_rewards_per_block = _get_reward_per_block(pool_records[2])
        pool3_user_rewards_per_block = _get_reward_per_block(pool_records[3])

        pool1_owner_rewards_per_block = _get_reward_per_block(pool_records[1], owner_rewards=True)
        pool2_owner_rewards_per_block = _get_reward_per_block(pool_records[2], owner_rewards=True)
        pool3_owner_rewards_per_block = _get_reward_per_block(pool_records[3], owner_rewards=True)

        oversaturated_epoch = max(e for e, r in pool_records[2].saturation_amounts.items() if r < 0)
        saturated_epoch = oversaturated_epoch - 2
        nonsaturated_epoch = oversaturated_epoch - 4

        try:
            # check that rewards per block per stake for "pool2" in the epoch where the pool is
            # oversaturated is lower than in epochs where pools are not oversaturated
            assert (
                pool1_user_rewards_per_block[nonsaturated_epoch]
                > pool2_user_rewards_per_block[oversaturated_epoch]
            )
            assert (
                pool2_user_rewards_per_block[nonsaturated_epoch]
                > pool2_user_rewards_per_block[oversaturated_epoch]
            )
            assert (
                pool3_user_rewards_per_block[nonsaturated_epoch]
                > pool2_user_rewards_per_block[oversaturated_epoch]
            )

            assert (
                pool1_user_rewards_per_block[saturated_epoch]
                > pool2_user_rewards_per_block[oversaturated_epoch]
            )
            assert (
                pool2_user_rewards_per_block[saturated_epoch]
                > pool2_user_rewards_per_block[oversaturated_epoch]
            )
            assert (
                pool3_user_rewards_per_block[saturated_epoch]
                > pool2_user_rewards_per_block[oversaturated_epoch]
            )

            # check that oversaturated pool doesn't lead to increased rewards for pool owner
            # when compared to saturated pool, i.e. total pool margin amount is not increased
            pool1_rew_fraction_sat = pool1_owner_rewards_per_block[saturated_epoch]
            pool2_rew_fraction_sat = pool2_owner_rewards_per_block[saturated_epoch]
            pool3_rew_fraction_sat = pool3_owner_rewards_per_block[saturated_epoch]

            pool2_rew_fraction_over = pool2_owner_rewards_per_block[oversaturated_epoch]

            assert pool2_rew_fraction_sat > pool2_rew_fraction_over or helpers.is_in_interval(
                pool2_rew_fraction_sat,
                pool2_rew_fraction_over,
                frac=0.2,
            )
            assert pool1_rew_fraction_sat > pool2_rew_fraction_over or helpers.is_in_interval(
                pool1_rew_fraction_sat,
                pool2_rew_fraction_over,
                frac=0.2,
            )
            assert pool3_rew_fraction_sat > pool2_rew_fraction_over or helpers.is_in_interval(
                pool3_rew_fraction_sat,
                pool2_rew_fraction_over,
                frac=0.2,
            )

            # Compare rewards in last (non-saturated) epoch to rewards in next-to-last
            # (saturated / over-saturated) epoch.
            # This way check that staked amount for each pool was restored to `initial_balance`
            # and that rewards correspond to the restored amounts.
            for pool_rec in pool_records.values():
                assert (
                    pool_rec.user_rewards[-1].reward_per_epoch * 100
                    < pool_rec.user_rewards[-2].reward_per_epoch
                )
        except AssertionError:
            # save debugging data in case of test failure
            with open(f"{temp_template}_pool_records.pickle", "wb") as out_data:
                pickle.dump(pool_records, out_data)
            raise
