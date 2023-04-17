"""Tests for MIR certificates."""
import logging
import time
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view

LOGGER = logging.getLogger(__name__)

RESERVES = "reserves"
TREASURY = "treasury"


def _wait_for_ada_pots(epoch_from: int, expected_len: int = 2) -> List[dbsync_queries.ADAPotsDBRow]:
    pots_records = []
    for r in range(4):
        if r > 0:
            LOGGER.warning(f"Repeating the `ada_pots` SQL query for the {r} time.")
            time.sleep(2 + r * r)
        pots_records = list(dbsync_queries.query_ada_pots(epoch_from=epoch_from))
        if len(pots_records) == expected_len:
            break
    else:
        raise AssertionError(
            f"Got {len(pots_records)} record(s) instead of expected {expected_len}."
        )

    return pots_records


@pytest.fixture
def cluster_pots(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    return cluster_manager.get(
        lock_resources=[
            cluster_management.Resources.RESERVES,
            cluster_management.Resources.TREASURY,
        ]
    )


@pytest.fixture
def pool_users(
    cluster_manager: cluster_management.ClusterManager,
    cluster_pots: clusterlib.ClusterLib,
) -> List[clusterlib.PoolUser]:
    """Create pool user."""
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        created_users = clusterlib_utils.create_pool_users(
            cluster_obj=cluster_pots,
            name_template=f"test_mir_certs_ci{cluster_manager.cluster_instance_num}",
            no_of_addr=5,
        )
        fixture_cache.value = created_users

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        *created_users,
        cluster_obj=cluster_pots,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )

    return created_users


@pytest.fixture
def registered_users(
    cluster_manager: cluster_management.ClusterManager,
    cluster_pots: clusterlib.ClusterLib,
    pool_users: List[clusterlib.PoolUser],
) -> List[clusterlib.PoolUser]:
    """Register pool user's stake address."""
    registered = pool_users[1:3]

    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore
        fixture_cache.value = registered

    for i, pool_user in enumerate(registered):
        temp_template = f"test_mir_certs_{i}_ci{cluster_manager.cluster_instance_num}"
        clusterlib_utils.register_stake_address(
            cluster_obj=cluster_pots, pool_user=pool_user, name_template=temp_template
        )

    return registered


@pytest.fixture
def skip_on_hf_shortcut(
    cluster_pots: clusterlib.ClusterLib,  # pylint: disable=unused-argument # noqa: ARG001
) -> None:
    """Skip test if HF shortcut is used."""
    if (
        cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL
        and cluster_nodes.get_cluster_type().uses_shortcut
    ):
        pytest.skip("MIR certs testing is not supported on local cluster with HF shortcut.")


class TestMIRCerts:
    """Tests for MIR certificates."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_transfer_to_treasury(
        self,
        skip_on_hf_shortcut: None,  # pylint: disable=unused-argument # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster_pots: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Send funds from the reserves pot to the treasury pot.

        Expected to fail when Era < Alonzo.
        """
        temp_template = common.get_test_id(cluster_pots)
        cluster = cluster_pots
        pool_user = pool_users[0]
        amount = 10_000_000_000_000

        mir_cert = cluster.g_governance.gen_mir_cert_to_treasury(
            transfer=amount, tx_name=temp_template
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[
                pool_user.payment.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        LOGGER.info(
            "Submitting MIR cert for transferring funds to treasury in "
            f"epoch {cluster.g_query.get_epoch()} on "
            f"cluster instance {cluster_manager.cluster_instance_num}"
        )
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=pool_user.payment.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        # check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            tx_epoch = cluster.g_query.get_epoch()

            assert tx_db_record.pot_transfers[0].reserves == -amount, (
                "Incorrect amount transferred from reserves "
                f"({tx_db_record.pot_transfers[0].reserves} != {-amount})"
            )
            assert tx_db_record.pot_transfers[0].treasury == amount, (
                "Incorrect amount transferred to treasury "
                f"({tx_db_record.pot_transfers[0].treasury} != {amount})"
            )

            cluster.wait_for_new_epoch()

            pots_records = _wait_for_ada_pots(epoch_from=tx_epoch)
            # normally `treasury[-1]` > `treasury[-2]`
            assert (pots_records[-1].treasury - pots_records[-2].treasury) > amount
            # normally `reserves[-1]` < `reserves[-2]`
            assert (pots_records[-2].reserves - pots_records[-1].reserves) > amount

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_transfer_to_treasury(
        self,
        skip_on_hf_shortcut: None,  # pylint: disable=unused-argument # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster_pots: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Send funds from the reserves pot to the treasury pot.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        temp_template = common.get_test_id(cluster_pots)
        cluster = cluster_pots
        pool_user = pool_users[0]
        amount = 10_000_000_000_000

        mir_cert = cluster.g_governance.gen_mir_cert_to_treasury(
            transfer=amount, tx_name=temp_template
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[
                pool_user.payment.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        tx_output = cluster.g_transaction.build_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            tx_files=tx_files,
            fee_buffer=1_000_000,
            witness_override=2,
        )

        LOGGER.info(
            "Submitting MIR cert for transferring funds to treasury in "
            f"epoch {cluster.g_query.get_epoch()} on "
            f"cluster instance {cluster_manager.cluster_instance_num}"
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=pool_user.payment.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            tx_epoch = cluster.g_query.get_epoch()

            assert tx_db_record.pot_transfers[0].reserves == -amount, (
                "Incorrect amount transferred from reserves "
                f"({tx_db_record.pot_transfers[0].reserves} != {-amount})"
            )
            assert tx_db_record.pot_transfers[0].treasury == amount, (
                "Incorrect amount transferred to treasury "
                f"({tx_db_record.pot_transfers[0].treasury} != {amount})"
            )

            cluster.wait_for_new_epoch()

            pots_records = _wait_for_ada_pots(epoch_from=tx_epoch)
            # normally `treasury[-1]` > `treasury[-2]`
            assert (pots_records[-1].treasury - pots_records[-2].treasury) > amount
            # normally `reserves[-1]` < `reserves[-2]`
            assert (pots_records[-2].reserves - pots_records[-1].reserves) > amount

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_transfer_to_reserves(
        self,
        skip_on_hf_shortcut: None,  # pylint: disable=unused-argument # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster_pots: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Send funds from the treasury pot to the reserves pot.

        Expected to fail when Era < Alonzo.
        """
        temp_template = common.get_test_id(cluster_pots)
        cluster = cluster_pots
        pool_user = pool_users[0]
        amount = 1_000_000_000_000

        mir_cert = cluster.g_governance.gen_mir_cert_to_rewards(
            transfer=amount, tx_name=temp_template
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[
                pool_user.payment.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        LOGGER.info(
            "Submitting MIR cert for transferring funds to reserves in "
            f"epoch {cluster.g_query.get_epoch()} on "
            f"cluster instance {cluster_manager.cluster_instance_num}"
        )
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=pool_user.payment.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        # check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            tx_epoch = cluster.g_query.get_epoch()

            assert tx_db_record.pot_transfers[0].treasury == -amount, (
                "Incorrect amount transferred from treasury "
                f"({tx_db_record.pot_transfers[0].treasury} != {-amount})"
            )
            assert tx_db_record.pot_transfers[0].reserves == amount, (
                "Incorrect amount transferred to reserves "
                f"({tx_db_record.pot_transfers[0].reserves} != {amount})"
            )

            cluster.wait_for_new_epoch()

            pots_records = _wait_for_ada_pots(epoch_from=tx_epoch)
            # normally `treasury[-1]` > `treasury[-2]`
            assert pots_records[-1].treasury < pots_records[-2].treasury
            # normally `reserves[-1]` < `reserves[-2]`
            assert pots_records[-1].reserves > pots_records[-2].reserves

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_transfer_to_reserves(
        self,
        skip_on_hf_shortcut: None,  # pylint: disable=unused-argument # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster_pots: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Send funds from the treasury pot to the reserves pot.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        temp_template = common.get_test_id(cluster_pots)
        cluster = cluster_pots
        pool_user = pool_users[0]
        amount = 1_000_000_000_000

        mir_cert = cluster.g_governance.gen_mir_cert_to_rewards(
            transfer=amount, tx_name=temp_template
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[
                pool_user.payment.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        tx_output = cluster.g_transaction.build_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            tx_files=tx_files,
            fee_buffer=1_000_000,
            witness_override=2,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        LOGGER.info(
            "Submitting MIR cert for transferring funds to reserves in "
            f"epoch {cluster.g_query.get_epoch()} on "
            f"cluster instance {cluster_manager.cluster_instance_num}"
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=pool_user.payment.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            tx_epoch = cluster.g_query.get_epoch()

            assert tx_db_record.pot_transfers[0].treasury == -amount, (
                "Incorrect amount transferred from treasury "
                f"({tx_db_record.pot_transfers[0].treasury} != {-amount})"
            )
            assert tx_db_record.pot_transfers[0].reserves == amount, (
                "Incorrect amount transferred to reserves "
                f"({tx_db_record.pot_transfers[0].reserves} != {amount})"
            )

            cluster.wait_for_new_epoch()

            pots_records = _wait_for_ada_pots(epoch_from=tx_epoch)
            # normally `treasury[-1]` > `treasury[-2]`
            assert pots_records[-1].treasury < pots_records[-2].treasury
            # normally `reserves[-1]` < `reserves[-2]`
            assert pots_records[-1].reserves > pots_records[-2].reserves

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("fund_src", (RESERVES, TREASURY))
    def test_pay_stake_addr_from(
        self,
        skip_on_hf_shortcut: None,  # pylint: disable=unused-argument # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster_pots: clusterlib.ClusterLib,
        registered_users: List[clusterlib.PoolUser],
        fund_src: str,
    ):
        """Send funds from the reserves or treasury pot to stake address.

        * generate an MIR certificate
        * submit a TX with the MIR certificate
        * check that the expected amount was added to the stake address reward account
        * (optional) check transaction in db-sync
        """
        temp_template = f"{common.get_test_id(cluster_pots)}_{fund_src}"
        cluster = cluster_pots
        amount = 50_000_000
        registered_user = registered_users[0]

        init_reward = cluster.g_query.get_stake_addr_info(
            registered_user.stake.address
        ).reward_account_balance

        mir_cert = cluster.g_governance.gen_mir_cert_stake_addr(
            stake_addr=registered_user.stake.address,
            reward=amount,
            tx_name=temp_template,
            use_treasury=fund_src == TREASURY,
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[
                registered_user.payment.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        LOGGER.info(
            f"Submitting MIR cert for transferring funds from {fund_src} to "
            f"'{registered_user.stake.address}' in epoch {cluster.g_query.get_epoch()} "
            f"on cluster instance {cluster_manager.cluster_instance_num}"
        )
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=registered_user.payment.address,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=registered_user.payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for source address `{registered_user.payment.address}`"

        cluster.wait_for_new_epoch()

        assert (
            cluster.g_query.get_stake_addr_info(
                registered_user.stake.address
            ).reward_account_balance
            == init_reward + amount
        ), f"Incorrect reward balance for stake address `{registered_user.stake.address}`"

        # check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            stash_record = (
                tx_db_record.treasury[0] if fund_src == TREASURY else tx_db_record.reserve[0]
            )
            assert stash_record.amount == amount, (
                "Incorrect amount transferred using MIR certificate "
                f"({stash_record.amount} != {amount})"
            )
            assert stash_record.address == registered_user.stake.address, (
                "Incorrect stake address "
                f"({stash_record.address} != {registered_user.stake.address})"
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.parametrize("fund_src", (RESERVES, TREASURY))
    def test_build_pay_stake_addr_from(
        self,
        skip_on_hf_shortcut: None,  # pylint: disable=unused-argument # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster_pots: clusterlib.ClusterLib,
        registered_users: List[clusterlib.PoolUser],
        fund_src: str,
    ):
        """Send funds from the reserves or treasury pot to stake address.

        Uses `cardano-cli transaction build` command for building the transactions.

        * generate an MIR certificate
        * submit a TX with the MIR certificate
        * check that the expected amount was added to the stake address reward account
        * (optional) check transaction in db-sync
        """
        temp_template = f"{common.get_test_id(cluster_pots)}_{fund_src}"
        cluster = cluster_pots
        amount = 50_000_000
        registered_user = registered_users[0]

        init_reward = cluster.g_query.get_stake_addr_info(
            registered_user.stake.address
        ).reward_account_balance

        mir_cert = cluster.g_governance.gen_mir_cert_stake_addr(
            stake_addr=registered_user.stake.address,
            reward=amount,
            tx_name=temp_template,
            use_treasury=fund_src == TREASURY,
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[
                registered_user.payment.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        tx_output = cluster.g_transaction.build_tx(
            src_address=registered_user.payment.address,
            tx_name=temp_template,
            tx_files=tx_files,
            fee_buffer=1_000_000,
            witness_override=2,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        LOGGER.info(
            f"Submitting MIR cert for transferring funds from {fund_src} to "
            f"'{registered_user.stake.address}' in epoch {cluster.g_query.get_epoch()} "
            f"on cluster instance {cluster_manager.cluster_instance_num}"
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=registered_user.payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee
        ), f"Incorrect balance for source address `{registered_user.payment.address}`"

        cluster.wait_for_new_epoch()

        assert (
            cluster.g_query.get_stake_addr_info(
                registered_user.stake.address
            ).reward_account_balance
            == init_reward + amount
        ), f"Incorrect reward balance for stake address `{registered_user.stake.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            stash_record = (
                tx_db_record.treasury[0] if fund_src == TREASURY else tx_db_record.reserve[0]
            )
            assert stash_record.amount == amount, (
                "Incorrect amount transferred using MIR certificate "
                f"({stash_record.amount} != {amount})"
            )
            assert stash_record.address == registered_user.stake.address, (
                "Incorrect stake address "
                f"({stash_record.address} != {registered_user.stake.address})"
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_pay_stake_addr_from_both(
        self,
        skip_on_hf_shortcut: None,  # pylint: disable=unused-argument # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster_pots: clusterlib.ClusterLib,
        registered_users: List[clusterlib.PoolUser],
    ):
        """Send funds from the reserves and treasury pots to stake address.

        * generate an MIR certificate for transferring from treasury
        * generate an MIR certificate for transferring from reserves
        * submit a TX with the treasury MIR certificate
        * in the same epoch as the previous TX, submit a TX with the reserves MIR certificate
        * check that the expected amount was added to the stake address reward account
        * (optional) check transactions in db-sync
        """
        cluster = cluster_pots
        temp_template = common.get_test_id(cluster)
        amount = 50_000_000
        registered_user = registered_users[0]

        init_reward = cluster.g_query.get_stake_addr_info(
            registered_user.stake.address
        ).reward_account_balance
        init_balance = cluster.g_query.get_address_balance(registered_user.payment.address)

        mir_cert_treasury = cluster.g_governance.gen_mir_cert_stake_addr(
            stake_addr=registered_user.stake.address,
            reward=amount,
            tx_name=f"{temp_template}_treasury",
            use_treasury=True,
        )
        tx_files_treasury = clusterlib.TxFiles(
            certificate_files=[mir_cert_treasury],
            signing_key_files=[
                registered_user.payment.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        mir_cert_reserves = cluster.g_governance.gen_mir_cert_stake_addr(
            stake_addr=registered_user.stake.address,
            reward=amount,
            tx_name=f"{temp_template}_reserves",
        )
        tx_files_reserves = clusterlib.TxFiles(
            certificate_files=[mir_cert_reserves],
            signing_key_files=[
                registered_user.payment.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        LOGGER.info(
            f"Submitting MIR cert for transferring funds from treasury to "
            f"'{registered_user.stake.address}' in epoch {cluster.g_query.get_epoch()} "
            f"on cluster instance {cluster_manager.cluster_instance_num}"
        )
        tx_raw_output_treasury = cluster.g_transaction.send_tx(
            src_address=registered_user.payment.address,
            tx_name=f"{temp_template}_treasury",
            tx_files=tx_files_treasury,
        )

        time.sleep(2)

        LOGGER.info(
            f"Submitting MIR cert for transferring funds from reserves to "
            f"'{registered_user.stake.address}' in epoch {cluster.g_query.get_epoch()} "
            f"on cluster instance {cluster_manager.cluster_instance_num}"
        )
        tx_raw_output_reserves = cluster.g_transaction.send_tx(
            src_address=registered_user.payment.address,
            tx_name=f"{temp_template}_reserves",
            tx_files=tx_files_reserves,
        )

        assert (
            cluster.g_query.get_address_balance(registered_user.payment.address)
            == init_balance - tx_raw_output_treasury.fee - tx_raw_output_reserves.fee
        ), f"Incorrect balance for source address `{registered_user.payment.address}`"

        cluster.wait_for_new_epoch()

        assert (
            cluster.g_query.get_stake_addr_info(
                registered_user.stake.address
            ).reward_account_balance
            == init_reward + amount * 2
        ), f"Incorrect reward balance for stake address `{registered_user.stake.address}`"

        tx_db_record_treasury = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_raw_output_treasury
        )
        if tx_db_record_treasury:
            tx_db_record_reserves = dbsync_utils.check_tx(
                cluster_obj=cluster, tx_raw_output=tx_raw_output_reserves
            )
            assert tx_db_record_reserves

            assert (
                not tx_db_record_treasury.reserve
            ), f"Reserve record is not empty: {tx_db_record_treasury.reserve}"
            assert (
                not tx_db_record_reserves.treasury
            ), f"Treasury record is not empty: {tx_db_record_reserves.treasury}"

            db_treasury = tx_db_record_treasury.treasury[0]
            assert db_treasury.amount == amount, (
                "Incorrect amount transferred using MIR certificate "
                f"({db_treasury.amount} != {amount})"
            )
            assert db_treasury.address == registered_user.stake.address, (
                "Incorrect stake address "
                f"({db_treasury.address} != {registered_user.stake.address})"
            )

            db_reserve = tx_db_record_reserves.reserve[0]
            assert db_reserve.amount == amount, (
                "Incorrect amount transferred using MIR certificate "
                f"({db_reserve.amount} != {amount})"
            )
            assert db_reserve.address == registered_user.stake.address, (
                "Incorrect stake address "
                f"({db_reserve.address} != {registered_user.stake.address})"
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_pay_multi_stake_addrs(
        self,
        skip_on_hf_shortcut: None,  # pylint: disable=unused-argument # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster_pots: clusterlib.ClusterLib,
        registered_users: List[clusterlib.PoolUser],
    ):
        """Send funds from the reserves and treasury pots to multiple stake addresses in single TX.

        * generate an MIR certificates for transferring from treasury for each stake address
        * generate an MIR certificates for transferring from reserves for each stake address
        * submit a TX with all the MIR certificates generated in previous steps
        * check that the expected amount was added to all stake address reward accounts
        * (optional) check transaction in db-sync
        """
        cluster = cluster_pots
        temp_template = common.get_test_id(cluster)
        amount_treasury = 50_000_000
        amount_reserves = 60_000_000

        init_reward_u0 = cluster.g_query.get_stake_addr_info(
            registered_users[0].stake.address
        ).reward_account_balance
        init_reward_u1 = cluster.g_query.get_stake_addr_info(
            registered_users[1].stake.address
        ).reward_account_balance

        mir_cert_treasury_u0 = cluster.g_governance.gen_mir_cert_stake_addr(
            stake_addr=registered_users[0].stake.address,
            reward=amount_treasury,
            tx_name=f"{temp_template}_treasury_u0",
            use_treasury=True,
        )
        mir_cert_reserves_u0 = cluster.g_governance.gen_mir_cert_stake_addr(
            stake_addr=registered_users[0].stake.address,
            reward=amount_reserves,
            tx_name=f"{temp_template}_reserves_u0",
        )
        mir_cert_treasury_u1 = cluster.g_governance.gen_mir_cert_stake_addr(
            stake_addr=registered_users[1].stake.address,
            reward=amount_treasury,
            tx_name=f"{temp_template}_treasury_u1",
            use_treasury=True,
        )
        mir_cert_reserves_u1 = cluster.g_governance.gen_mir_cert_stake_addr(
            stake_addr=registered_users[1].stake.address,
            reward=amount_reserves,
            tx_name=f"{temp_template}_reserves_u1",
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[
                mir_cert_treasury_u0,
                mir_cert_reserves_u0,
                mir_cert_treasury_u1,
                mir_cert_reserves_u1,
            ],
            signing_key_files=[
                registered_users[0].payment.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        LOGGER.info(
            f"Submitting MIR cert for transferring funds from treasury and reserves to "
            f"multiple stake addresses in epoch {cluster.g_query.get_epoch()} "
            f"on cluster instance {cluster_manager.cluster_instance_num}"
        )
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=registered_users[0].payment.address,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=registered_users[0].payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for source address `{registered_users[0].payment.address}`"

        cluster.wait_for_new_epoch()

        assert (
            cluster.g_query.get_stake_addr_info(
                registered_users[0].stake.address
            ).reward_account_balance
            == init_reward_u0 + amount_treasury + amount_reserves
        ), f"Incorrect reward balance for stake address `{registered_users[0].stake.address}`"
        assert (
            cluster.g_query.get_stake_addr_info(
                registered_users[1].stake.address
            ).reward_account_balance
            == init_reward_u1 + amount_treasury + amount_reserves
        ), f"Incorrect reward balance for stake address `{registered_users[1].stake.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_treasury_u0 = tx_db_record.treasury[0]
            db_reserve_u0 = tx_db_record.reserve[0]
            assert db_treasury_u0.amount == amount_treasury, (
                "Incorrect amount transferred using MIR certificate "
                f"({db_treasury_u0.amount} != {amount_treasury})"
            )
            assert db_treasury_u0.address == registered_users[0].stake.address, (
                "Incorrect stake address "
                f"({db_treasury_u0.address} != {registered_users[0].stake.address})"
            )
            assert db_reserve_u0.amount == amount_reserves, (
                "Incorrect amount transferred using MIR certificate "
                f"({db_reserve_u0.amount} != {amount_reserves})"
            )
            assert db_reserve_u0.address == registered_users[0].stake.address, (
                "Incorrect stake address "
                f"({db_reserve_u0.address} != {registered_users[0].stake.address})"
            )

            db_treasury_u1 = tx_db_record.treasury[1]
            db_reserve_u1 = tx_db_record.reserve[1]
            assert db_treasury_u1.amount == amount_treasury, (
                "Incorrect amount transferred using MIR certificate "
                f"({db_treasury_u1.amount} != {amount_treasury})"
            )
            assert db_treasury_u1.address == registered_users[1].stake.address, (
                "Incorrect stake address "
                f"({db_treasury_u1.address} != {registered_users[1].stake.address})"
            )
            assert db_reserve_u1.amount == amount_reserves, (
                "Incorrect amount transferred using MIR certificate "
                f"({db_reserve_u1.amount} != {amount_reserves})"
            )
            assert db_reserve_u1.address == registered_users[1].stake.address, (
                "Incorrect stake address "
                f"({db_reserve_u1.address} != {registered_users[1].stake.address})"
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("addr_history", ("addr_known", "addr_unknown"))
    @pytest.mark.parametrize("fund_src", (RESERVES, TREASURY))
    def test_pay_unregistered_stake_addr_from(  # noqa: C901
        self,
        skip_on_hf_shortcut: None,  # pylint: disable=unused-argument # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster_pots: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        fund_src: str,
        addr_history: str,
    ):
        """Send funds from the reserves or treasury pot to unregistered stake address.

        * generate an MIR certificate
        * if a stake address should be known on blockchain:

            - register the stake address
            - if transferring funds from treasury, deregister the stake address
              BEFORE submitting the TX

        * submit a TX with the MIR certificate
        * if a stake address should be known on blockchain and if transferring funds from reserves,
          deregister the stake address AFTER submitting the TX
        * check that the amount was NOT added to the stake address reward account
        * (optional) check transaction in db-sync
        """
        # pylint: disable=too-many-branches
        temp_template = f"{common.get_test_id(cluster_pots)}_{fund_src}_{addr_history}"
        cluster = cluster_pots

        if fund_src == TREASURY:
            amount = 1_500_000_000_000
            pool_user = pool_users[3]
        else:
            amount = 50_000_000_000_000
            pool_user = pool_users[4]

        init_balance = cluster.g_query.get_address_balance(pool_user.payment.address)

        mir_cert = cluster.g_governance.gen_mir_cert_stake_addr(
            stake_addr=pool_user.stake.address,
            reward=amount,
            tx_name=temp_template,
            use_treasury=fund_src == TREASURY,
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[
                pool_user.payment.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        # register the stake address, if it is supposed to be known on blockchain
        if addr_history == "addr_known":
            tx_raw_out_reg = clusterlib_utils.register_stake_address(
                cluster_obj=cluster_pots, pool_user=pool_user, name_template=temp_template
            )

            # deregister the stake address before submitting the Tx with MIR cert
            if fund_src == TREASURY:
                tx_raw_out_withdrawal, tx_raw_out_dereg = clusterlib_utils.deregister_stake_address(
                    cluster_obj=cluster_pots, pool_user=pool_user, name_template=temp_template
                )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        LOGGER.info(
            f"Submitting MIR cert for transferring funds from {fund_src} to "
            f"'{pool_user.stake.address}' in epoch {cluster.g_query.get_epoch()} "
            f"on cluster instance {cluster_manager.cluster_instance_num}"
        )
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        tx_epoch = cluster.g_query.get_epoch()

        # deregister the stake address after submitting the Tx with MIR cert
        if addr_history == "addr_known" and fund_src != TREASURY:
            tx_raw_out_withdrawal, tx_raw_out_dereg = clusterlib_utils.deregister_stake_address(
                cluster_obj=cluster_pots, pool_user=pool_user, name_template=temp_template
            )

        reg_dereg_fees = 0
        if addr_history == "addr_known":
            reg_dereg_fees = tx_raw_out_reg.fee + tx_raw_out_withdrawal.fee + tx_raw_out_dereg.fee

        assert (
            cluster.g_query.get_address_balance(pool_user.payment.address)
            == init_balance - tx_raw_output.fee - reg_dereg_fees
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            if fund_src == TREASURY:
                assert tx_db_record.treasury[0].amount == amount, (
                    "Incorrect amount transferred from treasury "
                    f"({tx_db_record.treasury[0].amount} != {amount})"
                )
            else:
                assert tx_db_record.reserve[0].amount == amount, (
                    "Incorrect amount transferred from reserve "
                    f"({tx_db_record.reserve[0].amount} != {amount})"
                )

        # wait for next epoch and check the reward
        cluster.wait_for_new_epoch()

        assert not cluster.g_query.get_stake_addr_info(
            pool_user.stake.address
        ).reward_account_balance, (
            f"Reward was added for unregistered stake address `{pool_user.stake.address}`"
        )

        if tx_db_record:
            # check that the amount was not transferred out of the pot
            pots_records = _wait_for_ada_pots(epoch_from=tx_epoch)

            if fund_src == TREASURY:
                # normally `treasury[-1]` > `treasury[-2]`
                assert abs(pots_records[-1].treasury - pots_records[-2].treasury) < amount
            else:
                # normally `reserves[-1]` < `reserves[-2]`
                assert abs(pots_records[-2].reserves - pots_records[-1].reserves) < amount


class TestNegativeMIRCerts:
    """Negative tests for MIR certificates."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("fund_src", (RESERVES, TREASURY))
    def test_exceed_pay_stake_addr_from(
        self,
        cluster_pots: clusterlib.ClusterLib,
        registered_users: List[clusterlib.PoolUser],
        fund_src: str,
    ):
        """Try to send more funds than available from the reserves or treasury pot to stake address.

        Expect failure.

        * generate an MIR certificate
        * submit a TX with the MIR certificate
        * check that submitting the transaction fails with an expected error
        """
        temp_template = f"{common.get_test_id(cluster_pots)}_{fund_src}"
        cluster = cluster_pots
        amount = 50_000_000_000_000_000
        registered_user = registered_users[0]

        mir_cert = cluster.g_governance.gen_mir_cert_stake_addr(
            stake_addr=registered_user.stake.address,
            reward=amount,
            tx_name=temp_template,
            use_treasury=fund_src == TREASURY,
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[
                registered_user.payment.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=registered_user.payment.address,
                tx_name=temp_template,
                tx_files=tx_files,
            )
        err_str = str(excinfo.value)
        assert "InsufficientForInstantaneousRewardsDELEG" in err_str, err_str
