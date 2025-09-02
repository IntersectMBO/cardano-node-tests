"""Tests for treasury donation."""

import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


@pytest.fixture
def cluster_treasury(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    return cluster_manager.get(lock_resources=cluster_management.Resources.POTS)


@pytest.fixture
def payment_addr_treasury(
    cluster_manager: cluster_management.ClusterManager,
    cluster_treasury: clusterlib.ClusterLib,
) -> clusterlib.AddressRecord:
    """Create new payment address."""
    cluster = cluster_treasury
    addr = common.get_payment_addr(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        caching_key=helpers.get_current_line_str(),
    )
    return addr


@pytest.fixture
def payment_addr_singleton(
    cluster_manager: cluster_management.ClusterManager,
    cluster_singleton: clusterlib.ClusterLib,
) -> clusterlib.AddressRecord:
    """Create new payment address.

    This fixture is used by single test, so it is not cached.
    """
    cluster = cluster_singleton
    addr = common.get_payment_addr(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
    )
    return addr


class TestTreasuryDonation:
    """Test treasury donation - transferring donation, check treasury state in pots."""

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.smoke
    def test_transfer_treasury_donation(
        self,
        cluster_treasury: clusterlib.ClusterLib,
        payment_addr_treasury: clusterlib.AddressRecord,
        build_method: str,
        submit_method: str,
    ):
        """Send funds from payment address to the treasury.

        The test doesn't check the actual treasury balance, only that the transaction can be
        built and submitted.

        * send funds from 1 source address to to the treasury
        * check expected balances
        """
        cluster = cluster_treasury
        temp_template = common.get_test_id(cluster)

        amount = 2_000_000

        # Make sure we have enough time to submit the Tx in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        treasury_val = cluster.g_query.get_treasury()
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr_treasury.skey_file])

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr_treasury.address,
            current_treasury_value=treasury_val,
            treasury_donation=amount,
            submit_method=submit_method,
            change_address=payment_addr_treasury.address,
            build_method=build_method,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=payment_addr_treasury.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - amount
        ), f"Incorrect balance for source address `{payment_addr_treasury.address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.needs_dbsync
    def test_dbsync_transfer_treasury_donation(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        payment_addr_singleton: clusterlib.AddressRecord,
    ):
        """Send funds from payment address to the treasury and check the amounts in db-sync.

        The test is a singleton (the only test that can run on a testnet at a time) so that
        the pots are not modified by other tests.

        * send funds from 1 source address to to the treasury
        * check expected balances for both source addresses and treasury
        * check transactions and ADA pots in db-sync
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)

        amount = 2_000_000

        # Wait for next epoch to make sure all deposits and withdrawals are settled
        cur_epoch = cluster.wait_for_new_epoch(padding_seconds=10)

        pot_bef_tx = next(dbsync_queries.query_ada_pots(epoch_from=cur_epoch, epoch_to=cur_epoch))
        assert pot_bef_tx.epoch_no == cur_epoch

        treasury_val = cluster.g_query.get_treasury()
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr_singleton.skey_file])

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr_singleton.address,
            current_treasury_value=treasury_val,
            treasury_donation=amount,
            change_address=payment_addr_singleton.address,
            use_build_cmd=True,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=payment_addr_singleton.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - amount
        ), f"Incorrect balance for source address `{payment_addr_singleton.address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        assert (
            cluster.g_query.get_address_balance(payment_addr_singleton.address)
            == dbsync_utils.get_utxo(address=payment_addr_singleton.address).amount_sum
        ), f"Unexpected balance for source address `{payment_addr_singleton.address}` in db-sync"

        donation_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output.out_file)
        db_tx_data = dbsync_utils.get_tx_record(txhash=donation_txid)
        assert db_tx_data.treasury_donation == amount

        cur_epoch = cluster.wait_for_new_epoch(padding_seconds=10)
        pot_aft_tx = next(dbsync_queries.query_ada_pots(epoch_from=cur_epoch, epoch_to=cur_epoch))
        assert pot_aft_tx.epoch_no == cur_epoch

        updated_treasury = (
            pot_bef_tx.treasury  # Old treasury balance
            + pot_bef_tx.fees  # Fees that were added to treasury
            + pot_bef_tx.reserves
            - pot_aft_tx.reserves  # Delta of reserves: percentage of reserves is moved to treasury
            + pot_bef_tx.rewards
            - pot_aft_tx.rewards  # Delta of rewards: rewards are paid from treasury
            + pot_bef_tx.deposits_stake
            - pot_aft_tx.deposits_stake
            + pot_bef_tx.deposits_proposal
            - pot_aft_tx.deposits_proposal  # Delta of deposits that are returned to rewards pot
        )
        assert pot_aft_tx.treasury == updated_treasury + amount
