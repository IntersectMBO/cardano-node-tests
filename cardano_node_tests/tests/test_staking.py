"""Tests for staking, rewards, blocks production on real block-producing pools."""
import logging
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


class DelegationOut(NamedTuple):
    pool_user: clusterlib.PoolUser
    pool_id: str
    tx_raw_output: clusterlib.TxRawOutput


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    p = Path(tmp_path_factory.getbasetemp()).joinpath(helpers.get_id_for_mktemp(__file__)).resolve()
    p.mkdir(exist_ok=True, parents=True)
    return p


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


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
        witness_override=len(tx_files_withdrawal.signing_key_files) * 2,
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
    # TODO: fee is not known when using `transaction build` command, assume withdrawal amount
    # is greater than fee
    src_reward_balance = cluster_obj.get_address_balance(dst_address)
    if src_reward_balance < src_init_balance:
        raise AssertionError(f"Incorrect balance for destination address `{dst_address}`.")

    return tx_raw_withdrawal_output


@pytest.fixture
def cluster_and_pool(
    cluster_manager: cluster_management.ClusterManager,
) -> Tuple[clusterlib.ClusterLib, str]:
    """Return instance of `clusterlib.ClusterLib`, and pool id to delegate to.

    We need to mark the pool as "in use" when requesting local cluster
    instance, that's why cluster instance and pool id are tied together in
    single fixture.
    """
    if cluster_nodes.ClusterType.TESTNET_NOPOOLS:
        cluster_obj: clusterlib.ClusterLib = cluster_manager.get()
        stake_dist = cluster_obj.get_stake_distribution()

        # select a pool with high stake distribution and reasonable margin
        s_pool_ids = sorted(stake_dist, key=lambda key: stake_dist[key])
        for pool_id in s_pool_ids:
            pool_params = cluster_obj.get_pool_params(pool_id)
            if pool_params.pool_params["margin"] <= 0.5 and not pool_params.retiring:
                break
        else:
            pytest.skip("Cannot find any usable pool.")
    else:
        cluster_obj = cluster_manager.get(use_resources=[cluster_management.Resources.POOL1])
        pool_id = _get_pool_id(
            cluster_obj=cluster_obj,
            addrs_data=cluster_manager.cache.addrs_data,
            pool_name="node-pool1",
        )
    return cluster_obj, pool_id


@pytest.fixture
def cluster_use_pool1(cluster_manager: cluster_management.ClusterManager) -> clusterlib.ClusterLib:
    return cluster_manager.get(use_resources=[cluster_management.Resources.POOL1])


@pytest.fixture
def cluster_lock_pool2(cluster_manager: cluster_management.ClusterManager) -> clusterlib.ClusterLib:
    return cluster_manager.get(lock_resources=[cluster_management.Resources.POOL2])


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
            name_template=f"test_staking_pool_users_ci{cluster_manager.cluster_instance_num}",
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


def _get_key_hashes(rec: dict) -> List[str]:
    """Get key hashes in ledger state snapshot record."""
    return [r[0]["key hash"] for r in rec]


def _get_val_for_key_hash(key_hash: str, rec: list) -> Any:
    """Get value for key hash in ledger state snapshot record."""
    for r in rec:
        if r[0]["key hash"] == key_hash:
            return r[1]
    return None


def _get_reward_for_key_hash(key_hash: str, rec: list) -> int:
    """Get reward value for key hash in ledger state snapshot record."""
    for r in rec:
        if r[0]["key hash"] != key_hash:
            continue
        rew_amount = 0
        for sr in r[1]:
            rew_amount += sr["rewardAmount"]
        return rew_amount
    return 0


def _get_pool_id(
    cluster_obj: clusterlib.ClusterLib,
    addrs_data: dict,
    pool_name: str,
) -> str:
    """Return stake pool id."""
    node_cold = addrs_data[pool_name]["cold_key_pair"]
    return cluster_obj.get_stake_pool_id(node_cold.vkey_file)


def _delegate_stake_add(
    cluster_obj: clusterlib.ClusterLib,
    addrs_data: dict,
    temp_template: str,
    pool_id: str = "",
    cold_vkey: Optional[Path] = None,
    amount: int = 100_000_000,
    check_delegation: bool = True,
    use_build_cmd: bool = False,
) -> DelegationOut:
    """Submit registration certificate and delegate to pool."""
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
    if pool_id:
        deleg_kwargs["stake_pool_id"] = pool_id
    elif cold_vkey:
        deleg_kwargs["cold_vkey_file"] = cold_vkey
        pool_id = cluster_obj.get_stake_pool_id(cold_vkey)

    stake_addr_deleg_cert_file = cluster_obj.gen_stake_addr_delegation_cert(**deleg_kwargs)

    # fund source address
    clusterlib_utils.fund_from_faucet(
        payment_addr_rec,
        cluster_obj=cluster_obj,
        faucet_data=addrs_data["user1"],
        amount=amount,
    )

    src_address = payment_addr_rec.address
    src_init_balance = cluster_obj.get_address_balance(src_address)

    # register stake address and delegate it to pool
    tx_files = clusterlib.TxFiles(
        certificate_files=[stake_addr_reg_cert_file, stake_addr_deleg_cert_file],
        signing_key_files=[payment_addr_rec.skey_file, stake_addr_rec.skey_file],
    )

    if use_build_cmd:
        tx_raw_output = cluster_obj.build_tx(
            src_address=src_address,
            tx_name=f"{temp_template}_reg_deleg",
            tx_files=tx_files,
            fee_buffer=2000_000,
            witness_override=len(tx_files.signing_key_files) * 2,
        )
        tx_signed = cluster_obj.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_reg_deleg",
        )
        cluster_obj.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
    else:
        tx_raw_output = cluster_obj.send_tx(
            src_address=src_address, tx_name=f"{temp_template}_reg_deleg", tx_files=tx_files
        )

    # check that the balance for source address was correctly updated
    # TODO: fee is not known when using `transaction build` command
    assert (
        cluster_obj.get_address_balance(src_address)
        < src_init_balance - cluster_obj.get_address_deposit()
    ), f"Incorrect balance for source address `{src_address}`"

    # check that the stake address was delegated
    if check_delegation:
        clusterlib_utils.wait_for_stake_distribution(cluster_obj)
        stake_addr_info = cluster_obj.get_stake_addr_info(stake_addr_rec.address)
        assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"
        assert pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

    return DelegationOut(pool_user=pool_user, pool_id=pool_id, tx_raw_output=tx_raw_output)


@pytest.mark.testnets
@pytest.mark.order(3)
class TestDelegateAddr:
    """Tests for address delegation to stake pools."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(
                    VERSIONS.transaction_era < VERSIONS.ALONZO, reason="runs only with Alonzo+ TX"
                ),
            ),
        ),
        ids=("build_raw", "build"),
    )
    @pytest.mark.dbsync
    def test_delegate_using_pool_id(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
        use_build_cmd: bool,
    ):
        """Submit registration certificate and delegate to pool using pool id.

        * register stake address and delegate it to pool
        * check that the stake address was delegated
        """
        cluster, pool_id = cluster_and_pool
        temp_template = f"{clusterlib_utils.get_temp_template(cluster)}_{use_build_cmd}"

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=0, stop=-10, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        # submit registration certificate and delegate to pool
        delegation_out = _delegate_stake_add(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
            use_build_cmd=use_build_cmd,
        )

        tx_db_record = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out.tx_raw_output
        )
        if tx_db_record:
            assert delegation_out.pool_user.stake.address in tx_db_record.stake_registration
            assert (
                delegation_out.pool_user.stake.address == tx_db_record.stake_delegation[0].address
            )
            assert tx_db_record.stake_delegation[0].active_epoch_no == init_epoch + 2
            assert delegation_out.pool_id == tx_db_record.stake_delegation[0].pool_id

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(
                    VERSIONS.transaction_era < VERSIONS.ALONZO, reason="runs only with Alonzo+ TX"
                ),
            ),
        ),
        ids=("build_raw", "build"),
    )
    @pytest.mark.dbsync
    @pytest.mark.skipif(
        cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.TESTNET_NOPOOLS,
        reason="supposed to run on cluster with pools",
    )
    def test_delegate_using_vkey(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_use_pool1: clusterlib.ClusterLib,
        use_build_cmd: bool,
    ):
        """Submit registration certificate and delegate to pool using cold vkey.

        * register stake address and delegate it to pool
        * check that the stake address was delegated
        """
        pool_name = "node-pool1"
        cluster = cluster_use_pool1
        temp_template = f"{clusterlib_utils.get_temp_template(cluster)}_{use_build_cmd}"

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=0, stop=-10, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        # submit registration certificate and delegate to pool
        node_cold = cluster_manager.cache.addrs_data[pool_name]["cold_key_pair"]
        delegation_out = _delegate_stake_add(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            cold_vkey=node_cold.vkey_file,
            use_build_cmd=use_build_cmd,
        )

        tx_db_record = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out.tx_raw_output
        )
        if tx_db_record:
            assert delegation_out.pool_user.stake.address in tx_db_record.stake_registration
            assert (
                delegation_out.pool_user.stake.address == tx_db_record.stake_delegation[0].address
            )
            assert tx_db_record.stake_delegation[0].active_epoch_no == init_epoch + 2
            assert delegation_out.pool_id == tx_db_record.stake_delegation[0].pool_id

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(2)
    @pytest.mark.dbsync
    @pytest.mark.long
    def test_deregister(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
    ):
        """Deregister stake address.

        * submit registration certificate and delegate to pool
        * attempt to deregister the stake address - deregistration is expected to fail
          because there are rewards in the stake address
        * withdraw rewards to payment address and deregister stake address
        * check that the key deposit was returned and rewards withdrawn
        * check that the stake address is no longer delegated
        """
        cluster, pool_id = cluster_and_pool
        temp_template = clusterlib_utils.get_temp_template(cluster)

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=0, stop=-10, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        # submit registration certificate and delegate to pool
        delegation_out = _delegate_stake_add(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        tx_db_deleg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out.tx_raw_output
        )
        if tx_db_deleg:
            assert delegation_out.pool_user.stake.address in tx_db_deleg.stake_registration
            assert delegation_out.pool_user.stake.address == tx_db_deleg.stake_delegation[0].address
            assert tx_db_deleg.stake_delegation[0].active_epoch_no == init_epoch + 2
            assert delegation_out.pool_id == tx_db_deleg.stake_delegation[0].pool_id

        clusterlib_utils.wait_for_stake_distribution(cluster)

        src_address = delegation_out.pool_user.payment.address

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

        # make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=0, stop=-40, force_epoch=False
        )

        # files for deregistering stake address
        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
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
            cluster.send_tx(
                src_address=src_address,
                tx_name=f"{temp_template}_dereg_fail",
                tx_files=tx_files_deregister,
            )
        assert "StakeKeyNonZeroAccountBalanceDELEG" in str(excinfo.value)

        src_payment_balance = cluster.get_address_balance(src_address)
        reward_balance = cluster.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance

        # withdraw rewards to payment address, deregister stake address
        tx_raw_deregister_output = cluster.send_tx(
            src_address=src_address,
            tx_name=f"{temp_template}_dereg_withdraw",
            tx_files=tx_files_deregister,
            withdrawals=[
                clusterlib.TxOut(address=delegation_out.pool_user.stake.address, amount=-1)
            ],
        )

        # check that the key deposit was returned and rewards withdrawn
        assert (
            cluster.get_address_balance(src_address)
            == src_payment_balance
            - tx_raw_deregister_output.fee
            + reward_balance
            + cluster.get_address_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        # check that the stake address is no longer delegated
        stake_addr_info = cluster.get_stake_addr_info(delegation_out.pool_user.stake.address)
        assert (
            not stake_addr_info.delegation
        ), f"Stake address is still delegated: {stake_addr_info}"

        tx_db_dereg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_raw_deregister_output
        )
        if tx_db_dereg:
            assert delegation_out.pool_user.stake.address in tx_db_dereg.stake_deregistration

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(
                    VERSIONS.transaction_era < VERSIONS.ALONZO, reason="runs only with Alonzo+ TX"
                ),
            ),
        ),
        ids=("build_raw", "build"),
    )
    @pytest.mark.dbsync
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
        """
        temp_template = f"{clusterlib_utils.get_temp_template(cluster)}_{use_build_cmd}"

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

        if use_build_cmd:
            tx_raw_output = cluster.build_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_reg_deleg",
                tx_files=tx_files,
                fee_buffer=2000_000,
                witness_override=len(tx_files.signing_key_files) * 2,
            )
            tx_signed = cluster.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_reg_deleg",
            )
            cluster.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
        else:
            tx_raw_output = cluster.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_reg_dereg",
                tx_files=tx_files,
            )

        # check that the balance for source address was correctly updated and that key deposit
        # was not needed
        # TODO: fee is not known when using `transaction build` command
        assert (
            cluster.get_address_balance(user_payment.address) < src_init_balance
        ), f"Incorrect balance for source address `{user_payment.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            assert user_registered.stake.address in tx_db_record.stake_registration
            assert user_registered.stake.address in tx_db_record.stake_deregistration

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(
                    VERSIONS.transaction_era < VERSIONS.ALONZO, reason="runs only with Alonzo+ TX"
                ),
            ),
        ),
        ids=("build_raw", "build"),
    )
    @pytest.mark.dbsync
    def test_addr_delegation_deregistration(
        self,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
        use_build_cmd: bool,
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
        cluster, pool_id = cluster_and_pool
        temp_template = f"{clusterlib_utils.get_temp_template(cluster)}_{use_build_cmd}"

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

        tx_db_reg = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_reg)
        if tx_db_reg:
            assert user_registered.stake.address in tx_db_reg.stake_registration

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output_reg.fee - cluster.get_address_deposit()
        ), f"Incorrect balance for source address `{user_payment.address}`"

        src_registered_balance = cluster.get_address_balance(user_payment.address)

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            stake_pool_id=pool_id,
        )

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=0, stop=-10, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

        # delegate and deregister stake address in single TX
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file, stake_addr_dereg_cert],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        if use_build_cmd:
            tx_raw_output_deleg = cluster.build_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_deleg_dereg",
                tx_files=tx_files,
                fee_buffer=2000_000,
                witness_override=len(tx_files.signing_key_files) * 2,
            )
            tx_signed = cluster.sign_tx(
                tx_body_file=tx_raw_output_deleg.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_deleg_dereg",
            )
            cluster.submit_tx(tx_file=tx_signed, txins=tx_raw_output_deleg.txins)
        else:
            tx_raw_output_deleg = cluster.send_tx(
                src_address=user_payment.address,
                tx_name=f"{temp_template}_deleg_dereg",
                tx_files=tx_files,
            )

        # check that the balance for source address was correctly updated and that the key
        # deposit was returned
        # TODO: fee is not known when using `transaction build` command
        expected_high_fee = 250000
        assert (
            cluster.get_address_balance(user_payment.address)
            >= src_registered_balance - expected_high_fee + cluster.get_address_deposit()
        ), f"Incorrect balance for source address `{user_payment.address}`"

        clusterlib_utils.wait_for_stake_distribution(cluster)

        # check that the stake address was NOT delegated
        stake_addr_info = cluster.get_stake_addr_info(user_registered.stake.address)
        assert not stake_addr_info.delegation, f"Stake address was delegated: {stake_addr_info}"

        tx_db_deleg = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_deleg)
        if tx_db_deleg:
            assert user_registered.stake.address in tx_db_deleg.stake_deregistration
            assert user_registered.stake.address == tx_db_deleg.stake_delegation[0].address
            assert tx_db_deleg.stake_delegation[0].active_epoch_no == init_epoch + 2
            assert pool_id == tx_db_deleg.stake_delegation[0].pool_id


@pytest.mark.testnets
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
        temp_template = clusterlib_utils.get_temp_template(cluster)

        # create stake address registration cert, use wrong stake vkey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.gen_stake_addr_registration_cert(
                addr_name=f"{temp_template}_addr0", stake_vkey_file=pool_users[0].payment.vkey_file
            )
        assert "Expected: StakeVerificationKeyShelley" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_delegation_cert_with_wrong_key(
        self,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to generate stake address delegation certificate using wrong stake vkey.

        Expect failure.
        """
        cluster, pool_id = cluster_and_pool
        temp_template = clusterlib_utils.get_temp_template(cluster)

        # create stake address delegation cert, use wrong stake vkey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.gen_stake_addr_delegation_cert(
                addr_name=f"{temp_template}_addr0",
                stake_vkey_file=pool_users[0].payment.vkey_file,
                stake_pool_id=pool_id,
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
        temp_template = clusterlib_utils.get_temp_template(cluster)

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
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
    ):
        """Try to delegate stake address using wrong payment skey.

        Expect failure.
        """
        cluster, pool_id = cluster_and_pool
        temp_template = clusterlib_utils.get_temp_template(cluster)

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

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            stake_pool_id=pool_id,
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
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(
                    VERSIONS.transaction_era < VERSIONS.ALONZO, reason="runs only with Alonzo+ TX"
                ),
            ),
        ),
        ids=("build_raw", "build"),
    )
    def test_delegate_unregistered_addr(
        self,
        cluster_and_pool: Tuple[clusterlib.ClusterLib, str],
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to delegate unregistered stake address.

        Expect failure.
        """
        cluster, pool_id = cluster_and_pool
        temp_template = f"{clusterlib_utils.get_temp_template(cluster)}_{use_build_cmd}"

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            stake_pool_id=pool_id,
        )

        # delegate unregistered stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file],
            signing_key_files=[user_payment.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                tx_raw_output = cluster.build_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_deleg_unreg",
                    tx_files=tx_files,
                    fee_buffer=2000_000,
                    witness_override=len(tx_files.signing_key_files) * 2,
                )
                tx_signed = cluster.sign_tx(
                    tx_body_file=tx_raw_output.out_file,
                    signing_key_files=tx_files.signing_key_files,
                    tx_name=f"{temp_template}_deleg_unreg",
                )
                cluster.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
            else:
                cluster.send_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_deleg_unreg",
                    tx_files=tx_files,
                )
        assert "StakeDelegationImpossibleDELEG" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(
                    VERSIONS.transaction_era < VERSIONS.ALONZO, reason="runs only with Alonzo+ TX"
                ),
            ),
        ),
        ids=("build_raw", "build"),
    )
    def test_deregister_not_registered_addr(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        pool_users_disposable: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Deregister not registered stake address."""
        temp_template = f"{clusterlib_utils.get_temp_template(cluster)}_{use_build_cmd}"

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
            if use_build_cmd:
                tx_raw_output = cluster.build_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_dereg_fail",
                    tx_files=tx_files,
                    fee_buffer=2000_000,
                    witness_override=len(tx_files.signing_key_files) * 2,
                )
                tx_signed = cluster.sign_tx(
                    tx_body_file=tx_raw_output.out_file,
                    signing_key_files=tx_files.signing_key_files,
                    tx_name=f"{temp_template}_dereg_fail",
                )
                cluster.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
            else:
                cluster.send_tx(
                    src_address=user_payment.address,
                    tx_name=f"{temp_template}_dereg_fail",
                    tx_files=tx_files,
                )
        assert "StakeKeyNonZeroAccountBalanceDELEG" in str(excinfo.value)


@pytest.mark.order(1)
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
        temp_template = clusterlib_utils.get_temp_template(cluster)
        # check pool rewards only when own pool is available
        check_pool_rewards = (
            cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.TESTNET
        )

        # make sure we have enough time to finish the registration/delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-300, force_epoch=False
        )

        if check_pool_rewards:
            pool_rec = cluster_manager.cache.addrs_data["node-pool1"]
            pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
            init_owner_rewards = cluster.get_stake_addr_info(
                pool_reward.stake.address
            ).reward_account_balance

        # submit registration certificate and delegate to pool
        delegation_out = _delegate_stake_add(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
            check_delegation=False,
        )

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

        * delegate to pool
        * create UTxOs with native tokens
        * collect data for pool owner and pool users for 9 epochs

           - each epoch check ledger state (expected data in `pstake*`, delegation, stake amount)
           - each epoch check received reward with reward in ledger state

        * withdraw rewards to payment address
        * burn native tokens
        """
        # pylint: disable=too-many-statements,too-many-locals,too-many-branches
        __: Any  # mypy workaround
        pool_name = "node-pool1"
        cluster = cluster_use_pool1

        temp_template = clusterlib_utils.get_temp_template(cluster)
        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        pool_reward = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["reward"])
        pool_reward_addr_dec = helpers.decode_bech32(pool_reward.stake.address)[2:]
        pool_stake_addr_dec = helpers.decode_bech32(pool_owner.stake.address)[2:]

        token_rand = clusterlib.get_rand_str(5)
        token_amount = 1_000_000

        # make sure we have enough time to finish the registration/delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-40, force_epoch=False
        )

        init_epoch = cluster.get_epoch()
        user_rewards = [(init_epoch, 0, 0)]
        owner_rewards = [
            (
                init_epoch,
                cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance,
                0,
            )
        ]

        # submit registration certificate and delegate to pool
        pool_id = _get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )
        delegation_out = _delegate_stake_add(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
            check_delegation=False,
        )

        native_tokens: List[clusterlib_utils.TokenRecord] = []
        if VERSIONS.transaction_era >= VERSIONS.MARY:
            # create native tokens UTxOs for pool user
            native_tokens = clusterlib_utils.new_tokens(
                *[f"couttscoin{token_rand}{i}" for i in range(5)],
                cluster_obj=cluster,
                temp_template=f"{temp_template}_{token_rand}",
                token_mint_addr=delegation_out.pool_user.payment,
                issuer_addr=delegation_out.pool_user.payment,
                amount=token_amount,
            )

        # make sure we managed to finish registration in the expected epoch
        assert (
            cluster.get_epoch() == init_epoch
        ), "Registration took longer than expected and would affect other checks"

        user_stake_addr_dec = helpers.decode_bech32(delegation_out.pool_user.stake.address)[2:]
        user_payment_balance = cluster.get_address_balance(delegation_out.pool_user.payment.address)

        # ledger state db
        rs_records: dict = {init_epoch: None}

        def _check_ledger_state(
            this_epoch: int,
            user_reward: int,
            prev_user_reward: int,
            abs_user_reward: int,
            abs_owner_reward: int,
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
            if abs_user_reward and prev_rs_record:
                assert abs_user_reward == _get_reward_for_key_hash(
                    user_stake_addr_dec, prev_rs_record
                )
            if abs_owner_reward and prev_rs_record:
                assert abs_owner_reward == _get_reward_for_key_hash(
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

            if this_epoch == init_epoch + 2:
                assert user_stake_addr_dec in pstake_mark
                assert user_stake_addr_dec in pstake_set
                assert user_stake_addr_dec not in pstake_go

            if this_epoch >= init_epoch + 2:
                assert pool_stake_addr_dec in pstake_mark
                assert pool_stake_addr_dec in pstake_set
                assert pool_stake_addr_dec in pstake_go

            if this_epoch >= init_epoch + 3:
                assert user_stake_addr_dec in pstake_mark
                assert user_stake_addr_dec in pstake_set
                assert user_stake_addr_dec in pstake_go

            # wait 4 epochs for first rewards
            if this_epoch >= init_epoch + 4:
                # make sure ledger state and actual stake correspond
                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["pstakeMark"]["stake"])
                    == user_reward + user_payment_balance
                )
                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["pstakeSet"]["stake"])
                    == prev_user_reward + user_payment_balance
                )
                assert (
                    _get_val_for_key_hash(user_stake_addr_dec, es_snapshot["pstakeGo"]["stake"])
                    == user_rewards[-3][1] + user_payment_balance
                )

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
            if cluster.get_epoch() == prev_owner_epoch:
                cluster.wait_for_new_epoch()

            # sleep till the end of epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=-19, stop=-15, check_slot=False
            )
            this_epoch = cluster.get_epoch()

            # current reward balances
            user_reward = cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
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

            # wait 4 epochs for first rewards
            if this_epoch >= init_epoch + 4:
                assert owner_reward > prev_owner_reward, "New reward was not received by pool owner"
                assert (
                    user_reward > prev_user_reward
                ), "New reward was not received by stake address"

            _check_ledger_state(
                this_epoch=this_epoch,
                user_reward=user_reward,
                prev_user_reward=prev_user_reward,
                abs_user_reward=abs_user_reward,
                abs_owner_reward=abs_owner_reward,
            )

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
        # TODO: Alonzo workaround
        try:
            clusterlib_utils.check_tx_view(cluster_obj=cluster, tx_raw_output=withdraw_out)
        except clusterlib.CLIError as err:
            if "friendlyTxBody: Alonzo not implemented yet" not in str(err):
                raise

        tx_db_record = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out.tx_raw_output
        )
        if tx_db_record:
            assert delegation_out.pool_user.stake.address in tx_db_record.stake_registration
            assert (
                delegation_out.pool_user.stake.address == tx_db_record.stake_delegation[0].address
            )
            assert tx_db_record.stake_delegation[0].active_epoch_no == init_epoch + 2
            assert delegation_out.pool_id == tx_db_record.stake_delegation[0].pool_id

            pool_user_db_record = dbsync_utils.check_address_reward(
                address=delegation_out.pool_user.stake.address, epoch_to=user_rewards[-1][0]
            )
            assert pool_user_db_record
            assert pool_user_db_record.pool_id == pool_id
            user_rewards_dict = {r[0]: r[2] for r in user_rewards if r[2]}
            user_db_rewards_dict = {
                r.spendable_epoch: r.amount for r in pool_user_db_record.rewards
            }
            assert user_rewards_dict == user_db_rewards_dict

            owner_rewards_db_record = dbsync_utils.check_address_reward(
                address=pool_reward.stake.address,
                epoch_from=owner_rewards[1][0],
                epoch_to=owner_rewards[-1][0],
            )
            assert owner_rewards_db_record
            assert owner_rewards_db_record.pool_id == pool_id
            owner_rewards_dict = {r[0]: r[2] for r in owner_rewards if r[2]}
            owner_db_rewards_dict = {
                r.spendable_epoch: r.amount for r in owner_rewards_db_record.rewards
            }
            assert owner_rewards_dict == owner_db_rewards_dict

    @allure.link(helpers.get_vcs_link())
    def test_reward_addr_delegation(  # noqa: C901
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool2: clusterlib.ClusterLib,
    ):
        """Check that the rewards address can be delegated and receive rewards.

        Tests https://github.com/input-output-hk/cardano-node/issues/1964

        The pool has a reward address that is different from pool owner's stake address.

        Collect data for pool owner for 10 epochs and:

        * delegate large amount of Lovelace to pool to make it more attractive
        * delegate reward address to pool
        * deregister pool owner's stake address
        * each epoch check ledger state (expected data in `pstake*`, delegation, stake amount)
        * each epoch check received reward with reward in ledger state
        * check that reward address still receives rewards for its staked amount even after
          the pool owner's stake address is deregistered
        """
        # pylint: disable=too-many-statements,too-many-locals
        pool_name = "node-pool2"
        cluster = cluster_lock_pool2

        temp_template = clusterlib_utils.get_temp_template(cluster)
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

        pool_id = _get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

        # delegate to pool to make the pool more attractive after the owner's
        # stake address is deregistered
        _delegate_stake_add(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
            amount=500_000_000_000,
            check_delegation=False,
        )

        # load and update original pool data
        loaded_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=pool_id
        )
        pool_data_updated = loaded_data._replace(pool_pledge=0)

        # make sure we have enough time to update the pool parameters
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-40, force_epoch=False
        )
        init_epoch = cluster.get_epoch()

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

        owner_rewards = [
            (
                init_epoch,
                cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance,
                0,
            )
        ]

        # make sure we managed to finish pool update in the expected epoch
        assert (
            cluster.get_epoch() == init_epoch
        ), "Pool update took longer than expected and would affect other checks"

        # ledger state db
        rs_records: dict = {init_epoch: None}

        def _check_ledger_state(
            this_epoch: int,
            owner_reward: int,
            prev_owner_reward: int,
            abs_owner_reward: int,
            owner_rewards: list,
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
            if abs_owner_reward and prev_rs_record:
                assert abs_owner_reward == _get_reward_for_key_hash(reward_addr_dec, prev_rs_record)

            pstake_mark = _get_key_hashes(es_snapshot["pstakeMark"]["stake"])
            pstake_set = _get_key_hashes(es_snapshot["pstakeSet"]["stake"])
            pstake_go = _get_key_hashes(es_snapshot["pstakeGo"]["stake"])

            if this_epoch == init_epoch + 2:
                assert reward_addr_dec not in pstake_mark
                assert stake_addr_dec in pstake_mark

            if this_epoch == init_epoch + 3:
                assert reward_addr_dec in pstake_mark
                assert reward_addr_dec not in pstake_set
                assert reward_addr_dec not in pstake_go

                assert stake_addr_dec not in pstake_mark
                assert stake_addr_dec in pstake_set
                assert stake_addr_dec in pstake_go

            if this_epoch == init_epoch + 4:
                assert reward_addr_dec in pstake_mark
                assert reward_addr_dec in pstake_set
                assert reward_addr_dec not in pstake_go

                assert stake_addr_dec not in pstake_mark
                assert stake_addr_dec not in pstake_set
                assert stake_addr_dec in pstake_go

            if this_epoch >= init_epoch + 5:
                assert reward_addr_dec in pstake_mark
                assert reward_addr_dec in pstake_set
                assert reward_addr_dec in pstake_go

                assert stake_addr_dec not in pstake_mark
                assert stake_addr_dec not in pstake_set
                assert stake_addr_dec not in pstake_go

                # make sure ledger state and actual stake correspond
                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeMark"]["stake"])
                    == owner_reward
                )
                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeSet"]["stake"])
                    == prev_owner_reward
                )
                assert (
                    _get_val_for_key_hash(reward_addr_dec, es_snapshot["pstakeGo"]["stake"])
                    == owner_rewards[-3][1]
                )

        LOGGER.info("Checking rewards for 9 epochs.")
        for __ in range(9):
            # reward balances in previous epoch
            (
                prev_epoch,
                prev_owner_reward,
                __,  # prev_abs_owner_reward
            ) = owner_rewards[-1]

            # wait for new epoch
            if cluster.get_epoch() == prev_epoch:
                cluster.wait_for_new_epoch()

            this_epoch = cluster.get_epoch()

            if this_epoch == init_epoch + 2:
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

                # make sure we managed to finish deregistration in the expected epoch
                assert (
                    cluster.get_epoch() == this_epoch
                ), "Deregistration took longer than expected and would affect other checks"

            # sleep till the end of epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=-19, stop=-15, check_slot=False
            )

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

            _check_ledger_state(
                this_epoch=this_epoch,
                owner_reward=owner_reward,
                prev_owner_reward=prev_owner_reward,
                abs_owner_reward=abs_owner_reward,
                owner_rewards=owner_rewards,
            )

            if this_epoch >= init_epoch + 6:
                # check that no reward for owner's stake address is received
                ep6_abs_owner_reward = owner_rewards[5][2]
                assert (
                    abs_owner_reward < ep6_abs_owner_reward
                ), "Received higher reward than expected"

        # check that some reward was received just for the delegated reward amount
        owner_reward = cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance
        ep6_owner_reward = owner_rewards[5][1]
        if owner_reward == ep6_owner_reward:
            LOGGER.info("Waiting up to 4 full epochs for first reward for delegated reward amount.")
            for i in range(5):
                if i > 0:
                    cluster.wait_for_new_epoch(padding_seconds=10)
                if cluster.get_stake_addr_info(pool_reward.stake.address).reward_account_balance:
                    break
            else:
                raise AssertionError("Haven't received any reward for delegated reward address")

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

        temp_template = clusterlib_utils.get_temp_template(cluster)

        # submit registration certificate and delegate to pool
        pool_id = _get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )
        delegation_out = _delegate_stake_add(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
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

        LOGGER.info("Waiting up to 4 full epochs for first reward.")
        for i in range(5):
            if i > 0:
                cluster.wait_for_new_epoch(padding_seconds=10)
            if cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance:
                break
        else:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish the transfer in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=0, stop=-40, force_epoch=False
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
        temp_template = clusterlib_utils.get_temp_template(cluster)

        pool_id = _get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

        # submit registration certificate and delegate to pool
        delegation_out = _delegate_stake_add(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        LOGGER.info("Waiting up to 4 full epochs for first reward.")
        for i in range(5):
            if i > 0:
                cluster.wait_for_new_epoch(padding_seconds=10)
            if cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance:
                break
        else:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish the pool update in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=0, stop=-40, force_epoch=False
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
        temp_template = clusterlib_utils.get_temp_template(cluster)

        pool_id = _get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

        # submit registration certificate and delegate to pool
        delegation_out = _delegate_stake_add(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        LOGGER.info("Waiting up to 4 full epochs for first reward.")
        for i in range(5):
            if i > 0:
                cluster.wait_for_new_epoch(padding_seconds=10)
            if cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance:
                break
        else:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to withdraw the pledge in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=0, stop=-40, force_epoch=False
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
        temp_template = clusterlib_utils.get_temp_template(cluster)

        pool_id = _get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )

        # submit registration certificate and delegate to pool
        delegation_out = _delegate_stake_add(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        LOGGER.info("Waiting up to 4 full epochs for first reward.")
        for i in range(5):
            if i > 0:
                cluster.wait_for_new_epoch(padding_seconds=10)
            if cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance:
                break
        else:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=0, stop=-40, force_epoch=False
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
        temp_template = clusterlib_utils.get_temp_template(cluster)

        # submit registration certificate and delegate to pool
        pool_id = _get_pool_id(
            cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pool_name
        )
        delegation_out = _delegate_stake_add(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        LOGGER.info("Waiting up to 4 full epochs for first reward.")
        for i in range(5):
            if i > 0:
                cluster.wait_for_new_epoch(padding_seconds=10)
            if cluster.get_stake_addr_info(
                delegation_out.pool_user.stake.address
            ).reward_account_balance:
                break
        else:
            pytest.skip(f"User of pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=0, stop=-40, force_epoch=False
        )

        # withdraw pool rewards to payment address
        # use `transaction build` if possible
        # TODO: disabled until BUG https://github.com/input-output-hk/cardano-node/issues/3074
        # is fixed
        # pylint: disable=condition-evals-to-constant
        if (
            False
            and VERSIONS.transaction_era >= VERSIONS.ALONZO
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
        temp_template = clusterlib_utils.get_temp_template(cluster)

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
            cluster_obj=cluster, start=0, stop=-40, force_epoch=False
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
                raise AssertionError(f"Stake pool `{pool_id}` not registered even after 5 epochs")

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
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="needs Allegra+ TX to run",
    )
    def test_2_pools_same_reward_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool2: clusterlib.ClusterLib,
    ):
        """Check that one reward address used for two pools receives rewards for both of them.

        * get combined reward amount per epoch for pool1 and pool2
        * set pool2 reward address to the reward address of pool1 by resubmitting the pool
          registration certificate
        * check that the original reward address for pool2 is NOT receiving rewards
        * check that the reward address for pool1 is now receiving rewards for both pools
          by comparing reward amount received in last epoch with reward amount previously received
          by both pools together
        """
        pool_name = "node-pool2"
        cluster = cluster_lock_pool2
        temp_template = clusterlib_utils.get_temp_template(cluster)

        pool1_rec = cluster_manager.cache.addrs_data["node-pool1"]
        pool1_reward = clusterlib.PoolUser(payment=pool1_rec["payment"], stake=pool1_rec["reward"])

        pool2_rec = cluster_manager.cache.addrs_data[pool_name]
        pool2_owner = clusterlib.PoolUser(payment=pool2_rec["payment"], stake=pool2_rec["stake"])
        pool2_reward = clusterlib.PoolUser(payment=pool2_rec["payment"], stake=pool2_rec["reward"])

        # load pool data
        node_cold = pool2_rec["cold_key_pair"]
        pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)
        loaded_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster, pool_name=f"changed_{pool_name}", pool_id=pool_id
        )

        LOGGER.info("Waiting up to 4 full epochs for first reward.")
        for i in range(5):
            if i > 0:
                cluster.wait_for_new_epoch(padding_seconds=10)
            if cluster.get_stake_addr_info(pool2_reward.stake.address).reward_account_balance:
                break
        else:
            pytest.skip(f"Pool '{pool_name}' hasn't received any rewards, cannot continue.")

        # get combined reward amount per epoch for pool1 and pool2
        pool1_amount_prev = 0
        pool2_amount_prev = 0
        combined_reward_per_epoch = 0
        for __ in range(3):
            pool1_amount = cluster.get_stake_addr_info(
                pool1_reward.stake.address
            ).reward_account_balance
            pool2_amount = cluster.get_stake_addr_info(
                pool2_reward.stake.address
            ).reward_account_balance

            # make sure both pools received reward in this epoch
            if (
                pool1_amount_prev
                and pool2_amount_prev
                and pool1_amount > pool1_amount_prev
                and pool2_amount > pool2_amount_prev
            ):
                combined_reward_per_epoch = (pool1_amount - pool1_amount_prev) + (
                    pool2_amount - pool2_amount_prev
                )
                break

            pool1_amount_prev = pool1_amount
            pool2_amount_prev = pool2_amount
            cluster.wait_for_new_epoch()

        assert combined_reward_per_epoch > 0, "Failed to get combined reward amount"

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

        # pool configuration changed, restart needed
        cluster_manager.set_needs_restart()

        cluster.wait_for_new_epoch(4)

        # check reward amount once the reward address change for pool2 is completed
        pool1_amount_prev = 0
        pool2_amount_prev = 0
        for __ in range(3):
            pool1_amount = cluster.get_stake_addr_info(
                pool1_reward.stake.address
            ).reward_account_balance
            pool2_amount = cluster.get_stake_addr_info(
                pool2_reward.stake.address
            ).reward_account_balance

            if pool1_amount_prev and pool2_amount_prev and pool1_amount > pool1_amount_prev:
                pool1_epoch_amount = pool1_amount - pool1_amount_prev
                pool2_epoch_amount = pool2_amount - pool2_amount_prev

                # check that the original reward address for pool2 is NOT receiving rewards
                assert pool2_epoch_amount == 0, "Pool reward address received unexpected rewards"

                # check that the reward address for pool1 is now receiving rewards
                # for both pools by comparing reward amount received in last epoch
                # with reward amount previously received by both pools together
                if pool1_epoch_amount >= combined_reward_per_epoch * 0.75:
                    break

            pool1_amount_prev = pool1_amount
            pool2_amount_prev = pool2_amount
            cluster.wait_for_new_epoch()
        else:
            raise AssertionError("New reward was not received by pool reward address")
