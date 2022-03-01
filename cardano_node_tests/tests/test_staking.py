"""Tests for staking, rewards, blocks production on real block-producing pools."""
import logging
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Tuple
from typing import Union

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import kes
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


class RewardRecord(NamedTuple):
    epoch_no: int
    reward_total: int
    reward_per_epoch: int
    member_pool_id: str = ""
    leader_pool_ids: Union[List[str], tuple] = ()
    stake_total: int = 0


@pytest.fixture
def cluster_and_pool(
    cluster_manager: cluster_management.ClusterManager,
) -> Tuple[clusterlib.ClusterLib, str]:
    return delegation.cluster_and_pool(cluster_manager=cluster_manager)


@pytest.fixture
def cluster_use_pool1(cluster_manager: cluster_management.ClusterManager) -> clusterlib.ClusterLib:
    return cluster_manager.get(use_resources=[cluster_management.Resources.POOL1])


@pytest.fixture
def cluster_use_pool1_2(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    return cluster_manager.get(
        use_resources=[cluster_management.Resources.POOL1, cluster_management.Resources.POOL2]
    )


@pytest.fixture
def cluster_lock_pool2(cluster_manager: cluster_management.ClusterManager) -> clusterlib.ClusterLib:
    return cluster_manager.get(lock_resources=[cluster_management.Resources.POOL2])


@pytest.fixture
def cluster_lock_pool1_2(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    return cluster_manager.get(
        lock_resources=[cluster_management.Resources.POOL1, cluster_management.Resources.POOL2]
    )


@pytest.fixture
def cluster_lock_pool2_pots(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    return cluster_manager.get(
        lock_resources=[
            cluster_management.Resources.POOL2,
            cluster_management.Resources.RESERVES,
            cluster_management.Resources.TREASURY,
        ]
    )


def withdraw_reward_w_build(
    cluster_obj: clusterlib.ClusterLib,
    stake_addr_record: clusterlib.AddressRecord,
    dst_addr_record: clusterlib.AddressRecord,
    tx_name: str,
    verify: bool = True,
    destination_dir: clusterlib.FileType = ".",
) -> clusterlib.TxRawOutput:
    """Withdraw reward to payment address.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        stake_addr_record: An `AddressRecord` tuple for the stake address with reward.
        dst_addr_record: An `AddressRecord` tuple for the destination payment address.
        tx_name: A name of the transaction.
        verify: A bool indicating whether to verify that the reward was transferred correctly.
        destination_dir: A path to directory for storing artifacts (optional).
    """
    dst_address = dst_addr_record.address
    src_init_balance = cluster_obj.get_address_balance(dst_address)

    tx_files_withdrawal = clusterlib.TxFiles(
        signing_key_files=[dst_addr_record.skey_file, stake_addr_record.skey_file],
    )

    tx_raw_withdrawal_output = cluster_obj.build_tx(
        src_address=dst_address,
        tx_name=f"{tx_name}_reward_withdrawal",
        tx_files=tx_files_withdrawal,
        withdrawals=[clusterlib.TxOut(address=stake_addr_record.address, amount=-1)],
        fee_buffer=2000_000,
        witness_override=len(tx_files_withdrawal.signing_key_files),
        destination_dir=destination_dir,
    )
    tx_signed = cluster_obj.sign_tx(
        tx_body_file=tx_raw_withdrawal_output.out_file,
        signing_key_files=tx_files_withdrawal.signing_key_files,
        tx_name=f"{tx_name}_reward_withdrawal",
    )
    cluster_obj.submit_tx(tx_file=tx_signed, txins=tx_raw_withdrawal_output.txins)

    if not verify:
        return tx_raw_withdrawal_output

    # check that reward is 0
    if cluster_obj.get_stake_addr_info(stake_addr_record.address).reward_account_balance != 0:
        raise AssertionError("Not all rewards were transferred.")

    # check that rewards were transferred
    src_reward_balance = cluster_obj.get_address_balance(dst_address)
    if (
        src_reward_balance
        != src_init_balance
        - tx_raw_withdrawal_output.fee
        + tx_raw_withdrawal_output.withdrawals[0].amount  # type: ignore
    ):
        raise AssertionError(f"Incorrect balance for destination address `{dst_address}`.")

    return tx_raw_withdrawal_output


def _add_spendable(rewards: List[dbsync_utils.RewardEpochRecord], max_epoch: int) -> Dict[int, int]:
    recs: Dict[int, int] = {}
    for r in rewards:
        epoch = r.spendable_epoch
        if max_epoch and epoch > max_epoch:
            continue
        amount = r.amount
        if epoch in recs:
            recs[epoch] += amount
        else:
            recs[epoch] = amount

    return recs


def _check_member_pool_ids(
    rewards_by_idx: Dict[int, RewardRecord], reward_db_record: dbsync_utils.RewardRecord
) -> None:
    """Check that in each epoch member rewards were received from the expected pool."""
    epoch_to = rewards_by_idx[max(rewards_by_idx)].epoch_no

    # reward records obtained from TX
    pool_ids_dict = {}
    for r_tx in rewards_by_idx.values():
        # rewards are received from pool to which the address was delegated 4 epochs ago
        pool_epoch = r_tx.epoch_no - 4
        rec_for_epoch_tx = rewards_by_idx.get(pool_epoch)
        if (
            r_tx.reward_total
            and r_tx.member_pool_id
            and rec_for_epoch_tx
            and rec_for_epoch_tx.member_pool_id
        ):
            pool_ids_dict[r_tx.epoch_no] = rec_for_epoch_tx.member_pool_id

    if not pool_ids_dict:
        return

    pool_first_epoch = min(pool_ids_dict)

    # reward records obtained from db-sync
    db_pool_ids_dict = {}
    for r_db in reward_db_record.rewards:
        if (
            r_db.pool_id
            and r_db.type == "member"
            and pool_first_epoch <= r_db.spendable_epoch <= epoch_to
        ):
            db_pool_ids_dict[r_db.spendable_epoch] = r_db.pool_id

    if db_pool_ids_dict:
        assert pool_ids_dict == db_pool_ids_dict


def _check_leader_pool_ids(
    rewards_by_idx: Dict[int, RewardRecord], reward_db_record: dbsync_utils.RewardRecord
) -> None:
    """Check that in each epoch leader rewards were received from the expected pool."""
    epoch_to = rewards_by_idx[max(rewards_by_idx)].epoch_no

    # reward records obtained from TX
    pool_ids_dict = {}
    for r_tx in rewards_by_idx.values():
        # rewards are received on address that was set as pool reward address 4 epochs ago
        pool_epoch = r_tx.epoch_no - 4
        rec_for_epoch_tx = rewards_by_idx.get(pool_epoch)
        if (
            r_tx.reward_total
            and r_tx.leader_pool_ids
            and rec_for_epoch_tx
            and rec_for_epoch_tx.leader_pool_ids
        ):
            pool_ids_dict[r_tx.epoch_no] = set(rec_for_epoch_tx.leader_pool_ids)

    if not pool_ids_dict:
        return

    pool_first_epoch = min(pool_ids_dict)

    # reward records obtained from db-sync
    db_pool_ids_dict: dict = {}
    for r_db in reward_db_record.rewards:
        if (
            r_db.pool_id
            and r_db.type == "leader"
            and pool_first_epoch <= r_db.spendable_epoch <= epoch_to
        ):
            rec_for_epoch_db = db_pool_ids_dict.get(r_db.spendable_epoch)
            if rec_for_epoch_db is None:
                db_pool_ids_dict[r_db.spendable_epoch] = {r_db.pool_id}
                continue
            rec_for_epoch_db.add(r_db.pool_id)

    if db_pool_ids_dict:
        assert pool_ids_dict == db_pool_ids_dict


def _dbsync_check_rewards(
    stake_address: str,
    rewards: List[RewardRecord],
) -> dbsync_utils.RewardRecord:
    """Check rewards in db-sync."""
    epoch_from = rewards[1].epoch_no
    epoch_to = rewards[-1].epoch_no

    # when dealing with spendable epochs, last "spendable epoch" is last "earned epoch" + 2
    reward_db_record = dbsync_utils.check_address_reward(
        address=stake_address, epoch_from=epoch_from, epoch_to=epoch_to + 2
    )
    assert reward_db_record

    rewards_by_idx = {r.epoch_no: r for r in rewards}

    # check that in each epoch rewards were received from the expected pool
    _check_member_pool_ids(rewards_by_idx=rewards_by_idx, reward_db_record=reward_db_record)
    _check_leader_pool_ids(rewards_by_idx=rewards_by_idx, reward_db_record=reward_db_record)

    # compare reward amounts with db-sync
    user_rewards_dict = {r.epoch_no: r.reward_per_epoch for r in rewards if r.reward_per_epoch}
    user_db_rewards_dict = _add_spendable(rewards=reward_db_record.rewards, max_epoch=epoch_to)
    assert user_rewards_dict == user_db_rewards_dict

    return reward_db_record


def _get_key_hashes(rec: dict) -> List[str]:
    """Get key hashes in ledger state snapshot record."""
    return [r[0]["key hash"] for r in rec]


def _get_val_for_key_hash(key_hash: str, rec: list) -> Any:
    """Get value for key hash in ledger state snapshot record."""
    for r in rec:
        if r[0]["key hash"] == key_hash:
            return r[1]
    return None


def _get_reward_amount_for_key_hash(key_hash: str, rec: list) -> int:
    """Get reward amount for key hash in ledger state snapshot record."""
    for r in rec:
        if r[0]["key hash"] != key_hash:
            continue
        rew_amount = 0
        for sr in r[1]:
            rew_amount += sr["rewardAmount"]
        return rew_amount
    return 0


def _get_reward_type_for_key_hash(key_hash: str, rec: list) -> List[str]:
    """Get reward types for key hash in ledger state snapshot record."""
    rew_types = []
    for r in rec:
        if r[0]["key hash"] != key_hash:
            continue
        for sr in r[1]:
            rew_types.append(sr["rewardType"])
        return rew_types
    return rew_types


@pytest.mark.order(6)
@pytest.mark.long
class TestRewards:
    """Tests for checking expected rewards."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.skipif(
        cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL,
        reason="supposed to run on testnet",
    )
    def test_reward_simple(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
    ):
        """Check that the stake address and pool owner are receiving rewards.

        * delegate to pool
        * wait for rewards for pool owner and pool users for up to 4 epochs
        * withdraw rewards to payment address
        """
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)
        # check pool rewards only when own pool is available
        check_pool_rewards = (
            cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.TESTNET
        )

        # make sure we have enough time to finish the registration/delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-300, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        if check_pool_rewards:
            pool_rec = cluster_manager.cache.addrs_data["node-pool1"]
            pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
            init_owner_rewards = cluster.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance

        # submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        assert (
            cluster.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        LOGGER.info("Waiting up to 4 full epochs for first reward.")
        for i in range(5):
            if i > 0:
                cluster.wait_for_new_epoch(padding_seconds=10)
            if cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance:
                break
        else:
            pytest.skip(f"User of pool '{pool_id}' hasn't received any rewards, cannot continue.")

        if check_pool_rewards:
            assert (
                cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance
                > init_owner_rewards
            ), f"Owner of pool '{pool_id}' hasn't received any rewards"

        # withdraw rewards to payment address, make sure we have enough time to finish
        # the withdrawal in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-300, force_epoch=False
        )
        cluster.withdraw_reward(
            stake_addr_record=delegation_out.pool_user.stake,
            dst_addr_record=delegation_out.pool_user.payment,
            tx_name=temp_template,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_reward_amount(  # noqa: C901
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_use_pool1: clusterlib.ClusterLib,
    ):
        """Check that the stake address and pool owner are receiving rewards.

        * create two payment addresses that share single stake address
        * register and delegate the stake address to pool
        * create UTxOs with native tokens
        * collect data for pool owner and pool users for 9 epochs

           - each epoch check ledger state (expected data in `pstake*`, delegation, stake amount)
           - each epoch check received reward with reward in ledger state

        * withdraw rewards to payment address
        * burn native tokens
        * (optional) check records in db-sync
        """
        # pylint: disable=too-many-statements,too-many-locals,too-many-branches
        __: Any  # mypy workaround
        pool_name = "node-pool1"
        cluster = cluster_use_pool1

        temp_template = common.get_test_id(cluster)
        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
        pool_reward_addr_dec = helpers.decode_bech32(pool_reward.stake.address)[2:]
        pool_stake_addr_dec = helpers.decode_bech32(pool_owner.stake.address)[2:]

        token_rand = clusterlib.get_rand_str(5)
        token_amount = 1_000_000

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

        # make sure we have enough time to finish the registration/delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-40, force_epoch=False
        )

        init_epoch = cluster.get_epoch()

        # submit registration certificate and delegate to pool
        pool_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_user=pool_user,
            pool_id=pool_id,
        )

        native_tokens: List[clusterlib_utils.TokenRecord] = []
        if VERSIONS.transaction_era >= VERSIONS.MARY:
            # create native tokens UTxOs for pool user
            native_tokens = clusterlib_utils.new_tokens(
                *[f"couttscoin{token_rand}{i}".encode("utf-8").hex() for i in range(5)],
                cluster_obj=cluster,
                temp_template=f"{temp_template}_{token_rand}",
                token_mint_addr=delegation_out.pool_user.payment,
                issuer_addr=delegation_out.pool_user.payment,
                amount=token_amount,
            )

        # make sure we managed to finish registration in the expected epoch
        assert (
            cluster.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        user_stake_addr_dec = helpers.decode_bech32(delegation_out.pool_user.stake.address)[2:]

        # balance for both payment addresses associated with the single stake address
        user_payment_balance = cluster.get_address_balance(
            payment_addr_recs[0].address
        ) + cluster.get_address_balance(payment_addr_recs[1].address)

        user_rewards = [
            RewardRecord(
                epoch_no=init_epoch,
                reward_total=0,
                reward_per_epoch=0,
                member_pool_id=pool_id,
                stake_total=user_payment_balance,
            )
        ]
        owner_rewards = [
            RewardRecord(
                epoch_no=init_epoch,
                reward_total=cluster.get_stake_addr_info(
                    pool_reward.stake.address
                ).reward_account_balance,
                reward_per_epoch=0,
                leader_pool_ids=[pool_id],
            )
        ]

        # ledger state db
        rs_records: dict = {init_epoch: None}

        def _check_ledger_state(
            this_epoch: int,
        ) -> None:
            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster,
                state_name=f"{temp_template}_{this_epoch}",
                ledger_state=ledger_state,
            )
            es_snapshot: dict = ledger_state["stateBefore"]["esSnapshots"]
            rs_record: list = ledger_state["possibleRewardUpdate"]["rs"]
            rs_records[this_epoch] = rs_record

            # Make sure reward amount corresponds with ledger state.
            # Reward is received on epoch boundary, so check reward with record for previous epoch.
            prev_rs_record = rs_records.get(this_epoch - 1)
            user_reward_epoch = user_rewards[-1].reward_per_epoch
            if user_reward_epoch and prev_rs_record:
                assert user_reward_epoch == _get_reward_amount_for_key_hash(
                    user_stake_addr_dec, prev_rs_record
                )
            owner_reward_epoch = owner_rewards[-1].reward_per_epoch
            if owner_reward_epoch and prev_rs_record:
                assert owner_reward_epoch == _get_reward_amount_for_key_hash(
                    pool_reward_addr_dec, prev_rs_record
                )

            pstake_mark = _get_key_hashes(es_snapshot["pstakeMark"]["stake"])
            pstake_set = _get_key_hashes(es_snapshot["pstakeSet"]["stake"])
            pstake_go = _get_key_hashes(es_snapshot["pstakeGo"]["stake"])

            if this_epoch == init_epoch + 1:
                assert pool_stake_addr_dec in pstake_mark
                assert pool_stake_addr_dec in pstake_set

                assert user_stake_addr_dec in pstake_mark
                assert user_stake_addr_dec not in pstake_set
                assert user_stake_addr_dec not in pstake_go

                # make sure ledger state and actual stake correspond
                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["pstakeMark"]["stake"])
                    == user_rewards[-1].stake_total
                )

            if this_epoch == init_epoch + 2:
                assert user_stake_addr_dec in pstake_mark
                assert user_stake_addr_dec in pstake_set
                assert user_stake_addr_dec not in pstake_go

                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["pstakeMark"]["stake"])
                    == user_rewards[-1].stake_total
                )
                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["pstakeSet"]["stake"])
                    == user_rewards[-2].stake_total
                )

            if this_epoch >= init_epoch + 2:
                assert pool_stake_addr_dec in pstake_mark
                assert pool_stake_addr_dec in pstake_set
                assert pool_stake_addr_dec in pstake_go

            if this_epoch >= init_epoch + 3:
                assert user_stake_addr_dec in pstake_mark
                assert user_stake_addr_dec in pstake_set
                assert user_stake_addr_dec in pstake_go

                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["pstakeMark"]["stake"])
                    == user_rewards[-1].stake_total
                )
                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["pstakeSet"]["stake"])
                    == user_rewards[-2].stake_total
                )
                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["pstakeGo"]["stake"])
                    == user_rewards[-3].stake_total
                )

        LOGGER.info("Checking rewards for 9 epochs.")
        for __ in range(9):
            # reward balance in previous epoch
            prev_user_reward = user_rewards[-1].reward_total
            (
                prev_owner_epoch,
                prev_owner_reward,
                *__,
            ) = owner_rewards[-1]

            # wait for new epoch
            if cluster.get_epoch() == prev_owner_epoch:
                cluster.wait_for_new_epoch()

            # sleep till the end of epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=-19, stop=-15, check_slot=False
            )
            this_epoch = cluster.get_epoch()

            # current reward balance
            user_reward = cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance
            owner_reward = cluster.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance

            # total reward amounts received this epoch
            user_reward_epoch = user_reward - prev_user_reward
            owner_reward_epoch = owner_reward - prev_owner_reward

            # store collected rewards info
            user_rewards.append(
                RewardRecord(
                    epoch_no=this_epoch,
                    reward_total=user_reward,
                    reward_per_epoch=user_reward_epoch,
                    member_pool_id=pool_id,
                    stake_total=user_payment_balance + user_reward,
                )
            )
            owner_rewards.append(
                RewardRecord(
                    epoch_no=this_epoch,
                    reward_total=owner_reward,
                    reward_per_epoch=owner_reward_epoch,
                    leader_pool_ids=[pool_id],
                )
            )

            # wait 4 epochs for first rewards
            if this_epoch >= init_epoch + 4:
                assert owner_reward > prev_owner_reward, "New reward was not received by pool owner"
                assert (
                    user_reward > prev_user_reward
                ), "New reward was not received by stake address"

            _check_ledger_state(this_epoch=this_epoch)

        # withdraw rewards to payment address
        if this_epoch == cluster.get_epoch():
            cluster.wait_for_new_epoch()

        withdraw_out = cluster.withdraw_reward(
            stake_addr_record=delegation_out.pool_user.stake,
            dst_addr_record=delegation_out.pool_user.payment,
            tx_name=temp_template,
        )

        if native_tokens:
            # burn native tokens
            tokens_to_burn = [t._replace(amount=-token_amount) for t in native_tokens]
            clusterlib_utils.mint_or_burn_sign(
                cluster_obj=cluster,
                new_tokens=tokens_to_burn,
                temp_template=f"{temp_template}_burn",
            )

        # check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=withdraw_out)

        tx_db_record = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out.tx_raw_output
        )
        if tx_db_record:
            delegation.db_check_delegation(
                pool_user=delegation_out.pool_user,
                db_record=tx_db_record,
                deleg_epoch=init_epoch,
                pool_id=delegation_out.pool_id,
            )

            _dbsync_check_rewards(
                stake_address=delegation_out.pool_user.stake.address,
                rewards=user_rewards,
            )

            _dbsync_check_rewards(
                stake_address=pool_reward.stake.address,
                rewards=owner_rewards,
            )

            # check in db-sync that both payment addresses share single stake address
            assert (
                dbsync_utils.get_utxo(address=payment_addr_recs[0].address).stake_address
                == stake_addr_rec.address
            )
            assert (
                dbsync_utils.get_utxo(address=payment_addr_recs[1].address).stake_address
                == stake_addr_rec.address
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.needs_dbsync
    def test_reward_addr_delegation(  # noqa: C901
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool2_pots: clusterlib.ClusterLib,
    ):
        """Check that the rewards address can be delegated and receive rewards.

        Tests https://github.com/input-output-hk/cardano-node/issues/1964

        The pool has a reward address that is different from pool owner's stake address.

        * delegate reward address to the pool
        * collect reward address data for 8 epochs and

           - each epoch check ledger state (expected data in `pstake*`, delegation, stake amount)
           - each epoch check received reward with reward in ledger state
           - check that reward address receives rewards for its staked amount +
             the pool owner's pledge (and pool cost)
           - send TXs with MIR certs that transfer funds from reserves and treasury
             to pool reward address and check the reward was received as expected

        * check records in db-sync

           - transaction inputs, outputs, withdrawals, etc.
           - reward amounts received each epoch
           - expected pool id
           - expected reward types
        """
        # pylint: disable=too-many-statements,too-many-locals,too-many-branches
        __: Any  # mypy workaround
        pool_name = "node-pool2"
        cluster = cluster_lock_pool2_pots
        mir_reward = 50_000_000_000

        temp_template = common.get_test_id(cluster)
        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
        reward_addr_dec = helpers.decode_bech32(pool_reward.stake.address)[2:]

        # fund pool owner's addresses so balance keeps higher than pool pledge after fees etc.
        # are deducted
        clusterlib_utils.fund_from_faucet(
            pool_owner,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=150_000_000,
            force=True,
        )

        pool_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

        # make sure we have enough time to finish delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-40, force_epoch=False
        )

        init_epoch = cluster.get_epoch()

        # rewards each epoch
        reward_records: List[RewardRecord] = []

        # ledger state db
        rs_records: dict = {init_epoch: None}

        def _check_ledger_state(
            this_epoch: int,
        ) -> None:
            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster,
                state_name=f"{temp_template}_{this_epoch}",
                ledger_state=ledger_state,
            )
            es_snapshot: dict = ledger_state["stateBefore"]["esSnapshots"]
            rs_record: list = ledger_state["possibleRewardUpdate"]["rs"]
            rs_records[this_epoch] = rs_record

            # Make sure reward amount corresponds with ledger state.
            # Reward is received on epoch boundary, so check reward with record for previous epoch.
            prev_rs_record = rs_records.get(this_epoch - 1)
            owner_reward_epoch = reward_records[-1].reward_per_epoch
            if owner_reward_epoch and prev_rs_record:
                prev_recorded_reward = _get_reward_amount_for_key_hash(
                    reward_addr_dec, prev_rs_record
                )
                assert owner_reward_epoch in (
                    prev_recorded_reward,
                    prev_recorded_reward + mir_reward,
                )

            pstake_mark = _get_key_hashes(es_snapshot["pstakeMark"]["stake"])
            pstake_set = _get_key_hashes(es_snapshot["pstakeSet"]["stake"])
            pstake_go = _get_key_hashes(es_snapshot["pstakeGo"]["stake"])

            if this_epoch == init_epoch + 1:
                assert reward_addr_dec in pstake_mark
                assert reward_addr_dec not in pstake_set
                assert reward_addr_dec not in pstake_go

                # make sure ledger state and actual stake correspond
                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeMark"]["stake"])
                    == reward_records[-1].reward_total
                )

            if this_epoch == init_epoch + 2:
                assert reward_addr_dec in pstake_mark
                assert reward_addr_dec in pstake_set
                assert reward_addr_dec not in pstake_go

                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeMark"]["stake"])
                    == reward_records[-1].reward_total
                )
                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeSet"]["stake"])
                    == reward_records[-2].reward_total
                )

            if init_epoch + 3 <= this_epoch <= init_epoch + 5:
                assert reward_addr_dec in pstake_mark
                assert reward_addr_dec in pstake_set
                assert reward_addr_dec in pstake_go

                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeMark"]["stake"])
                    == reward_records[-1].reward_total
                )
                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeSet"]["stake"])
                    == reward_records[-2].reward_total
                )
                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeGo"]["stake"])
                    == reward_records[-3].reward_total
                )

            if this_epoch == init_epoch + 6:
                assert reward_addr_dec not in pstake_mark
                assert reward_addr_dec in pstake_set
                assert reward_addr_dec in pstake_go

                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeSet"]["stake"])
                    == reward_records[-2].reward_total
                )
                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeGo"]["stake"])
                    == reward_records[-3].reward_total
                )

            if this_epoch == init_epoch + 7:
                assert reward_addr_dec not in pstake_mark
                assert reward_addr_dec not in pstake_set
                assert reward_addr_dec in pstake_go

                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeGo"]["stake"])
                    == reward_records[-3].reward_total
                )

            if this_epoch > init_epoch + 7:
                assert reward_addr_dec not in pstake_mark
                assert reward_addr_dec not in pstake_set
                assert reward_addr_dec not in pstake_go

            # check that rewards are comming from multiple sources where expected
            # ("LeaderReward" and "MemberReward")
            if init_epoch + 3 <= this_epoch <= init_epoch + 7:
                assert ["LeaderReward", "MemberReward"] == _get_reward_type_for_key_hash(
                    reward_addr_dec, rs_record
                )
            else:
                assert ["LeaderReward"] == _get_reward_type_for_key_hash(reward_addr_dec, rs_record)

        def _mir_tx(fund_src: str) -> clusterlib.TxRawOutput:
            mir_cert = cluster.gen_mir_cert_stake_addr(
                stake_addr=pool_reward.stake.address,
                reward=mir_reward,
                tx_name=temp_template,
                use_treasury=fund_src == "treasury",
            )
            mir_tx_files = clusterlib.TxFiles(
                certificate_files=[mir_cert],
                signing_key_files=[
                    pool_owner.payment.skey_file,
                    *cluster.genesis_keys.delegate_skeys,
                ],
            )

            LOGGER.info(
                f"Submitting MIR cert for tranferring funds from {fund_src} to "
                f"'{pool_reward.stake.address}' in epoch {cluster.get_epoch()} "
                f"on cluster instance {cluster_manager.cluster_instance_num}"
            )
            mir_tx_raw_output = cluster.send_tx(
                src_address=pool_owner.payment.address,
                tx_name=f"{temp_template}_{fund_src}",
                tx_files=mir_tx_files,
            )

            return mir_tx_raw_output

        # delegate pool rewards address to pool
        node_cold = pool_rec["cold_key_pair"]
        reward_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=pool_reward.stake.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[
                reward_addr_deleg_cert_file,
            ],
            signing_key_files=[
                pool_owner.payment.skey_file,
                pool_reward.stake.skey_file,
                node_cold.skey_file,
            ],
        )
        tx_raw_deleg = cluster.send_tx(
            src_address=pool_owner.payment.address,
            tx_name=f"{temp_template}_deleg_rewards",
            tx_files=tx_files,
        )

        with cluster_manager.restart_on_failure():
            # make sure we managed to finish delegation in the expected epoch
            assert (
                cluster.get_epoch() == init_epoch
            ), "Delegation took longer than expected and would affect other checks"

            reward_records.append(
                RewardRecord(
                    epoch_no=init_epoch,
                    reward_total=cluster.get_stake_addr_info(
                        pool_reward.stake.address
                    ).reward_account_balance,
                    reward_per_epoch=0,
                    leader_pool_ids=[pool_id],
                )
            )

            LOGGER.info("Checking rewards for 8 epochs.")
            for __ in range(8):
                # reward balance in previous epoch
                (
                    prev_epoch,
                    prev_owner_reward,
                    *__,
                ) = reward_records[-1]

                # wait for new epoch
                if cluster.get_epoch() == prev_epoch:
                    cluster.wait_for_new_epoch()

                this_epoch = cluster.get_epoch()

                # current reward balance
                owner_reward = cluster.get_stake_addr_info(
                    pool_reward.stake.address
                ).reward_account_balance

                # Total reward amount received this epoch.
                # If `owner_reward < prev_owner_reward`, withdrawal took place during
                # previous epoch.
                if owner_reward > prev_owner_reward:
                    owner_reward_epoch = owner_reward - prev_owner_reward
                else:
                    owner_reward_epoch = owner_reward

                # store collected rewards info
                reward_records.append(
                    RewardRecord(
                        epoch_no=this_epoch,
                        reward_total=owner_reward,
                        reward_per_epoch=owner_reward_epoch,
                        leader_pool_ids=[pool_id],
                    )
                )

                if this_epoch == init_epoch + 2:
                    mir_tx_raw_reserves = _mir_tx("reserves")

                if this_epoch == init_epoch + 3:
                    assert owner_reward_epoch > mir_reward
                    mir_tx_raw_treasury = _mir_tx("treasury")

                if this_epoch == init_epoch + 4:
                    assert owner_reward_epoch > mir_reward

                # undelegate rewards address
                if this_epoch == init_epoch + 5:
                    # create stake address deregistration cert
                    reward_addr_dereg_cert_file = cluster.gen_stake_addr_deregistration_cert(
                        addr_name=f"{temp_template}_reward",
                        stake_vkey_file=pool_reward.stake.vkey_file,
                    )

                    # create stake address registration cert
                    reward_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
                        addr_name=f"{temp_template}_reward",
                        stake_vkey_file=pool_reward.stake.vkey_file,
                    )

                    # withdraw rewards; deregister and register stake address in single TX
                    tx_files = clusterlib.TxFiles(
                        certificate_files=[reward_addr_dereg_cert_file, reward_addr_reg_cert_file],
                        signing_key_files=[
                            pool_owner.payment.skey_file,
                            pool_reward.stake.skey_file,
                        ],
                    )
                    tx_raw_undeleg = cluster.send_tx(
                        src_address=pool_owner.payment.address,
                        tx_name=f"{temp_template}_undeleg",
                        tx_files=tx_files,
                        withdrawals=[
                            clusterlib.TxOut(address=pool_reward.stake.address, amount=-1)
                        ],
                    )

                    reward_stake_info = cluster.get_stake_addr_info(pool_reward.stake.address)
                    assert reward_stake_info.address, "Reward address is not registered"
                    assert not reward_stake_info.delegation, "Reward address is still delegated"

                # sleep till the end of epoch
                clusterlib_utils.wait_for_epoch_interval(
                    cluster_obj=cluster, start=-19, stop=-15, check_slot=False
                )

                _check_ledger_state(this_epoch=this_epoch)

        # check TX records in db-sync
        assert dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_deleg)
        assert dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_undeleg)
        assert dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=mir_tx_raw_reserves)
        assert dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=mir_tx_raw_treasury)

        # check pool records in db-sync
        pool_params: dict = cluster.get_pool_params(pool_id).pool_params
        dbsync_utils.check_pool_data(ledger_pool_data=pool_params, pool_id=pool_id)

        # check rewards in db-sync
        reward_db_record = _dbsync_check_rewards(
            stake_address=pool_reward.stake.address,
            rewards=reward_records,
        )

        # in db-sync check that there were rewards of multiple different types
        # ("leader", "member", "treasury", "reserves")
        reward_types: Dict[int, List[str]] = {}
        for rec in reward_db_record.rewards:
            stored_types = reward_types.get(rec.earned_epoch)
            if stored_types is None:
                reward_types[rec.earned_epoch] = [rec.type]
                continue
            stored_types.append(rec.type)

        for repoch, rtypes in reward_types.items():
            rtypes_set = set(rtypes)
            assert len(rtypes_set) == len(
                rtypes
            ), "Multiple rewards of the same type were received in single epoch"

            if repoch <= init_epoch + 1:
                assert rtypes_set == {"leader"}
            if repoch == init_epoch + 2:
                assert rtypes_set == {"reserves", "leader", "member"}
            if repoch == init_epoch + 3:
                assert rtypes_set == {"treasury", "leader", "member"}
            if init_epoch + 4 <= repoch <= 6:
                assert rtypes_set == {"leader", "member"}
            if repoch > init_epoch + 6:
                assert rtypes_set == {"leader"}

    @allure.link(helpers.get_vcs_link())
    def test_decreasing_reward_transfered_funds(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_use_pool1: clusterlib.ClusterLib,
    ):
        """Check that rewards are gradually decreasing when funds are being transfered.

        Even though nothing is staked and rewards are being transfered from reward address, there
        are still some funds staked on the reward address at the time ledger snapshot is taken. For
        that reason the reward amount received every epoch is gradually decreasing over the period
        of several epochs until it is finally 0.

        * delegate stake address
        * wait for first reward
        * transfer all funds from payment address back to faucet, so no funds are staked
        * keep withdrawing new rewards so reward balance is 0
        * check that reward amount is decreasing epoch after epoch
        """
        pool_name = "node-pool1"
        cluster = cluster_use_pool1

        temp_template = common.get_test_id(cluster)

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-20, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        # submit registration certificate and delegate to pool
        pool_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        assert (
            cluster.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # create destination address for rewards withdrawal
        dst_addr_record = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_dst_addr", cluster_obj=cluster
        )[0]

        # fund destination address
        clusterlib_utils.fund_from_faucet(
            dst_addr_record,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        # make sure we have enough time to finish the transfer in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-40, force_epoch=False
        )

        # transfer all funds from payment address back to faucet, so no funds are staked
        clusterlib_utils.return_funds_to_faucet(
            delegation_out.pool_user.payment,
            cluster_obj=cluster,
            faucet_addr=cluster_manager.cache.addrs_data["user1"]["payment"].address,
            tx_name=temp_template,
        )
        assert (
            cluster.get_address_balance(delegation_out.pool_user.payment.address) == 0
        ), f"Incorrect balance for source address `{delegation_out.pool_user.payment.address}`"

        rewards_rec = []

        # keep withdrawing new rewards so reward balance is 0
        def _withdraw():
            rewards = cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance
            if rewards:
                epoch = cluster.get_epoch()
                payment_balance = cluster.get_address_balance(
                    delegation_out.pool_user.payment.address
                )
                rewards_rec.append(rewards)
                LOGGER.info(f"epoch {epoch} - reward: {rewards}, payment: {payment_balance}")
                # TODO - check ledger state wrt stake amount and expected reward
                clusterlib_utils.save_ledger_state(
                    cluster_obj=cluster, state_name=f"{temp_template}_{epoch}"
                )
                # withdraw rewards to destination address
                cluster.withdraw_reward(
                    stake_addr_record=delegation_out.pool_user.stake,
                    dst_addr_record=dst_addr_record,
                    tx_name=f"{temp_template}_ep{epoch}",
                )

        LOGGER.info("Withdrawing new rewards for next 4 epochs.")
        _withdraw()
        for __ in range(4):
            cluster.wait_for_new_epoch(padding_seconds=10)
            _withdraw()

        assert rewards_rec[-1] < rewards_rec[-2] // 3, "Rewards are not decreasing"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_unmet_pledge1(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool2: clusterlib.ClusterLib,
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
        pool_name = "node-pool2"
        cluster = cluster_lock_pool2

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        temp_template = common.get_test_id(cluster)

        pool_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-20, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        # submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        assert (
            cluster.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish the pool update in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-40, force_epoch=False
        )

        # load and update original pool data
        loaded_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=pool_id
        )
        pool_data_updated = loaded_data._replace(pool_pledge=loaded_data.pool_pledge * 9)

        # increase the needed pledge amount - update the pool parameters by resubmitting the pool
        # registration certificate
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
            delegation_out.pool_user.stake.address
        ).reward_account_balance

        cluster.wait_for_new_epoch(3)

        with cluster_manager.restart_on_failure():
            # check that NO new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                == cluster.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "Received unexpected rewards"

            # check that pool owner is also NOT receiving rewards
            assert (
                orig_owner_reward
                == cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # Return the pool to the original state - restore pledge settings.

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
                < cluster.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                orig_owner_reward
                < cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "New reward was not received by pool reward address"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_unmet_pledge2(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool2: clusterlib.ClusterLib,
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
        pool_name = "node-pool2"
        cluster = cluster_lock_pool2

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        temp_template = common.get_test_id(cluster)

        pool_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-20, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        # submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        assert (
            cluster.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to withdraw the pledge in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-40, force_epoch=False
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
        cluster.send_funds(
            src_address=pool_owner.payment.address,
            destinations=destinations,
            tx_name=f"{temp_template}_withdraw_pledge",
            tx_files=tx_files,
        )

        assert cluster.get_address_balance(pool_owner.payment.address) < loaded_data.pool_pledge, (
            f"Pledge still high - pledge: {loaded_data.pool_pledge}, "
            f"funds: {cluster.get_address_balance(pool_owner.payment.address)}"
        )

        cluster.wait_for_new_epoch(4, padding_seconds=30)

        orig_owner_reward = cluster.get_stake_addr_info(
            pool_rec["reward"].address
        ).reward_account_balance
        orig_user_reward = cluster.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance

        cluster.wait_for_new_epoch(3)

        with cluster_manager.restart_on_failure():
            # check that NO new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                == cluster.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "Received unexpected rewards"

            # check that pool owner is also NOT receiving rewards
            assert (
                orig_owner_reward
                == cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # Return the pool to the original state - restore pledge funds.

            # fund user address so it has enough funds for fees etc.
            clusterlib_utils.fund_from_faucet(
                delegation_out.pool_user,
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
            tx_files = clusterlib.TxFiles(
                signing_key_files=[delegation_out.pool_user.payment.skey_file]
            )
            cluster.send_funds(
                src_address=delegation_out.pool_user.payment.address,
                destinations=destinations,
                tx_name=f"{temp_template}_return_pledge",
                tx_files=tx_files,
            )

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
                < cluster.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                orig_owner_reward
                < cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "New reward was not received by pool reward address"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_deregistered_stake_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool2: clusterlib.ClusterLib,
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
        pool_name = "node-pool2"
        cluster = cluster_lock_pool2

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        temp_template = common.get_test_id(cluster)

        pool_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-20, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        # submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        assert (
            cluster.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-40, force_epoch=False
        )

        # deregister stake address - owner's stake is lower than pledge
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

        with cluster_manager.restart_on_failure():
            # check that the key deposit was returned
            assert (
                cluster.get_address_balance(pool_owner.payment.address)
                == src_init_balance - tx_raw_deregister_output.fee + cluster.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_owner.payment.address}`"

            # check that the stake address is no longer delegated
            assert not cluster.get_stake_addr_info(
                pool_owner.stake.address
            ), "Stake address still delegated"

            cluster.wait_for_new_epoch(4, padding_seconds=30)

            orig_owner_reward = cluster.get_stake_addr_info(
                pool_rec["reward"].address
            ).reward_account_balance
            orig_user_reward = cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance

            cluster.wait_for_new_epoch(3)

            # check that NO new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                == cluster.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "Received unexpected rewards"

            # check that pool owner is also NOT receiving rewards
            assert (
                orig_owner_reward
                == cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # Return the pool to the original state - reregister stake address and
            # delegate it to the pool.

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

            # check that the balance for source address was correctly updated
            assert (
                cluster.get_address_balance(pool_owner.payment.address)
                == src_updated_balance - tx_raw_output.fee - cluster.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_owner.payment.address}`"

            cluster.wait_for_new_epoch(4, padding_seconds=30)

            # check that the stake address was delegated
            stake_addr_info = cluster.get_stake_addr_info(pool_owner.stake.address)
            assert (
                stake_addr_info.delegation
            ), f"Stake address was not delegated yet: {stake_addr_info}"

            assert pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

            # check that new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(pool_rec["reward"].address).reward_account_balance
            ), "New reward was not received by pool reward address"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_deregistered_reward_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool2: clusterlib.ClusterLib,
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
        pool_name = "node-pool2"
        cluster = cluster_lock_pool2

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
        temp_template = common.get_test_id(cluster)

        pool_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-20, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        # submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        assert (
            cluster.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-40, force_epoch=False
        )

        # withdraw pool rewards to payment address
        # use `transaction build` if possible
        if (
            VERSIONS.transaction_era >= VERSIONS.ALONZO
            and VERSIONS.transaction_era == VERSIONS.cluster_era
        ):
            withdraw_reward_w_build(
                cluster_obj=cluster,
                stake_addr_record=pool_reward.stake,
                dst_addr_record=pool_reward.payment,
                tx_name=temp_template,
            )
        else:
            cluster.withdraw_reward(
                stake_addr_record=pool_reward.stake,
                dst_addr_record=pool_reward.payment,
                tx_name=temp_template,
            )

        # deregister the pool reward address
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

        with cluster_manager.restart_on_failure():
            # check that the key deposit was returned
            assert (
                cluster.get_address_balance(pool_reward.payment.address)
                == src_init_balance - tx_raw_deregister_output.fee + cluster.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            # check that the reward address is no longer delegated
            assert not cluster.get_stake_addr_info(
                pool_reward.stake.address
            ), "Stake address still delegated"

            orig_user_reward = cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance

            cluster.wait_for_new_epoch(3)

            # check that pool owner is NOT receiving rewards
            assert (
                cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance == 0
            ), "Pool owner received unexpected rewards"

            # check that new rewards are received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "New reward was not received by stake address"

            # Return the pool to the original state - reregister reward address.

            # fund source address
            clusterlib_utils.fund_from_faucet(
                pool_reward,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=150_000_000,
                force=True,
            )

            src_updated_balance = cluster.get_address_balance(pool_reward.payment.address)

            # reregister reward address
            tx_files = clusterlib.TxFiles(
                certificate_files=[
                    pool_rec["reward_addr_registration_cert"],
                ],
                signing_key_files=[pool_reward.payment.skey_file, pool_reward.stake.skey_file],
            )
            tx_raw_output = cluster.send_tx(
                src_address=pool_reward.payment.address,
                tx_name=f"{temp_template}_rereg_deleg",
                tx_files=tx_files,
            )

            # check that the balance for source address was correctly updated
            assert (
                cluster.get_address_balance(pool_reward.payment.address)
                == src_updated_balance - tx_raw_output.fee - cluster.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            cluster.wait_for_new_epoch(4, padding_seconds=30)

            # check that new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(
                    delegation_out.pool_user.stake.address
                ).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance > 0
            ), "New reward was not received by pool reward address"

    @allure.link(helpers.get_vcs_link())
    def test_deregister_reward_addr_retire_pool(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool2: clusterlib.ClusterLib,
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
        # pylint: disable=too-many-statements
        __: Any  # mypy workaround
        pool_name = "node-pool2"
        cluster = cluster_lock_pool2

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        pool_opcert_file: Path = pool_rec["pool_operational_cert"]
        temp_template = common.get_test_id(cluster)

        LOGGER.info("Waiting up to 4 full epochs for first reward.")
        for i in range(5):
            if i > 0:
                cluster.wait_for_new_epoch(padding_seconds=10)
            if cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance:
                break
        else:
            pytest.skip(f"Pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish reward address deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-40, force_epoch=False
        )

        # withdraw pool rewards to payment address
        cluster.withdraw_reward(
            stake_addr_record=pool_reward.stake,
            dst_addr_record=pool_reward.payment,
            tx_name=temp_template,
        )

        # deregister the pool reward address
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

        with cluster_manager.restart_on_failure():
            # check that the key deposit was returned
            assert (
                cluster.get_address_balance(pool_reward.payment.address)
                == src_init_balance - tx_raw_deregister_output.fee + cluster.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            # check that the reward address is no longer delegated
            assert not cluster.get_stake_addr_info(
                pool_reward.stake.address
            ), "Stake address still delegated"

            cluster.wait_for_new_epoch(3)

            # check that pool owner is NOT receiving rewards
            assert (
                cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance == 0
            ), "Pool owner received unexpected rewards"

            # fund source address
            clusterlib_utils.fund_from_faucet(
                pool_reward,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=150_000_000,
                force=True,
            )

            # make sure we have enough time to finish pool deregistration in one epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=5, stop=-40, force_epoch=False
            )

            src_dereg_balance = cluster.get_address_balance(pool_owner.payment.address)
            stake_acount_balance = cluster.get_stake_addr_info(
                pool_owner.stake.address
            ).reward_account_balance
            reward_acount_balance = cluster.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance

            node_cold = pool_rec["cold_key_pair"]
            pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)

            # deregister stake pool
            depoch = cluster.get_epoch() + 1
            __, tx_raw_output = cluster.deregister_stake_pool(
                pool_owners=[pool_owner],
                cold_key_pair=node_cold,
                epoch=depoch,
                pool_name=pool_name,
                tx_name=temp_template,
            )
            assert cluster.get_pool_params(pool_id).retiring == depoch

            # check that the pool was deregistered
            cluster.wait_for_new_epoch()
            assert not cluster.get_pool_params(
                pool_id
            ).pool_params, f"The pool {pool_id} was not deregistered"

            # check command kes-period-info case: de-register pool
            kes_period_info = cluster.get_kes_period_info(pool_opcert_file)
            kes.check_kes_period_info_result(
                kes_output=kes_period_info, expected_scenario=kes.KesScenarios.ALL_VALID
            )

            # check that the balance for source address was correctly updated
            assert src_dereg_balance - tx_raw_output.fee == cluster.get_address_balance(
                pool_owner.payment.address
            )

            # check that the pool deposit was NOT returned to reward or stake address
            assert (
                cluster.get_stake_addr_info(pool_owner.stake.address).reward_account_balance
                == stake_acount_balance
            )
            assert (
                cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance
                == reward_acount_balance
            )

            # Return the pool to the original state - reregister the pool, register
            # the reward address, delegate the stake address to the pool.

            src_updated_balance = cluster.get_address_balance(pool_reward.payment.address)

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
            tx_raw_output = cluster.send_tx(
                src_address=pool_reward.payment.address,
                tx_name=f"{temp_template}_rereg_pool",
                tx_files=tx_files,
            )

            # check command kes-period-info case: re-register pool, check without
            # waiting to take effect
            kes_period_info = cluster.get_kes_period_info(pool_opcert_file)
            kes.check_kes_period_info_result(
                kes_output=kes_period_info, expected_scenario=kes.KesScenarios.ALL_VALID
            )

            # check that the balance for source address was correctly updated and that the
            # pool deposit was needed
            assert (
                cluster.get_address_balance(pool_reward.payment.address)
                == src_updated_balance
                - tx_raw_output.fee
                - cluster.get_pool_deposit()
                - cluster.get_address_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            LOGGER.info("Waiting up to 5 epochs for stake pool to be reregistered.")
            for __ in range(5):
                cluster.wait_for_new_epoch(padding_seconds=10)
                if pool_id in cluster.get_stake_distribution():
                    break
            else:
                raise AssertionError(f"Stake pool `{pool_id}` not registered even after 5 epochs.")

            # check command kes-period-info case: re-register pool
            kes_period_info = cluster.get_kes_period_info(pool_opcert_file)
            kes.check_kes_period_info_result(
                kes_output=kes_period_info, expected_scenario=kes.KesScenarios.ALL_VALID
            )

            # wait before checking delegation and rewards
            cluster.wait_for_new_epoch(3, padding_seconds=30)

            # check that the stake address was delegated
            stake_addr_info = cluster.get_stake_addr_info(pool_owner.stake.address)
            assert (
                stake_addr_info.delegation
            ), f"Stake address was not delegated yet: {stake_addr_info}"

            assert pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

            # check that pool owner is receiving rewards
            assert cluster.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance, "New reward was not received by pool reward address"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.needs_dbsync
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="needs Allegra+ TX to run",
    )
    def test_2_pools_same_reward_addr(  # noqa: C901
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool1_2: clusterlib.ClusterLib,
    ):
        """Check that one reward address used for two pools receives rewards for both of them.

        * set pool2 reward address to the reward address of pool1 by resubmitting the pool
          registration certificate
        * collect data for both pool1 and pool2 for several epochs and with the help of db-sync

           - check that the original reward address for pool2 is NOT receiving rewards
           - check that the reward address for pool1 is now receiving rewards for both pools

        * check records in db-sync

           - transaction inputs, outputs, withdrawals, etc.
           - reward amounts received each epoch
           - expected pool ids
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-locals
        pool_name = "node-pool2"
        cluster = cluster_lock_pool1_2
        temp_template = common.get_test_id(cluster)

        pool1_rec = cluster_manager.cache.addrs_data["node-pool1"]
        pool1_reward = clusterlib.PoolUser(payment=pool1_rec["payment"], stake=pool1_rec["reward"])
        pool1_node_cold = pool1_rec["cold_key_pair"]
        pool1_id = cluster.get_stake_pool_id(pool1_node_cold.vkey_file)

        pool2_rec = cluster_manager.cache.addrs_data[pool_name]
        pool2_owner = clusterlib.PoolUser(payment=pool2_rec["payment"], stake=pool2_rec["stake"])
        pool2_reward = clusterlib.PoolUser(payment=pool2_rec["payment"], stake=pool2_rec["reward"])
        pool2_node_cold = pool2_rec["cold_key_pair"]
        pool2_id = cluster.get_stake_pool_id(pool2_node_cold.vkey_file)

        # load pool data
        loaded_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=pool2_id
        )

        LOGGER.info("Waiting up to 4 full epochs for first rewards.")
        for i in range(5):
            if i > 0:
                cluster.wait_for_new_epoch(padding_seconds=10)
            pool1_amount = cluster.get_stake_addr_info(
                pool1_reward.stake.address
            ).reward_account_balance
            pool2_amount = cluster.get_stake_addr_info(
                pool2_reward.stake.address
            ).reward_account_balance
            if pool1_amount and pool2_amount:
                break
        else:
            pytest.skip("Pools haven't received any rewards, cannot continue.")

        # make sure we have enough time to submit pool registration cert in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-20, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        # fund source address so the pledge is still met after TX fees are deducted
        clusterlib_utils.fund_from_faucet(
            pool2_reward,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=150_000_000,
            force=True,
        )

        # set pool2 reward address to the reward address of pool1 by resubmitting the pool
        # registration certificate
        pool_reg_cert_file = cluster.gen_pool_registration_cert(
            pool_data=loaded_data,
            vrf_vkey_file=pool2_rec["vrf_key_pair"].vkey_file,
            cold_vkey_file=pool2_rec["cold_key_pair"].vkey_file,
            owner_stake_vkey_files=[pool2_owner.stake.vkey_file],
            reward_account_vkey_file=pool1_rec["reward"].vkey_file,
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[pool_reg_cert_file],
            signing_key_files=[
                pool2_owner.payment.skey_file,
                pool2_owner.stake.skey_file,
                pool2_rec["cold_key_pair"].skey_file,
            ],
        )
        tx_raw_update_pool = cluster.send_tx(
            src_address=pool2_owner.payment.address,
            tx_name=f"{temp_template}_update_pool2",
            tx_files=tx_files,
            deposit=0,  # no additional deposit, the pool is already registered
        )

        # pool configuration changed, restart needed
        cluster_manager.set_needs_restart()

        assert (
            cluster.get_epoch() == init_epoch
        ), "Pool setup took longer than expected and would affect other checks"
        this_epoch = init_epoch

        # rewards each epoch
        rewards_ledger_pool1: List[RewardRecord] = []
        rewards_ledger_pool2: List[RewardRecord] = []

        # check rewards
        for ep in range(6):
            if ep > 0:
                cluster.wait_for_new_epoch(padding_seconds=10)
                this_epoch = cluster.get_epoch()

            pool1_amount = cluster.get_stake_addr_info(
                pool1_reward.stake.address
            ).reward_account_balance
            pool2_amount = cluster.get_stake_addr_info(
                pool2_reward.stake.address
            ).reward_account_balance

            reward_for_epoch_pool1 = 0
            if rewards_ledger_pool1:
                prev_record_pool1 = rewards_ledger_pool1[-1]
                reward_for_epoch_pool1 = pool1_amount - prev_record_pool1.reward_total

            reward_for_epoch_pool2 = 0
            if rewards_ledger_pool2:
                prev_record_pool2 = rewards_ledger_pool2[-1]
                reward_for_epoch_pool2 = pool2_amount - prev_record_pool2.reward_total

            leader_ids_pool1 = [pool1_id]
            leader_ids_pool2 = [pool2_id]

            # pool re-registration took affect in `init_epoch` + 1
            if this_epoch >= init_epoch + 1:
                leader_ids_pool1 = [pool1_id, pool2_id]
                leader_ids_pool2 = []

            # pool2 starts receiving leader rewards on pool1 address in `init_epoch` + 5
            # (re-registration epoch + 4)
            if this_epoch >= init_epoch + 5:
                # check that the original reward address for pool2 is NOT receiving rewards
                assert (
                    reward_for_epoch_pool2 == 0
                ), "Original reward address of 'pool2' received unexpected rewards"

            # rewards each epoch
            rewards_ledger_pool1.append(
                RewardRecord(
                    epoch_no=this_epoch,
                    reward_total=pool1_amount,
                    reward_per_epoch=reward_for_epoch_pool1,
                    leader_pool_ids=leader_ids_pool1,
                )
            )

            rewards_ledger_pool2.append(
                RewardRecord(
                    epoch_no=this_epoch,
                    reward_total=pool2_amount,
                    reward_per_epoch=reward_for_epoch_pool2,
                    leader_pool_ids=leader_ids_pool2,
                )
            )

            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster,
                state_name=f"{temp_template}_{this_epoch}",
                ledger_state=ledger_state,
            )

        assert (
            len(rewards_ledger_pool1[-1].leader_pool_ids) == 2
        ), "Reward address of 'pool1' is not used as reward address for both 'pool1' and 'pool2'"
        assert rewards_ledger_pool1[
            -1
        ].reward_per_epoch, (
            f"Reward address didn't receive any reward in epoch {rewards_ledger_pool1[-1].epoch_no}"
        )
        assert (
            rewards_ledger_pool2[-1].reward_per_epoch == 0
        ), "Original reward address of 'pool2' received unexpected rewards"

        # check TX records in db-sync
        assert dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_update_pool)

        # check pool records in db-sync
        pool1_params: dict = cluster.get_pool_params(pool1_id).pool_params
        dbsync_utils.check_pool_data(ledger_pool_data=pool1_params, pool_id=pool1_id)
        pool2_params: dict = cluster.get_pool_params(pool2_id).pool_params
        dbsync_utils.check_pool_data(ledger_pool_data=pool2_params, pool_id=pool2_id)

        # check rewards in db-sync
        rewards_db_pool1 = _dbsync_check_rewards(
            stake_address=pool1_reward.stake.address,
            rewards=rewards_ledger_pool1,
        )
        rewards_db_pool2 = _dbsync_check_rewards(
            stake_address=pool2_reward.stake.address,
            rewards=rewards_ledger_pool2,
        )

        # in db-sync check that pool1 reward address is used as reward address for pool1, and
        # in the expected epochs also for pool2
        reward_types_pool1: Dict[int, List[str]] = {}
        for rec in rewards_db_pool1.rewards:
            stored_types = reward_types_pool1.get(rec.earned_epoch)
            if stored_types is None:
                reward_types_pool1[rec.earned_epoch] = [rec.type]
                continue
            stored_types.append(rec.type)

        for repoch, rtypes in reward_types_pool1.items():
            if repoch <= init_epoch + 2:
                assert rtypes == ["leader"]
            else:
                assert rtypes == ["leader", "leader"]

        # in db-sync check that pool2 reward address is NOT used for receiving rewards anymore
        # in the expected epochs
        reward_types_pool2: Dict[int, List[str]] = {}
        for rec in rewards_db_pool2.rewards:
            stored_types = reward_types_pool2.get(rec.earned_epoch)
            if stored_types is None:
                reward_types_pool2[rec.earned_epoch] = [rec.type]
                continue
            stored_types.append(rec.type)

        for repoch, rtypes in reward_types_pool2.items():
            if repoch <= init_epoch + 2:
                assert rtypes == ["leader"]
            else:
                assert not rtypes

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_redelegation(  # noqa: C901
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_use_pool1_2: clusterlib.ClusterLib,
    ):
        """Check rewards received by stake address over multiple epochs.

        The address is re-delegated and deregistred / re-registered multiple times.

        * delegate stake address to pool
        * in next epoch, re-delegate to another pool
        * in next epoch, deregister stake address, immediatelly re-register and delegate to pool
        * in next epoch, deregister stake address, wait for second half of an epoch, re-register
          and delegate to pool
        * while doing the steps above, collect data for pool user for 8 epochs

           - each epoch check ledger state (expected data in `pstake*`, delegation, stake amount)
           - each epoch check received reward with reward in ledger state

        * (optional) check records in db-sync
        """
        # pylint: disable=too-many-statements,too-many-locals,too-many-branches
        __: Any  # mypy workaround
        pool1_name = "node-pool1"
        pool2_name = "node-pool2"
        cluster = cluster_use_pool1_2

        temp_template = common.get_test_id(cluster)

        pool1_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool1_name
        )
        pool2_id = delegation.get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool2_name
        )

        # make sure we have enough time to finish the registration/delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-40, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        # submit registration certificate and delegate to pool1
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool1_id,
        )

        # make sure we managed to finish registration in the expected epoch
        assert (
            cluster.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        reward_records = [
            RewardRecord(
                epoch_no=init_epoch,
                reward_total=0,
                reward_per_epoch=0,
                member_pool_id=pool1_id,
                stake_total=cluster.get_address_balance(delegation_out.pool_user.payment.address),
            )
        ]

        stake_addr_dec = helpers.decode_bech32(delegation_out.pool_user.stake.address)[2:]

        # ledger state db
        rs_records: dict = {init_epoch: None}

        def _check_ledger_state(
            this_epoch: int,
        ) -> None:
            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster,
                state_name=f"{temp_template}_{this_epoch}",
                ledger_state=ledger_state,
            )
            es_snapshot: dict = ledger_state["stateBefore"]["esSnapshots"]
            rs_record: list = ledger_state["possibleRewardUpdate"]["rs"]
            rs_records[this_epoch] = rs_record

            # Make sure reward amount corresponds with ledger state.
            # Reward is received on epoch boundary, so check reward with record for previous epoch.
            prev_rs_record = rs_records.get(this_epoch - 1)
            reward_for_epoch = reward_records[-1].reward_per_epoch
            if reward_for_epoch and prev_rs_record:
                assert reward_for_epoch == _get_reward_amount_for_key_hash(
                    stake_addr_dec, prev_rs_record
                )

            pstake_mark = _get_key_hashes(es_snapshot["pstakeMark"]["stake"])
            pstake_set = _get_key_hashes(es_snapshot["pstakeSet"]["stake"])
            pstake_go = _get_key_hashes(es_snapshot["pstakeGo"]["stake"])

            if this_epoch == init_epoch + 1:
                assert stake_addr_dec in pstake_mark
                assert stake_addr_dec not in pstake_set
                assert stake_addr_dec not in pstake_go

                # make sure ledger state and actual stake correspond
                assert (
                    _get_val_for_key_hash(stake_addr_dec, es_snapshot["pstakeMark"]["stake"])
                    == reward_records[-1].stake_total
                )

            if this_epoch == init_epoch + 2:
                assert stake_addr_dec in pstake_mark
                assert stake_addr_dec in pstake_set
                assert stake_addr_dec not in pstake_go

                assert (
                    _get_val_for_key_hash(stake_addr_dec, es_snapshot["pstakeMark"]["stake"])
                    == reward_records[-1].stake_total
                )
                assert (
                    _get_val_for_key_hash(stake_addr_dec, es_snapshot["pstakeSet"]["stake"])
                    == reward_records[-2].stake_total
                )

            if this_epoch >= init_epoch + 3:
                assert stake_addr_dec in pstake_mark
                assert stake_addr_dec in pstake_set
                assert stake_addr_dec in pstake_go

                assert (
                    _get_val_for_key_hash(stake_addr_dec, es_snapshot["pstakeMark"]["stake"])
                    == reward_records[-1].stake_total
                )
                assert (
                    _get_val_for_key_hash(stake_addr_dec, es_snapshot["pstakeSet"]["stake"])
                    == reward_records[-2].stake_total
                )
                assert (
                    _get_val_for_key_hash(stake_addr_dec, es_snapshot["pstakeGo"]["stake"])
                    == reward_records[-3].stake_total
                )

        LOGGER.info("Checking rewards for 8 epochs.")
        for __ in range(8):
            # reward balance in previous epoch
            prev_epoch, prev_reward_total, *__ = reward_records[-1]

            # wait for new epoch
            if cluster.get_epoch() == prev_epoch:
                cluster.wait_for_new_epoch(padding_seconds=15)

            this_epoch = cluster.get_epoch()

            # current reward balance
            reward_total = cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance

            # Total reward amount received this epoch.
            # If `reward_total < prev_reward_total`, withdrawal took place during previous epoch.
            if reward_total > prev_reward_total:
                reward_for_epoch = reward_total - prev_reward_total
            else:
                reward_for_epoch = reward_total

            # current payment balance
            payment_balance = cluster.get_address_balance(delegation_out.pool_user.payment.address)

            # stake amount this epoch
            stake_total = payment_balance + reward_total

            if this_epoch == init_epoch + 2:
                # re-delegate to pool2
                delegation_out_ep2 = delegation.delegate_stake_addr(
                    cluster_obj=cluster,
                    addrs_data=cluster_manager.cache.addrs_data,
                    temp_template=f"{temp_template}_ep2",
                    pool_user=delegation_out.pool_user,
                    pool_id=pool2_id,
                )

            if this_epoch == init_epoch + 3:
                # deregister stake address
                clusterlib_utils.deregister_stake_address(
                    cluster_obj=cluster,
                    pool_user=delegation_out.pool_user,
                    name_template=f"{temp_template}_ep3",
                )
                # re-register, delegate to pool1
                delegation_out_ep3 = delegation.delegate_stake_addr(
                    cluster_obj=cluster,
                    addrs_data=cluster_manager.cache.addrs_data,
                    temp_template=f"{temp_template}_ep3",
                    pool_user=delegation_out.pool_user,
                    pool_id=pool1_id,
                )

            if this_epoch == init_epoch + 4:
                assert (
                    reward_total > prev_reward_total
                ), "New reward was not received by stake address"

                # deregister stake address
                clusterlib_utils.deregister_stake_address(
                    cluster_obj=cluster,
                    pool_user=delegation_out.pool_user,
                    name_template=f"{temp_template}_ep4",
                )
                # wait for second half of an epoch
                clusterlib_utils.wait_for_epoch_interval(
                    cluster_obj=cluster, start=-60, stop=-40, check_slot=False
                )
                # re-register, delegate to pool1
                delegation_out_ep4 = delegation.delegate_stake_addr(
                    cluster_obj=cluster,
                    addrs_data=cluster_manager.cache.addrs_data,
                    temp_template=f"{temp_template}_ep4",
                    pool_user=delegation_out.pool_user,
                    pool_id=pool1_id,
                )

            if this_epoch == init_epoch + 5:
                assert reward_total == 0, "Unexpected reward was received by stake address"

            if this_epoch >= init_epoch + 6:
                assert (
                    reward_total > prev_reward_total
                ), "New reward was not received by stake address"

            # sleep till the end of epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=-19, stop=-15, check_slot=False
            )

            # store collected rewards info
            reward_records.append(
                RewardRecord(
                    epoch_no=this_epoch,
                    reward_total=reward_total,
                    reward_per_epoch=reward_for_epoch,
                    member_pool_id=cluster.get_stake_addr_info(
                        delegation_out.pool_user.stake.address
                    ).delegation,
                    stake_total=stake_total,
                )
            )

            _check_ledger_state(this_epoch=this_epoch)

        # check records in db-sync
        tx_db_record_init = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out.tx_raw_output
        )
        tx_db_record_ep2 = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out_ep2.tx_raw_output
        )
        tx_db_record_ep3 = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out_ep3.tx_raw_output
        )
        tx_db_record_ep4 = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out_ep4.tx_raw_output
        )

        if tx_db_record_init:
            delegation.db_check_delegation(
                pool_user=delegation_out.pool_user,
                db_record=tx_db_record_init,
                deleg_epoch=init_epoch,
                pool_id=delegation_out.pool_id,
            )

            assert tx_db_record_ep2
            assert (
                delegation_out_ep2.pool_user.stake.address
                not in tx_db_record_ep2.stake_registration
            )
            assert (
                delegation_out_ep2.pool_user.stake.address
                == tx_db_record_ep2.stake_delegation[0].address
            )
            assert tx_db_record_ep2.stake_delegation[0].active_epoch_no == init_epoch + 4
            assert delegation_out_ep2.pool_id == tx_db_record_ep2.stake_delegation[0].pool_id

            delegation.db_check_delegation(
                pool_user=delegation_out_ep3.pool_user,
                db_record=tx_db_record_ep3,
                deleg_epoch=init_epoch + 3,
                pool_id=delegation_out_ep3.pool_id,
            )

            delegation.db_check_delegation(
                pool_user=delegation_out_ep4.pool_user,
                db_record=tx_db_record_ep4,
                deleg_epoch=init_epoch + 4,
                pool_id=delegation_out_ep4.pool_id,
            )

            _dbsync_check_rewards(
                stake_address=delegation_out.pool_user.stake.address,
                rewards=reward_records,
            )
