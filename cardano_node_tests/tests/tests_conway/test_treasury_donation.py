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
    return cluster_manager.get(
        lock_resources=[
            cluster_management.Resources.RESERVES,
            cluster_management.Resources.TREASURY,
        ]
    )


@pytest.fixture
def payment_addr(
    cluster_manager: cluster_management.ClusterManager,
    cluster_treasury: clusterlib.ClusterLib,
) -> clusterlib.AddressRecord:
    """Create new payment address."""
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        addr = clusterlib_utils.create_payment_addr_records(
            f"addr_treasury_donation_ci{cluster_manager.cluster_instance_num}_0",
            cluster_obj=cluster_treasury,
        )[0]
        fixture_cache.value = addr

    # Fund source address
    clusterlib_utils.fund_from_faucet(
        addr,
        cluster_obj=cluster_treasury,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )
    return addr


class TestTreasuryDonation:
    """Test treasury donation - transferring donation, check treasury state in pots."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.needs_dbsync
    @common.PARAM_USE_BUILD_CMD
    def test_transfer_treasury_donation(
        self,
        cluster_treasury: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        use_build_cmd: bool,
    ):
        """Send funds from payment address to the treasury.

        * send funds from 1 source address to to the treasury
        * check expected balances for both source addresses and treasury
        * check transactions and ADA pots in db-sync
        """
        cluster = cluster_treasury
        temp_template = common.get_test_id(cluster)

        amount = 2_000_000

        cur_epoch = cluster.g_query.get_epoch()
        if cur_epoch < 1:
            cluster.wait_for_new_epoch(padding_seconds=10)

        # Make sure we have enough time to query the treasury and submit the Tx in the same epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=10, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        cur_epoch = cluster.g_query.get_epoch()

        pot_bef_trs_donat = list(
            dbsync_queries.query_ada_pots(epoch_from=cur_epoch, epoch_to=cur_epoch)
        )
        pot_bef_tx = pot_bef_trs_donat[0]
        assert pot_bef_tx.epoch_no == cur_epoch

        treasury_val = cluster.g_conway_governance.query.treasury()
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr.address,
            current_treasury_value=treasury_val,
            treasury_donation=amount,
            # Use submit-api when available and when we are using `transaction build`.
            # This test is long and while we don't need to test all combination of build commands /
            # submit methods, we want to test all of them at least once.
            submit_method=submit_utils.SubmitMethods.API
            if use_build_cmd and submit_utils.is_submit_api_available()
            else submit_utils.SubmitMethods.CLI,
            change_address=payment_addr.address,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - amount
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        assert (
            cluster.g_query.get_address_balance(payment_addr.address)
            == dbsync_utils.get_utxo(address=payment_addr.address).amount_sum
        ), f"Unexpected balance for source address `{payment_addr.address}` in db-sync"

        donation_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output.out_file)
        db_tx_data = dbsync_utils.get_tx_record(txhash=donation_txid)
        assert db_tx_data.treasury_donation == amount

        cur_epoch = cluster.wait_for_new_epoch(padding_seconds=10)
        pot_aft_trs_donat = list(
            dbsync_queries.query_ada_pots(epoch_from=cur_epoch, epoch_to=cur_epoch)
        )
        pot_aft_tx = pot_aft_trs_donat[0]
        assert pot_aft_tx.epoch_no == cur_epoch

        current_treasury = (
            pot_bef_tx.treasury
            + pot_bef_tx.reserves
            + pot_bef_tx.rewards
            + pot_bef_tx.fees
            - pot_aft_tx.reserves
            - pot_aft_tx.rewards
        )
        # Any unclaimed ADA (rewards, deposits) go to the treasury, so we can't expect
        # the precise amount, we can only expect the minimal amount.
        assert pot_aft_tx.treasury >= current_treasury + amount
