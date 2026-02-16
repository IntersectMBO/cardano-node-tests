"""Tests for stake address delegation."""

import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def cluster_and_pool(
    cluster_manager: cluster_management.ClusterManager,
) -> tuple[clusterlib.ClusterLib, str]:
    return delegation.cluster_and_pool(cluster_manager=cluster_manager)


@pytest.fixture
def cluster_and_pool_and_rewards(
    cluster_manager: cluster_management.ClusterManager,
) -> tuple[clusterlib.ClusterLib, str]:
    return delegation.cluster_and_pool(
        cluster_manager=cluster_manager, use_resources=[cluster_management.Resources.REWARDS]
    )


@pytest.fixture
def cluster_and_two_pools(
    cluster_manager: cluster_management.ClusterManager,
) -> tuple[clusterlib.ClusterLib, str, str]:
    """Return instance of `clusterlib.ClusterLib` and two pools."""
    cluster_obj = cluster_manager.get(
        use_resources=[
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
        ]
    )
    pool_names = cluster_manager.get_used_resources(from_set=cluster_management.Resources.ALL_POOLS)
    pool_ids = [
        delegation.get_pool_id(
            cluster_obj=cluster_obj,
            addrs_data=cluster_manager.cache.addrs_data,
            pool_name=p,
        )
        for p in pool_names
    ]
    assert len(pool_ids) == 2, "Expecting two pools"
    return cluster_obj, pool_ids[0], pool_ids[1]


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
        name_template=f"{test_id}_disposable",
        no_of_addr=2,
    )
    return pool_users


@pytest.fixture
def pool_users_cluster_and_pool(
    cluster_manager: cluster_management.ClusterManager,
    cluster_and_pool: tuple[clusterlib.ClusterLib, str],
) -> list[clusterlib.PoolUser]:
    """Create pool users using `cluster_and_pool` fixture.

    .. warning::
       The cached addresses can be used only for payments, not for delegation!
       The pool can be different every time the fixture is called.
    """
    cluster, *__ = cluster_and_pool
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
def pool_users_disposable_cluster_and_pool(
    cluster_and_pool: tuple[clusterlib.ClusterLib, str],
) -> list[clusterlib.PoolUser]:
    """Create function scoped pool users using `cluster_and_pool` fixture."""
    cluster, *__ = cluster_and_pool
    test_id = common.get_test_id(cluster)
    pool_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster,
        name_template=f"{test_id}_disposable_cap",
        no_of_addr=2,
    )
    return pool_users


class TestDelegateAddr:
    """Tests for stake address delegation."""

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.dbsync
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_delegate_using_pool_id(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_and_pool: tuple[clusterlib.ClusterLib, str],
        build_method: str,
    ):
        """Submit registration certificate and delegate to pool using pool id.

        Uses parametrized build method (build or build-raw).

        * create payment and stake address
        * fund payment address
        * generate stake address registration and delegation certificates using pool id
        * submit registration and delegation certificates in single transaction
        * check that the stake address was delegated to the expected pool
        * check that the stake address is active and registered
        * (optional) check records in db-sync
        """
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # Submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
            build_method=build_method,
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
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.dbsync
    @pytest.mark.smoke
    def test_delegate_using_vkey(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_use_pool: tuple[clusterlib.ClusterLib, str],
        build_method: str,
    ):
        """Submit registration certificate and delegate to pool using cold vkey.

        Uses parametrized build method (build or build-raw). Delegates using pool cold vkey file
        instead of pool id.

        * create payment and stake address
        * fund payment address
        * generate stake address registration and delegation certificates using pool cold vkey
        * submit registration and delegation certificates in single transaction
        * check that the stake address was delegated to the expected pool
        * check that the stake address is active and registered
        * (optional) check records in db-sync
        """
        cluster, pool_name = cluster_use_pool
        temp_template = common.get_test_id(cluster)

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # Submit registration certificate and delegate to pool
        node_cold = cluster_manager.cache.addrs_data[pool_name]["cold_key_pair"]
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            cold_vkey=node_cold.vkey_file,
            build_method=build_method,
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
    @pytest.mark.long
    def test_multi_delegation(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_and_two_pools: tuple[clusterlib.ClusterLib, str, str],
    ):
        """Delegate multiple stake addresses that share the same payment keys to multiple pools.

        Test that multiple stake addresses using the same payment key can be delegated to different
        pools and all receive rewards independently.

        * create 1 payment vkey/skey key pair
        * create 4 stake vkey/skey key pairs
        * create 4 payment addresses for each combination of payment_vkey/stake_vkey
        * fund the payment addresses
        * generate stake address registration certificates for all 4 stake addresses
        * generate delegation certificates for the stake addresses (alternating between 2 pools)
        * submit registration and delegation certificates in single transaction
        * check that all stake addresses are delegated to their respective pools
        * check that the balance for source address was correctly updated
        * wait 4 epochs for first rewards
        * check that all stake addresses received rewards from their respective pools
        """
        cluster, pool1_id, pool2_id = cluster_and_two_pools
        temp_template = common.get_test_id(cluster)

        # Step: Create 1 payment vkey/skey key pair

        payment_key_pair = cluster.g_address.gen_payment_key_pair(
            key_name=f"{temp_template}_payment"
        )

        # Step: Create 4 stake vkey/skey key pairs

        stake_addr_recs = clusterlib_utils.create_stake_addr_records(
            *[f"{temp_template}_{i}" for i in range(4)],
            cluster_obj=cluster,
        )

        # Step: Create 1 payment addresses for each combination of payment_vkey/stake_vkey

        def _get_pool_users(
            name: str, stake_addr_rec: clusterlib.AddressRecord
        ) -> clusterlib.PoolUser:
            addr = cluster.g_address.gen_payment_addr(
                addr_name=f"{name}_addr",
                payment_vkey_file=payment_key_pair.vkey_file,
                stake_vkey_file=stake_addr_rec.vkey_file,
            )
            addr_rec = clusterlib.AddressRecord(
                address=addr,
                vkey_file=payment_key_pair.vkey_file,
                skey_file=payment_key_pair.skey_file,
            )
            pool_user = clusterlib.PoolUser(payment=addr_rec, stake=stake_addr_rec)

            return pool_user

        pool_users = [
            _get_pool_users(
                name=f"{temp_template}_{i}",
                stake_addr_rec=s,
            )
            for i, s in enumerate(stake_addr_recs)
        ]

        # Fund payment addresses
        clusterlib_utils.fund_from_faucet(
            *[u.payment for u in pool_users],
            cluster_obj=cluster,
            all_faucets=cluster_manager.cache.addrs_data,
            amount=200_000_000,
        )

        # Step: Delegate the stake addresses to 2 different pools

        # Create registration certificates
        stake_addr_reg_cert_files = [
            cluster.g_stake_address.gen_stake_addr_registration_cert(
                addr_name=f"{temp_template}_{i}_addr",
                deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster),
                stake_vkey_file=pu.stake.vkey_file,
            )
            for i, pu in enumerate(pool_users)
        ]

        def _get_pool_id(idx: int) -> str:
            return pool1_id if idx % 2 == 0 else pool2_id

        delegation_map = [(pu, _get_pool_id(i)) for i, pu in enumerate(pool_users)]

        # Create delegation certificates to different pool for each stake address
        stake_addr_deleg_cert_files = [
            cluster.g_stake_address.gen_stake_and_vote_delegation_cert(
                addr_name=f"{temp_template}_{i}_addr",
                stake_vkey_file=d[0].stake.vkey_file,
                stake_pool_id=d[1],
                always_abstain=True,
            )
            for i, d in enumerate(delegation_map)
        ]

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # Submit registration and delegation certificates
        src_address = pool_users[0].payment.address
        tx_files = clusterlib.TxFiles(
            certificate_files=[*stake_addr_reg_cert_files, *stake_addr_deleg_cert_files],
            signing_key_files=[
                *(pu.payment.skey_file for pu in pool_users),
                *(pu.stake.skey_file for pu in pool_users),
            ],
        )
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=src_address,
            tx_name=f"{temp_template}_reg_deleg",
            tx_files=tx_files,
        )

        assert cluster.g_query.get_epoch() == init_epoch, (
            "Delegation took longer than expected and would affect other checks"
        )

        # Check that the stake addresses are delegated
        for deleg_rec in delegation_map:
            stake_addr_info = cluster.g_query.get_stake_addr_info(deleg_rec[0].stake.address)
            assert stake_addr_info.delegation, (
                f"Stake address was not delegated yet: {stake_addr_info}"
            )
            assert deleg_rec[1] == stake_addr_info.delegation, (
                "Stake address delegated to wrong pool"
            )

        # Check that the balance for source address was correctly updated
        deposit = cluster.g_query.get_address_deposit()
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[
            0
        ].amount == clusterlib.calculate_utxos_balance(
            tx_raw_output.txins
        ) - tx_raw_output.fee - deposit * len(stake_addr_deleg_cert_files), (
            f"Incorrect balance for source address `{src_address}`"
        )

        # Step: Check that the stake addresses received rewards

        LOGGER.info("Waiting 4 epochs for first rewards.")
        cluster.wait_for_epoch(epoch_no=init_epoch + 4, padding_seconds=10)

        failures = [
            f"Address '{d[0].stake.address}' delegated to pool '{d[1]}' hasn't received rewards."
            for d in delegation_map
            if not cluster.g_query.get_stake_addr_info(d[0].stake.address).reward_account_balance
        ]
        if failures:
            raise AssertionError("\n".join(failures))

        # TODO: check delegation in db-sync

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(7)
    @pytest.mark.dbsync
    @pytest.mark.long
    def test_deregister_delegated(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_and_pool_and_rewards: tuple[clusterlib.ClusterLib, str],
    ):
        """Deregister a delegated stake address.

        * create two payment addresses that share single stake address
        * register and delegate the stake address to pool
        * attempt to deregister the stake address - deregistration is expected to fail
          because there are rewards in the stake address
        * withdraw rewards to payment address and deregister stake address
        * check that the key deposit was returned and rewards withdrawn
        * check that the stake address is no longer delegated
        * (optional) check records in db-sync
        """
        cluster, pool_id = cluster_and_pool_and_rewards
        temp_template = common.get_test_id(cluster)

        # Create two payment addresses that share single stake address (just to test that
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

        # Fund payment address
        clusterlib_utils.fund_from_faucet(
            *payment_addr_recs,
            cluster_obj=cluster,
            all_faucets=cluster_manager.cache.addrs_data,
            amount=200_000_000,
        )

        pool_user = clusterlib.PoolUser(payment=payment_addr_recs[1], stake=stake_addr_rec)

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # Submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_user=pool_user,
            pool_id=pool_id,
        )

        assert cluster.g_query.get_epoch() == init_epoch, (
            "Delegation took longer than expected and would affect other checks"
        )

        tx_db_deleg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=delegation_out.tx_raw_output
        )
        if tx_db_deleg:
            # Check in db-sync that both payment addresses share single stake address
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
        cluster.wait_for_epoch(epoch_no=init_epoch + 4, padding_seconds=10)
        assert cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance, f"User of pool '{pool_id}' hasn't received any rewards"

        # Make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # Files for deregistering stake address
        stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster),
            stake_vkey_file=delegation_out.pool_user.stake.vkey_file,
        )
        tx_files_deregister = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[
                delegation_out.pool_user.payment.skey_file,
                delegation_out.pool_user.stake.skey_file,
            ],
        )

        # Attempt to deregister the stake address - deregistration is expected to fail
        # because there are rewards in the stake address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=src_address,
                tx_name=f"{temp_template}_dereg_fail",
                tx_files=tx_files_deregister,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "StakeKeyNonZeroAccountBalanceDELEG" in exc_value
                or "StakeKeyHasNonZeroRewardAccountBalanceDELEG" in exc_value
            ), exc_value

        src_payment_balance = cluster.g_query.get_address_balance(src_address)
        reward_balance = cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance

        # Withdraw rewards to payment address, deregister stake address
        tx_raw_deregister_output = cluster.g_transaction.send_tx(
            src_address=src_address,
            tx_name=f"{temp_template}_dereg_withdraw",
            tx_files=tx_files_deregister,
            withdrawals=[
                clusterlib.TxOut(address=delegation_out.pool_user.stake.address, amount=-1)
            ],
        )

        # Check that the key deposit was returned and rewards withdrawn
        assert (
            cluster.g_query.get_address_balance(src_address)
            == src_payment_balance
            - tx_raw_deregister_output.fee
            + reward_balance
            + cluster.g_query.get_address_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        # Check that the stake address is no longer delegated
        stake_addr_info = cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        )
        assert not stake_addr_info.delegation, (
            f"Stake address is still delegated: {stake_addr_info}"
        )

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
    def test_delegate_multisig(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_and_pool_and_rewards: tuple[clusterlib.ClusterLib, str],
    ):
        """Delegate and deregister multisig stake address.

        Test delegation and deregistration of a stake address that uses a multisig script as
        stake credentials. All required signers must sign the transactions.

        * create 5 stake key pairs
        * create multisig script requiring all signatures (all type)
        * create payment address using multisig script as stake credentials
        * create script stake address using multisig script
        * fund payment address
        * generate stake address registration and delegation certificates
        * sign and submit registration and delegation transaction with all required signatures
        * check that the stake address is registered and delegated to pool
        * wait 4 epochs for first reward
        * attempt to deregister stake address with rewards - expect failure
        * withdraw rewards to payment address and deregister stake address
        * check that the key deposit was returned and rewards withdrawn
        * check that the stake address is no longer delegated
        * (optional) check records in db-sync
        """
        cluster, pool_id = cluster_and_pool_and_rewards
        temp_template = common.get_test_id(cluster)

        stake_key_recs = [
            cluster.g_stake_address.gen_stake_key_pair(key_name=f"{temp_template}_sig_{i}")
            for i in range(1, 6)
        ]
        stake_skey_files = [r.skey_file for r in stake_key_recs]
        stake_vkey_files = [r.vkey_file for r in stake_key_recs]

        # Create a multisig script to be used as stake credentials
        multisig_script = clusterlib_utils.build_stake_multisig_script(
            cluster_obj=cluster,
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            stake_vkey_files=stake_vkey_files,
        )

        pool_user = delegation.PoolUserScript(
            payment=cluster.g_address.gen_payment_addr_and_keys(
                name=f"{temp_template}_script_pool_user",
                stake_script_file=multisig_script,
            ),
            # Create script address
            stake=delegation.AddressRecordScript(
                address=cluster.g_stake_address.gen_stake_addr(
                    addr_name=temp_template, stake_script_file=multisig_script
                ),
                script_file=multisig_script,
            ),
        )

        # Fund payment address
        clusterlib_utils.fund_from_faucet(
            pool_user.payment,
            cluster_obj=cluster,
            all_faucets=cluster_manager.cache.addrs_data,
            amount=200_000_000,
        )

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # Submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_multisig_stake_addr(
            cluster_obj=cluster,
            temp_template=temp_template,
            pool_user=pool_user,
            skey_files=stake_skey_files,
            pool_id=pool_id,
        )

        assert cluster.g_query.get_epoch() == init_epoch, (
            "Delegation took longer than expected and would affect other checks"
        )

        src_address = delegation_out.pool_user.payment.address

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_epoch(epoch_no=init_epoch + 4, padding_seconds=10)
        assert cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance, f"User of pool '{pool_id}' hasn't received any rewards"

        # Make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # Files for deregistering stake address
        stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster),
            stake_script_file=delegation_out.pool_user.stake.script_file,
        )
        dereg_cert_script = clusterlib.ComplexCert(
            certificate_file=stake_addr_dereg_cert,
            script_file=delegation_out.pool_user.stake.script_file,
        )
        tx_files_deregister = clusterlib.TxFiles(
            signing_key_files=[
                delegation_out.pool_user.payment.skey_file,
                *stake_skey_files,
            ],
        )

        # Attempt to deregister the stake address - deregistration is expected to fail
        # because there are rewards in the stake address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=src_address,
                tx_name=f"{temp_template}_dereg_fail",
                tx_files=tx_files_deregister,
                complex_certs=[dereg_cert_script],
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "StakeKeyNonZeroAccountBalanceDELEG" in exc_value
                or "StakeKeyHasNonZeroRewardAccountBalanceDELEG" in exc_value
            ), exc_value

        src_payment_balance = cluster.g_query.get_address_balance(src_address)
        reward_balance = cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance

        # Withdraw rewards to payment address, deregister stake address
        tx_raw_deregister_output = cluster.g_transaction.send_tx(
            src_address=src_address,
            tx_name=f"{temp_template}_dereg_withdraw",
            tx_files=tx_files_deregister,
            complex_certs=[dereg_cert_script],
            withdrawals=[
                clusterlib.TxOut(address=delegation_out.pool_user.stake.address, amount=-1)
            ],
        )

        # Check that the key deposit was returned and rewards withdrawn
        assert (
            cluster.g_query.get_address_balance(src_address)
            == src_payment_balance
            - tx_raw_deregister_output.fee
            + reward_balance
            + cluster.g_query.get_address_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        # Check that the stake address is no longer delegated
        stake_addr_info = cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        )
        assert not stake_addr_info.delegation, (
            f"Stake address is still delegated: {stake_addr_info}"
        )

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
        cluster_and_pool_and_rewards: tuple[clusterlib.ClusterLib, str],
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
        cluster, pool_id = cluster_and_pool_and_rewards
        temp_template = common.get_test_id(cluster)

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # Submit registration certificate and delegate to pool
        delegation_out = delegation.delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            temp_template=temp_template,
            pool_id=pool_id,
        )

        assert cluster.g_query.get_epoch() == init_epoch, (
            "Delegation took longer than expected and would affect other checks"
        )

        # Check records in db-sync
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
        undeleg_epoch = cluster.wait_for_epoch(epoch_no=init_epoch + 4, padding_seconds=10)
        assert cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance, f"User of pool '{pool_id}' hasn't received any rewards"

        # Files for deregistering / re-registering stake address
        address_deposit = common.get_conway_address_deposit(cluster_obj=cluster)

        stake_addr_dereg_cert_file = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_undeleg_addr0",
            deposit_amt=address_deposit,
            stake_vkey_file=delegation_out.pool_user.stake.vkey_file,
        )
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_undeleg_addr0",
            deposit_amt=address_deposit,
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

        # Withdraw rewards to payment address; deregister and re-register stake address
        tx_raw_undeleg = cluster.g_transaction.send_tx(
            src_address=src_address,
            tx_name=f"{temp_template}_undeleg_withdraw",
            tx_files=tx_files_undeleg,
            withdrawals=[
                clusterlib.TxOut(address=delegation_out.pool_user.stake.address, amount=-1)
            ],
        )

        # Check that the key deposit was NOT returned and rewards were withdrawn
        assert (
            cluster.g_query.get_address_balance(src_address)
            == src_payment_balance - tx_raw_undeleg.fee + reward_balance
        ), f"Incorrect balance for source address `{src_address}`"

        # Check that the stake address is no longer delegated
        stake_addr_info = cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        )
        assert stake_addr_info.address, f"Reward address is not registered: {stake_addr_info}"
        assert not stake_addr_info.delegation, (
            f"Stake address is still delegated: {stake_addr_info}"
        )

        still_rewards_epoch = cluster.wait_for_epoch(epoch_no=undeleg_epoch + 1, padding_seconds=20)

        assert cluster.g_query.get_stake_addr_info(
            delegation_out.pool_user.stake.address
        ).reward_account_balance, "No reward was received next epoch after undelegation"

        # Check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_undeleg)

        # Check records in db-sync
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
            assert still_rewards_epoch in db_reward_epochs

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.dbsync
    @pytest.mark.parametrize(
        "stake_cert",
        ("vkey_file", "stake_address"),
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_addr_delegation_deregistration(
        self,
        cluster_and_pool: tuple[clusterlib.ClusterLib, str],
        pool_users_cluster_and_pool: list[clusterlib.PoolUser],
        pool_users_disposable_cluster_and_pool: list[clusterlib.PoolUser],
        stake_cert: str,
        build_method: str,
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
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable_cluster_and_pool[0]
        user_payment = pool_users_cluster_and_pool[0].payment
        src_init_balance = cluster.g_query.get_address_balance(user_payment.address)

        stake_vkey_file = user_registered.stake.vkey_file if stake_cert == "vkey_file" else None
        stake_address = user_registered.stake.address if stake_cert == "stake_address" else None

        address_deposit = common.get_conway_address_deposit(cluster_obj=cluster)

        # Create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=address_deposit,
            stake_vkey_file=stake_vkey_file,
            stake_address=stake_address,
        )

        # Register stake address
        tx_files_reg = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )
        tx_raw_output_reg = cluster.g_transaction.send_tx(
            src_address=user_payment.address,
            tx_name=f"{temp_template}_reg",
            tx_files=tx_files_reg,
        )

        # Check that the stake address is registered
        assert cluster.g_query.get_stake_addr_info(user_registered.stake.address).address, (
            f"Stake address is not registered: {user_registered.stake.address}"
        )

        tx_db_reg = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_reg)
        if tx_db_reg:
            assert user_registered.stake.address in tx_db_reg.stake_registration

        # Check that the balance for source address was correctly updated
        assert (
            cluster.g_query.get_address_balance(user_payment.address)
            == src_init_balance - tx_raw_output_reg.fee - cluster.g_query.get_address_deposit()
        ), f"Incorrect balance for source address `{user_payment.address}`"

        src_registered_balance = cluster.g_query.get_address_balance(user_payment.address)

        # Create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.g_stake_address.gen_stake_and_vote_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=stake_vkey_file,
            stake_address=stake_address,
            stake_pool_id=pool_id,
            always_abstain=True,
        )

        # Create stake address deregistration cert
        stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=address_deposit,
            stake_vkey_file=stake_vkey_file,
            stake_address=stake_address,
        )

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # Delegate and deregister stake address in single TX
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file, stake_addr_dereg_cert],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        def _build_and_submit() -> clusterlib.TxRawOutput:
            return clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_deleg_dereg",
                src_address=user_payment.address,
                tx_files=tx_files,
                build_method=build_method,
                witness_override=len(tx_files.signing_key_files),
            )

        try:
            tx_raw_output_deleg = common.match_blocker(func=_build_and_submit)
        except clusterlib.CLIError as exc:
            if "ValueNotConservedUTxO" in str(exc):
                issues.cli_942.finish_test()
            raise

        # Check that the balance for source address was correctly updated and that the key
        # deposit was returned
        assert (
            cluster.g_query.get_address_balance(user_payment.address)
            == src_registered_balance
            - tx_raw_output_deleg.fee
            + cluster.g_query.get_address_deposit()
        ), f"Incorrect balance for source address `{user_payment.address}`"

        # Check that the stake address was NOT delegated
        stake_addr_info = cluster.g_query.get_stake_addr_info(user_registered.stake.address)
        assert not stake_addr_info.delegation, f"Stake address was delegated: {stake_addr_info}"

        tx_db_deleg = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_deleg)
        if tx_db_deleg:
            assert user_registered.stake.address in tx_db_deleg.stake_deregistration
            assert user_registered.stake.address == tx_db_deleg.stake_delegation[0].address
            assert tx_db_deleg.stake_delegation[0].active_epoch_no == init_epoch + 2
            assert pool_id == tx_db_deleg.stake_delegation[0].pool_id


class TestNegative:
    """Tests that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_delegation_cert_with_wrong_key(
        self,
        cluster_and_pool: tuple[clusterlib.ClusterLib, str],
        pool_users_cluster_and_pool: list[clusterlib.PoolUser],
    ):
        """Try to generate stake address delegation certificate using wrong stake vkey.

        Expect failure.

        * attempt to generate stake address delegation certificate using payment vkey instead of
          stake vkey
        * check that certificate generation fails with expected error
        """
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)

        # Create stake address delegation cert, use wrong stake vkey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_stake_address.gen_stake_and_vote_delegation_cert(
                addr_name=f"{temp_template}_addr0",
                stake_vkey_file=pool_users_cluster_and_pool[0].payment.vkey_file,
                stake_pool_id=pool_id,
                always_abstain=True,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "Expected: StakeVerificationKeyShelley" in exc_value
                or "MissingVKeyWitnessesUTXOW" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_delegate_addr_with_wrong_key(
        self,
        cluster_and_pool: tuple[clusterlib.ClusterLib, str],
        pool_users_cluster_and_pool: list[clusterlib.PoolUser],
        pool_users_disposable_cluster_and_pool: list[clusterlib.PoolUser],
    ):
        """Try to delegate stake address using wrong payment skey.

        Expect failure.

        * create and register stake address
        * generate stake address delegation certificate
        * attempt to submit delegation transaction signed with wrong payment skey
        * check that transaction submission fails with MissingVKeyWitnessesUTXOW error
        """
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable_cluster_and_pool[0]
        user_payment = pool_users_cluster_and_pool[0].payment

        # Register stake address
        clusterlib_utils.register_stake_address(
            cluster_obj=cluster,
            pool_user=clusterlib.PoolUser(payment=user_payment, stake=user_registered.stake),
            name_template=temp_template,
            deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster),
        )

        # Create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.g_stake_address.gen_stake_and_vote_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            stake_pool_id=pool_id,
            always_abstain=True,
        )

        # Delegate stake address, use wrong payment skey
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
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "MissingVKeyWitnessesUTXOW" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_delegate_unknown_addr(
        self,
        cluster_and_pool: tuple[clusterlib.ClusterLib, str],
        pool_users_cluster_and_pool: list[clusterlib.PoolUser],
        pool_users_disposable_cluster_and_pool: list[clusterlib.PoolUser],
        build_method: str,
    ):
        """Try to delegate unknown stake address.

        Expect failure.

        Uses parametrized build method (build or build-raw).

        * create stake address but do not register it
        * generate stake address delegation certificate for unregistered address
        * attempt to submit delegation transaction for unknown stake address
        * check that transaction fails with StakeDelegationImpossibleDELEG or
          StakeKeyNotRegisteredDELEG error
        """
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable_cluster_and_pool[0]
        user_payment = pool_users_cluster_and_pool[0].payment

        # Create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.g_stake_address.gen_stake_and_vote_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            stake_pool_id=pool_id,
            always_abstain=True,
        )

        # Delegate unknown stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_deleg_unknown",
                src_address=user_payment.address,
                tx_files=tx_files,
                build_method=build_method,
                witness_override=len(tx_files.signing_key_files),
            )

        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "StakeDelegationImpossibleDELEG" in exc_value
                or "StakeKeyNotRegisteredDELEG" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_delegate_deregistered_addr(
        self,
        cluster_and_pool: tuple[clusterlib.ClusterLib, str],
        pool_users_cluster_and_pool: list[clusterlib.PoolUser],
        pool_users_disposable_cluster_and_pool: list[clusterlib.PoolUser],
        build_method: str,
    ):
        """Try to delegate deregistered stake address.

        Expect failure.

        Uses parametrized build method (build or build-raw).

        * create and register stake address
        * deregister the stake address
        * generate stake address delegation certificate for deregistered address
        * attempt to submit delegation transaction for deregistered stake address
        * check that transaction fails with StakeDelegationImpossibleDELEG or
          StakeKeyNotRegisteredDELEG error
        """
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable_cluster_and_pool[0]
        user_payment = pool_users_cluster_and_pool[0].payment

        address_deposit = common.get_conway_address_deposit(cluster_obj=cluster)
        certs_pool_user = clusterlib.PoolUser(payment=user_payment, stake=user_registered.stake)

        # Register stake address
        clusterlib_utils.register_stake_address(
            cluster_obj=cluster,
            pool_user=certs_pool_user,
            name_template=temp_template,
            deposit_amt=address_deposit,
        )

        # Deregister stake address
        clusterlib_utils.deregister_stake_address(
            cluster_obj=cluster,
            pool_user=certs_pool_user,
            name_template=temp_template,
            deposit_amt=address_deposit,
        )

        # Create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.g_stake_address.gen_stake_and_vote_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            stake_pool_id=pool_id,
            always_abstain=True,
        )

        # Delegate deregistered stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_deleg_cert_file],
            signing_key_files=[user_payment.skey_file, user_registered.stake.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_deleg_dereg",
                src_address=user_payment.address,
                tx_files=tx_files,
                build_method=build_method,
                witness_override=len(tx_files.signing_key_files),
            )

        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "StakeDelegationImpossibleDELEG" in exc_value
                or "StakeKeyNotRegisteredDELEG" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_delegatee_not_registered(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        pool_users_disposable: list[clusterlib.PoolUser],
    ):
        """Try to delegate stake address to unregistered pool.

        Expect failure.

        * create and register stake address
        * generate pool cold keys and certificate but do not register the pool
        * generate stake address delegation certificate using unregistered pool cold vkey
        * attempt to submit delegation transaction to unregistered pool
        * check that transaction fails with DelegateeNotRegisteredDELEG or
          DelegateeStakePoolNotRegisteredDELEG error
        """
        temp_template = common.get_test_id(cluster)

        user_registered = pool_users_disposable[0]
        user_payment = pool_users[0].payment

        # Register stake address
        clusterlib_utils.register_stake_address(
            cluster_obj=cluster,
            pool_user=clusterlib.PoolUser(payment=user_payment, stake=user_registered.stake),
            name_template=temp_template,
            deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster),
        )

        # Create pool cold keys and ceritifcate, but don't register the pool
        node_cold = cluster.g_node.gen_cold_key_pair_and_counter(node_name=f"{temp_template}_pool")

        # Create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.g_stake_address.gen_stake_and_vote_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=user_registered.stake.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
            always_abstain=True,
        )

        # Delegate stake address
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
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "DelegateeNotRegisteredDELEG" in exc_value  # Before cardano-node 10.0.0
                or "DelegateeStakePoolNotRegisteredDELEG" in exc_value
            ), exc_value
