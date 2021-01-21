"""Tests for staking, rewards, blocks production on real block-producing pools."""
import logging
import time
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
from cardano_node_tests.utils.devops_cluster import VERSIONS

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    return Path(tmp_path_factory.mktemp(helpers.get_id_for_mktemp(__file__))).resolve()


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


@pytest.fixture
def cluster_use_pool1(cluster_manager: parallel_run.ClusterManager) -> clusterlib.ClusterLib:
    return cluster_manager.get(use_resources=["node-pool1"])


@pytest.fixture
def cluster_lock_pool2(cluster_manager: parallel_run.ClusterManager) -> clusterlib.ClusterLib:
    return cluster_manager.get(lock_resources=["node-pool2"])


@pytest.fixture
def pool_users(
    cluster_manager: parallel_run.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.PoolUser]:
    """Create pool users."""
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        created_users = clusterlib_utils.create_pool_users(
            cluster_obj=cluster,
            name_template=f"test_staking_pool_users_ci{cluster_manager.cluster_instance}",
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
    pool_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster,
        name_template=f"test_staking_pool_users_{clusterlib.get_rand_str(3)}",
        no_of_addr=2,
    )
    return pool_users


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


def _get_key_hashes(rec: dict) -> List[str]:
    """Get key hashes in ledger state snapshot record."""
    return [r[0]["key hash"] for r in rec]


def _get_val_for_key_hash(key_hash: str, rec: list) -> Any:
    """Get value for key hash in ledger state snapshot record."""
    for r in rec:
        if r[0]["key hash"] == key_hash:
            return r[1]
    return None


def _delegate_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    addrs_data: dict,
    temp_template: str,
    pool_name: str,
    delegate_with_pool_id: bool = False,
    check_delegation: bool = True,
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

    # check that the stake address was delegated
    if check_delegation:
        clusterlib_utils.wait_for_stake_distribution(cluster_obj)
        stake_addr_info = cluster_obj.get_stake_addr_info(stake_addr_rec.address)
        assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"
        assert stake_pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

    return pool_user


@pytest.mark.run(order=3)
class TestDelegateAddr:
    """Tests for address delegation to stake pools."""

    @allure.link(helpers.get_vcs_link())
    def test_delegate_using_pool_id(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster_use_pool1: clusterlib.ClusterLib,
    ):
        """Submit registration certificate and delegate to pool using pool id.

        * register stake address and delegate it to pool
        * check that the stake address was delegated
        """
        pool_name = "node-pool1"
        cluster = cluster_use_pool1
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
        cluster_use_pool1: clusterlib.ClusterLib,
    ):
        """Submit registration certificate and delegate to pool using cold vkey.

        * register stake address and delegate it to pool
        * check that the stake address was delegated
        """
        pool_name = "node-pool1"
        cluster = cluster_use_pool1
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )

    @pytest.mark.run(order=2)
    @allure.link(helpers.get_vcs_link())
    def test_deregister(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster_use_pool1: clusterlib.ClusterLib,
    ):
        """Deregister stake address.

        * submit registration certificate and delegate to pool
        * attempt to deregister the stake address - deregistration is expected to fail
          because there are rewards in the stake address
        * withdraw rewards to payment address
        * deregister stake address
        * check that the key deposit was returned
        * check that the stake address is no longer delegated
        """
        pool_name = "node-pool1"
        cluster = cluster_use_pool1
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

        LOGGER.info("Waiting up to 4 epochs for first reward.")
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

        # attempt to deregister the stake address - deregistration is expected to fail
        # because there are rewards in the stake address
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
        """Submit registration and deregistration certificates in single TX.

        * create stake address registration cert
        * create stake address deregistration cert
        * register and deregister stake address in single TX
        * check that the balance for source address was correctly updated and that key deposit
          was not needed
        """
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

        # check that the balance for source address was correctly updated and that key deposit
        # was not needed
        assert (
            cluster.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output.fee
        ), f"Incorrect balance for source address `{user_payment.address}`"

    @allure.link(helpers.get_vcs_link())
    def test_addr_delegation_deregistration(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster_use_pool1: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
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
        """
        pool_name = "node-pool1"
        cluster = cluster_use_pool1

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

        # check that the balance for source address was correctly updated and that the key
        # deposit was returned
        assert (
            cluster.get_address_balance(user_payment.address)
            == src_registered_balance - tx_raw_output_deleg.fee + cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{user_payment.address}`"

        clusterlib_utils.wait_for_stake_distribution(cluster)

        # check that the stake address was NOT delegated
        stake_addr_info = cluster.get_stake_addr_info(user_registered.stake.address)
        assert not stake_addr_info.delegation, f"Stake address was delegated: {stake_addr_info}"


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
        cluster_use_pool1: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to generate stake address delegation certificate using wrong stake vkey.

        Expect failure.
        """
        pool_name = "node-pool1"
        cluster = cluster_use_pool1

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
        """Try to register stake address using wrong payment skey.

        Expect failure.
        """
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
        cluster_use_pool1: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Try to delegate stake address using wrong payment skey.

        Expect failure.
        """
        pool_name = "node-pool1"
        cluster = cluster_use_pool1

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
        cluster_use_pool1: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Try to delegate unregistered stake address.

        Expect failure.
        """
        pool_name = "node-pool1"
        cluster = cluster_use_pool1

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
    def test_deregister_not_registered_addr(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Deregister not registered stake address."""
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


@pytest.mark.run(order=1)
class TestRewards:
    """Tests for checking expected rewards."""

    @allure.link(helpers.get_vcs_link())
    def test_reward_amount(  # noqa: C901
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster_use_pool1: clusterlib.ClusterLib,
    ):
        """Check that the stake address and pool owner are receiving rewards.

        * delegate to pool
        * collect data for pool owner and pool users for 10 epochs

           - each epoch check ledger state (expected data in `_pstake*`, delegation, stake amount)
           - each epoch check received reward with reward in ledger state

        * withdraw rewards to payment address
        """
        # pylint: disable=too-many-statements,too-many-locals,too-many-branches
        pool_name = "node-pool1"
        cluster = cluster_use_pool1

        temp_template = helpers.get_func_name()
        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
        pool_reward_addr_dec = helpers.decode_bech32(pool_reward.stake.address)[2:]
        pool_stake_addr_dec = helpers.decode_bech32(pool_owner.stake.address)[2:]

        sleep_time = clusterlib_utils.time_to_next_epoch_start(cluster) - 8
        if sleep_time > 0:
            time.sleep(sleep_time)

        init_epoch = cluster.get_last_block_epoch()
        user_rewards = [(init_epoch, 0, 0)]
        owner_rewards = [
            (
                init_epoch,
                cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance,
                0,
            )
        ]

        # save ledger state
        clusterlib_utils.save_ledger_state(
            cluster_obj=cluster, name_template=f"{temp_template}_{init_epoch}"
        )
        ledger_state: dict = cluster.get_ledger_state()
        es_snapshots = {init_epoch: ledger_state["nesEs"]["esSnapshots"]}
        rs_records = {init_epoch: ledger_state["nesRu"]["rs"]}

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
            check_delegation=False,
        )

        user_stake_addr_dec = helpers.decode_bech32(pool_user.stake.address)[2:]
        user_payment_balance = cluster.get_address_balance(pool_user.payment.address)

        LOGGER.info("Checking rewards for 9 epochs.")
        for __ in range(9):
            # reward balances in previous epoch
            prev_user_epoch, prev_user_reward, __ = user_rewards[-1]
            (
                prev_owner_epoch,
                prev_owner_reward,
                __,  # prev_abs_owner_reward
            ) = owner_rewards[-1]

            # wait for new epoch
            if cluster.get_last_block_epoch() == prev_owner_epoch:
                cluster.wait_for_new_epoch()

            # sleep till the end of epoch
            sleep_time = clusterlib_utils.time_to_next_epoch_start(cluster) - 5
            if sleep_time > 0:
                time.sleep(sleep_time)

            this_epoch = cluster.get_last_block_epoch()

            # current reward balances
            user_reward = cluster.get_stake_addr_info(
                pool_user.stake.address
            ).reward_account_balance
            owner_reward = cluster.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance

            # absolute reward amounts received this epoch
            abs_user_reward = (
                user_reward - prev_user_reward if this_epoch == prev_user_epoch + 1 else 0
            )
            abs_owner_reward = (
                owner_reward - prev_owner_reward if this_epoch == prev_owner_epoch + 1 else 0
            )

            # store collected rewards info
            user_rewards.append(
                (
                    this_epoch,
                    user_reward,
                    abs_user_reward,
                )
            )
            owner_rewards.append(
                (
                    this_epoch,
                    owner_reward,
                    abs_owner_reward,
                )
            )

            # save ledger state
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster, name_template=f"{temp_template}_{this_epoch}"
            )
            ledger_state = cluster.get_ledger_state()
            es_snapshot: dict = ledger_state["nesEs"]["esSnapshots"]
            es_snapshots[this_epoch] = es_snapshot
            rs_record: list = ledger_state["nesRu"]["rs"]
            rs_records[this_epoch] = rs_record

            # Make sure reward amount corresponds with ledger state.
            # Reward is received on epoch boundary, so check reward with record for previous epoch.
            if abs_user_reward:
                assert abs_user_reward == _get_val_for_key_hash(
                    user_stake_addr_dec, rs_records[this_epoch - 1]
                )
            if abs_owner_reward:
                assert abs_owner_reward == _get_val_for_key_hash(
                    pool_reward_addr_dec, rs_records[this_epoch - 1]
                )

            _pstake_mark = _get_key_hashes(es_snapshot["_pstakeMark"]["_stake"])
            _pstake_set = _get_key_hashes(es_snapshot["_pstakeSet"]["_stake"])
            _pstake_go = _get_key_hashes(es_snapshot["_pstakeGo"]["_stake"])

            if this_epoch == init_epoch + 2:
                assert pool_stake_addr_dec in _pstake_mark
                assert pool_stake_addr_dec in _pstake_set

                assert user_stake_addr_dec in _pstake_mark
                assert user_stake_addr_dec not in _pstake_set
                assert user_stake_addr_dec not in _pstake_go

            if this_epoch == init_epoch + 3:
                assert user_stake_addr_dec in _pstake_mark
                assert user_stake_addr_dec in _pstake_set
                assert user_stake_addr_dec not in _pstake_go

            if this_epoch >= init_epoch + 3:
                assert pool_stake_addr_dec in _pstake_mark
                assert pool_stake_addr_dec in _pstake_set
                assert pool_stake_addr_dec in _pstake_go

            if this_epoch >= init_epoch + 4:
                assert user_stake_addr_dec in _pstake_mark
                assert user_stake_addr_dec in _pstake_set
                assert user_stake_addr_dec in _pstake_go

            if this_epoch >= init_epoch + 5:
                # wait 5 epochs for first rewards
                assert owner_reward > prev_owner_reward, "New reward was not received by pool owner"
                assert (
                    user_reward > prev_user_reward
                ), "New reward was not received by stake address"

                # make sure ledger state and actual stake correspond
                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["_pstakeMark"]["_stake"])
                    == user_reward + user_payment_balance
                )
                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["_pstakeSet"]["_stake"])
                    == prev_user_reward + user_payment_balance
                )
                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["_pstakeGo"]["_stake"])
                    == user_rewards[-3][1] + user_payment_balance
                )

        # withdraw rewards to payment address
        if this_epoch == cluster.get_last_block_epoch():
            cluster.wait_for_new_epoch()
        clusterlib_utils.withdraw_reward(
            cluster_obj=cluster, pool_user=pool_user, name_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    def test_reward_addr_delegation(  # noqa: C901
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster_lock_pool2: clusterlib.ClusterLib,
    ):
        """Check that the rewards address can be delegated and receive rewards.

        Tests https://github.com/input-output-hk/cardano-node/issues/1964

        The pool has a reward address that is different from pool owner's stake address.

        Collect data for pool owner for 10 epochs and:

        * delegate reward address to stake pool
        * deregister pool owner's stake address
        * each epoch check ledger state (expected data in `_pstake*`, delegation, stake amount)
        * each epoch check received reward with reward in ledger state
        * check that reward address still receives rewards for its staked amount even after
          the pool owner's stake address is deregistered
        """
        # pylint: disable=too-many-statements,too-many-locals
        pool_name = "node-pool2"
        cluster = cluster_lock_pool2

        temp_template = helpers.get_func_name()
        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
        reward_addr_dec = helpers.decode_bech32(pool_reward.stake.address)[2:]
        stake_addr_dec = helpers.decode_bech32(pool_owner.stake.address)[2:]

        # fund pool owner's addresses so balance keeps higher than pool pledge after fees etc.
        # are deducted
        clusterlib_utils.fund_from_faucet(
            pool_owner,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=150_000_000,
            force=True,
        )

        node_cold = pool_rec["cold_key_pair"]
        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)

        # load and update original pool data
        loaded_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=stake_pool_id
        )
        pool_data_updated = loaded_data._replace(pool_pledge=0)

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

        cluster_manager.set_needs_restart()  # changing pool configuration, restart needed

        sleep_time = clusterlib_utils.time_to_next_epoch_start(cluster) - 5
        if sleep_time > 0:
            time.sleep(sleep_time)

        init_epoch = cluster.get_last_block_epoch()
        owner_rewards = [
            (
                init_epoch,
                cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance,
                0,
            )
        ]

        # save ledger state
        clusterlib_utils.save_ledger_state(
            cluster_obj=cluster, name_template=f"{temp_template}_{init_epoch}"
        )
        ledger_state: dict = cluster.get_ledger_state()
        es_snapshots = {init_epoch: ledger_state["nesEs"]["esSnapshots"]}
        rs_records = {init_epoch: ledger_state["nesRu"]["rs"]}

        LOGGER.info("Checking rewards for 9 epochs.")
        for __ in range(9):
            # reward balances in previous epoch
            (
                prev_epoch,
                prev_owner_reward,
                __,  # prev_abs_owner_reward
            ) = owner_rewards[-1]

            # wait for new epoch
            if cluster.get_last_block_epoch() == prev_epoch:
                cluster.wait_for_new_epoch()

            # sleep till the end of epoch
            sleep_time = clusterlib_utils.time_to_next_epoch_start(cluster) - 10
            if sleep_time > 0:
                time.sleep(sleep_time)

            this_epoch = cluster.get_last_block_epoch()

            # current reward balances
            owner_reward = cluster.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance

            # absolute reward amounts received this epoch
            abs_owner_reward = (
                owner_reward - prev_owner_reward if this_epoch == prev_epoch + 1 else 0
            )

            # store collected rewards info
            owner_rewards.append(
                (
                    this_epoch,
                    owner_reward,
                    abs_owner_reward,
                )
            )

            # save ledger state
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster, name_template=f"{temp_template}_{this_epoch}"
            )
            ledger_state = cluster.get_ledger_state()
            es_snapshot: dict = ledger_state["nesEs"]["esSnapshots"]
            es_snapshots[this_epoch] = es_snapshot
            rs_record: list = ledger_state["nesRu"]["rs"]
            rs_records[this_epoch] = rs_record

            # Make sure reward amount corresponds with ledger state.
            # Reward is received on epoch boundary, so check reward with record for previous epoch.
            if abs_owner_reward:
                assert abs_owner_reward == _get_val_for_key_hash(
                    reward_addr_dec, rs_records[this_epoch - 1]
                )

            _pstake_mark = _get_key_hashes(es_snapshot["_pstakeMark"]["_stake"])
            _pstake_set = _get_key_hashes(es_snapshot["_pstakeSet"]["_stake"])
            _pstake_go = _get_key_hashes(es_snapshot["_pstakeGo"]["_stake"])

            if this_epoch == init_epoch + 2:
                assert reward_addr_dec not in _pstake_mark
                assert stake_addr_dec in _pstake_mark

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

                cluster.send_tx(
                    src_address=pool_owner.payment.address,
                    tx_name=f"{temp_template}_deleg_rewards",
                    tx_files=tx_files,
                )
                cluster.wait_for_new_block(new_blocks=2)

                # deregister stake address
                stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
                    addr_name=f"{temp_template}_addr0", stake_vkey_file=pool_owner.stake.vkey_file
                )
                tx_files_deregister = clusterlib.TxFiles(
                    certificate_files=[stake_addr_dereg_cert],
                    signing_key_files=[pool_owner.payment.skey_file, pool_owner.stake.skey_file],
                )

                cluster.send_tx(
                    src_address=pool_owner.payment.address,
                    tx_name=f"{temp_template}_dereg",
                    tx_files=tx_files_deregister,
                )
                cluster.wait_for_new_block(new_blocks=2)

                # make sure we managed to finish deregistration in the expected epoch
                assert (
                    cluster.get_last_block_epoch() == this_epoch
                ), "Deregistration took longer than expected and would affect other checks"

            if this_epoch == init_epoch + 3:
                assert reward_addr_dec in _pstake_mark
                assert reward_addr_dec not in _pstake_set
                assert reward_addr_dec not in _pstake_go

                assert stake_addr_dec in _pstake_mark
                assert stake_addr_dec in _pstake_set
                assert stake_addr_dec in _pstake_go

            if this_epoch == init_epoch + 4:
                assert reward_addr_dec in _pstake_mark
                assert reward_addr_dec in _pstake_set
                assert reward_addr_dec not in _pstake_go

                assert stake_addr_dec not in _pstake_mark
                assert stake_addr_dec in _pstake_set
                assert stake_addr_dec in _pstake_go

            if this_epoch == init_epoch + 5:
                assert stake_addr_dec in _pstake_go

            if this_epoch >= init_epoch + 5:
                assert reward_addr_dec in _pstake_mark
                assert reward_addr_dec in _pstake_set
                assert reward_addr_dec in _pstake_go

                assert stake_addr_dec not in _pstake_mark
                assert stake_addr_dec not in _pstake_set

                # make sure ledger state and actual stake correspond
                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["_pstakeMark"]["_stake"])
                    == owner_reward
                )
                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["_pstakeSet"]["_stake"])
                    == prev_owner_reward
                )
                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["_pstakeGo"]["_stake"])
                    == owner_rewards[-3][1]
                )

            if this_epoch >= init_epoch + 6:
                assert stake_addr_dec not in _pstake_go

    @allure.link(helpers.get_vcs_link())
    def test_decreasing_reward_transfered_funds(
        self,
        cluster_manager: parallel_run.ClusterManager,
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

        LOGGER.info("Waiting up to 4 epochs for first reward.")
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

        # transfer all funds from payment address back to faucet, so no funds are staked
        clusterlib_utils.return_funds_to_faucet(
            pool_user.payment,
            cluster_obj=cluster,
            faucet_addr=cluster_manager.cache.addrs_data["user1"]["payment"].address,
            tx_name=temp_template,
        )
        assert (
            cluster.get_address_balance(pool_user.payment.address) == 0
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        rewards_rec = []

        # keep withdrawing new rewards so reward balance is 0
        def _withdraw():
            rewards = cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance
            if rewards:
                epoch = cluster.get_last_block_epoch()
                payment_balance = cluster.get_address_balance(pool_user.payment.address)
                rewards_rec.append(rewards)
                LOGGER.info(f"epoch {epoch} - reward: {rewards}, payment: {payment_balance}")
                # TODO - check ledger state wrt stake amount and expected reward
                clusterlib_utils.save_ledger_state(
                    cluster_obj=cluster, name_template=f"{temp_template}_{epoch}"
                )
                # withdraw rewards to destination address
                clusterlib_utils.withdraw_reward(
                    cluster_obj=cluster,
                    pool_user=pool_user,
                    name_template=f"{temp_template}_ep{epoch}",
                    dst_addr_record=dst_addr_record,
                )

        LOGGER.info("Withdrawing new rewards for 4 epochs.")
        helpers.wait_for(
            _withdraw,
            delay=10,
            num_sec=4 * cluster.epoch_length_sec + 100,
            silent=True,
        )

        assert rewards_rec[-1] < rewards_rec[-2] // 3, "Rewards are not decreasing"

    @allure.link(helpers.get_vcs_link())
    def test_no_reward_unmet_pledge1(
        self,
        cluster_manager: parallel_run.ClusterManager,
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
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )

        LOGGER.info("Waiting up to 4 epochs for first reward.")
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
            pool_user.stake.address
        ).reward_account_balance

        cluster.wait_for_new_epoch(3)

        with cluster_manager.restart_on_failure():
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
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )

        LOGGER.info("Waiting up to 4 epochs for first reward.")
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

        with cluster_manager.restart_on_failure():
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

            # Return the pool to the original state - restore pledge funds.

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
    def test_no_reward_deregistered_stake_addr(
        self,
        cluster_manager: parallel_run.ClusterManager,
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
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )

        LOGGER.info("Waiting up to 4 epochs for first reward.")
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
        cluster.wait_for_new_block(new_blocks=2)

        with cluster_manager.restart_on_failure():
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
    def test_no_reward_deregistered_reward_addr(
        self,
        cluster_manager: parallel_run.ClusterManager,
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
        temp_template = helpers.get_func_name()

        # submit registration certificate and delegate to pool
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_name=pool_name,
        )

        LOGGER.info("Waiting up to 4 epochs for first reward.")
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

        # withdraw pool rewards to payment address
        clusterlib_utils.withdraw_reward(
            cluster_obj=cluster, pool_user=pool_reward, name_template=temp_template
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
        cluster.wait_for_new_block(new_blocks=2)

        with cluster_manager.restart_on_failure():
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

            # check that pool owner is NOT receiving rewards
            assert (
                orig_pool_reward
                == cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # check that new rewards are received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance
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
            cluster.wait_for_new_block(new_blocks=2)

            # check that the balance for source address was correctly updated
            assert (
                cluster.get_address_balance(pool_reward.payment.address)
                == src_updated_balance - tx_raw_output.fee - cluster.get_key_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            cluster.wait_for_new_epoch(4, padding_seconds=30)

            # check that new rewards were received by those delegating to the pool
            assert (
                orig_user_reward
                < cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance
            ), "New reward was not received by stake address"

            # check that pool owner is also receiving rewards
            assert (
                orig_pool_reward
                < cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance
            ), "New reward was not received by pool reward address"

    @allure.link(helpers.get_vcs_link())
    def test_deregister_reward_addr_retire_pool(
        self,
        cluster_manager: parallel_run.ClusterManager,
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
        pool_name = "node-pool2"
        cluster = cluster_lock_pool2

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        temp_template = helpers.get_func_name()

        LOGGER.info("Waiting up to 4 epochs for first reward.")
        stake_reward = helpers.wait_for(
            lambda: cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance,
            delay=10,
            num_sec=4 * cluster.epoch_length_sec + 100,
            message="receive rewards",
            silent=True,
        )
        if not stake_reward:
            cluster_manager.set_needs_restart()
            pytest.skip(f"Pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # withdraw pool rewards to payment address
        clusterlib_utils.withdraw_reward(
            cluster_obj=cluster, pool_user=pool_reward, name_template=temp_template
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
        cluster.wait_for_new_block(new_blocks=2)

        with cluster_manager.restart_on_failure():
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

            cluster.wait_for_new_epoch(3)

            # check that pool owner is NOT receiving rewards
            assert (
                orig_pool_reward
                == cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance
            ), "Pool owner received unexpected rewards"

            # fund source address
            clusterlib_utils.fund_from_faucet(
                pool_reward,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=150_000_000,
                force=True,
            )

            src_dereg_balance = cluster.get_address_balance(pool_owner.payment.address)
            stake_acount_balance = cluster.get_stake_addr_info(
                pool_owner.stake.address
            ).reward_account_balance
            reward_acount_balance = cluster.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance

            node_cold = pool_rec["cold_key_pair"]
            stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)

            # deregister stake pool
            __, tx_raw_output = cluster.deregister_stake_pool(
                pool_owners=[pool_owner],
                cold_key_pair=node_cold,
                epoch=cluster.get_last_block_epoch() + 1,
                pool_name=pool_name,
                tx_name=temp_template,
            )

            LOGGER.info("Waiting up to 3 epochs for stake pool to be deregistered.")
            stake_pool_id_dec = helpers.decode_bech32(stake_pool_id)
            helpers.wait_for(
                lambda: cluster.get_registered_stake_pools_ledger_state().get(stake_pool_id_dec)
                is None,
                delay=10,
                num_sec=3 * cluster.epoch_length_sec,
                message="deregister stake pool",
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

            node_cold = pool_rec["cold_key_pair"]
            stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)

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
            cluster.wait_for_new_block(new_blocks=2)

            # check that the balance for source address was correctly updated and that the
            # pool deposit was needed
            assert (
                cluster.get_address_balance(pool_reward.payment.address)
                == src_updated_balance
                - tx_raw_output.fee
                - cluster.get_pool_deposit()
                - cluster.get_key_deposit()
            ), f"Incorrect balance for source address `{pool_reward.payment.address}`"

            LOGGER.info("Waiting up to 5 epochs for stake pool to be reregistered.")
            helpers.wait_for(
                lambda: stake_pool_id in cluster.get_stake_distribution(),
                delay=10,
                num_sec=5 * cluster.epoch_length_sec,
                message="reregister stake pool",
            )

            # wait before checking delegation and rewards
            cluster.wait_for_new_epoch(3, padding_seconds=30)

            # check that the stake address was delegated
            stake_addr_info = cluster.get_stake_addr_info(pool_owner.stake.address)
            assert (
                stake_addr_info.delegation
            ), f"Stake address was not delegated yet: {stake_addr_info}"

            assert (
                stake_pool_id == stake_addr_info.delegation
            ), "Stake address delegated to wrong pool"

            # check that pool owner is receiving rewards
            assert cluster.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance, "New reward was not received by pool reward address"

    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="needs Allegra+ TX to run",
    )
    @allure.link(helpers.get_vcs_link())
    def test_2_pools_same_reward_addr(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster_lock_pool2: clusterlib.ClusterLib,
    ):
        """Check that one reward address used for two pools receives rewards for both of them.

        * set pool2 reward address to the reward address of pool1 by resubmitting the pool
          registration certificate
        * get combined reward amount per epoch for pool1 and pool2
        * check that the original reward address for pool2 is NOT receiving rewards
        * check that the reward address for pool1 is now receiving rewards for both pools
          by comparing reward amount received in last epoch with reward amount previously received
          by both pools together
        """
        pool_name = "node-pool2"
        cluster = cluster_lock_pool2
        temp_template = helpers.get_func_name()

        pool1_rec = cluster_manager.cache.addrs_data["node-pool1"]
        pool1_reward = clusterlib.PoolUser(payment=pool1_rec["payment"], stake=pool1_rec["reward"])

        pool2_rec = cluster_manager.cache.addrs_data[pool_name]
        pool2_owner = clusterlib.PoolUser(payment=pool2_rec["payment"], stake=pool2_rec["stake"])
        pool2_reward = clusterlib.PoolUser(payment=pool2_rec["payment"], stake=pool2_rec["reward"])

        # load pool data
        node_cold = pool2_rec["cold_key_pair"]
        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)
        loaded_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=stake_pool_id
        )

        LOGGER.info("Waiting up to 4 epochs for first reward.")
        stake_reward = helpers.wait_for(
            lambda: cluster.get_stake_addr_info(pool2_reward.stake.address).reward_account_balance,
            delay=10,
            num_sec=4 * cluster.epoch_length_sec + 100,
            message="receive rewards",
            silent=True,
        )
        if not stake_reward:
            cluster_manager.set_needs_restart()
            pytest.skip(f"Pool '{pool_name}' hasn't received any rewards, cannot continue.")

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
        cluster.send_tx(
            src_address=pool2_owner.payment.address,
            tx_name=f"{temp_template}_update_param",
            tx_files=tx_files,
            deposit=0,  # no additional deposit, the pool is already registered
        )
        cluster.wait_for_new_block(new_blocks=2)

        # pool configuration changed, restart needed
        cluster_manager.set_needs_restart()

        cluster.wait_for_new_epoch()

        # get combined reward amount per epoch for pool1 and pool2
        pool1_ep1_amount = cluster.get_stake_addr_info(
            pool1_reward.stake.address
        ).reward_account_balance
        pool2_ep1_amount = cluster.get_stake_addr_info(
            pool2_reward.stake.address
        ).reward_account_balance

        cluster.wait_for_new_epoch()

        pool1_ep2_amount = cluster.get_stake_addr_info(
            pool1_reward.stake.address
        ).reward_account_balance
        pool2_ep2_amount = cluster.get_stake_addr_info(
            pool2_reward.stake.address
        ).reward_account_balance
        combined_reward_per_epoch = (pool1_ep2_amount - pool1_ep1_amount) + (
            pool2_ep2_amount - pool2_ep1_amount
        )

        cluster.wait_for_new_epoch(3)

        # get reward amount once the reward address change for pool2 is completed
        pool1_done1_amount = cluster.get_stake_addr_info(
            pool1_reward.stake.address
        ).reward_account_balance
        pool2_done1_amount = cluster.get_stake_addr_info(
            pool2_reward.stake.address
        ).reward_account_balance

        cluster.wait_for_new_epoch()

        pool1_done2_amount = cluster.get_stake_addr_info(
            pool1_reward.stake.address
        ).reward_account_balance
        pool2_done2_amount = cluster.get_stake_addr_info(
            pool2_reward.stake.address
        ).reward_account_balance

        pool1_epoch_amount = pool1_done2_amount - pool1_done1_amount
        pool2_epoch_amount = pool2_done2_amount - pool2_done1_amount

        # check that the original reward address for pool2 is NOT receiving rewards
        assert pool2_epoch_amount == 0, "Pool reward address received unexpected rewards"

        # check that the reward address for pool1 is now receiving rewards for both pools
        # by comparing reward amount received in last epoch with reward amount previously received
        # by both pools together
        assert (
            pool1_epoch_amount >= combined_reward_per_epoch * 0.9
        ), "New reward was not received by pool reward address"
