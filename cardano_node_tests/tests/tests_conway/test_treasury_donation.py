"""Tests for treasury donation."""

import logging
import typing as tp

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


@pytest.mark.testnets
@pytest.mark.smoke
class TestTreasuryDonation:
    """Test treasury donation - transferring donation, check treasury state in pots."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> tp.List[clusterlib.AddressRecord]:
        """Create new payment address."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                f"addr_basic_ci{cluster_manager.cluster_instance_num}_0",
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source address
        clusterlib_utils.fund_from_faucet(
            *addrs,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.dbsync
    @common.PARAM_USE_BUILD_CMD
    @submit_utils.PARAM_SUBMIT_METHOD
    def test_transfer_treasury_donation(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: tp.List[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Send funds from payment address to the treasury.

        * send funds from 1 source address to to the treasury
        * check expected balances for both source addresses and treasury
        * (optional) check transactions and ADA pots in db-sync
        """
        temp_template = common.get_test_id(cluster)

        amount = 2_000_000
        src_addr = payment_addrs[0]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        cur_epoch = cluster.g_query.get_epoch()
        if cur_epoch < 1:
            cluster.wait_for_new_epoch(padding_seconds=5)
        cur_epoch = cluster.g_query.get_epoch()
        pot_bef_trs_donat = list(
            dbsync_queries.query_ada_pots(epoch_from=cur_epoch, epoch_to=cur_epoch)
        )

        ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
        es_account: dict = ledger_state["stateBefore"]["esAccountState"]
        treasury_val = es_account["treasury"]

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=src_addr.address,
            current_treasury_value=treasury_val,
            treasury_donation=amount,
            submit_method=submit_method,
            change_address=src_addr.address,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files,
            witness_override=None,
            witness_count_add=0,
        )

        donation_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output.out_file)
        db_tx_data = dbsync_utils.get_tx_record(txhash=donation_txid)
        assert db_tx_data.treasury_donation == amount

        cluster.wait_for_new_epoch(padding_seconds=5)
        cur_epoch = cluster.g_query.get_epoch()
        pot_aft_trs_donat = list(
            dbsync_queries.query_ada_pots(epoch_from=cur_epoch, epoch_to=cur_epoch)
        )

        pot_bef_tx = pot_bef_trs_donat[0]
        pot_aft_tx = pot_aft_trs_donat[0]

        current_treasury = (
            pot_bef_tx.reserves
            - pot_aft_tx.reserves
            - pot_aft_tx.rewards
            + pot_bef_tx.rewards
            + pot_bef_tx.treasury
            + pot_bef_tx.fees
        )
        assert pot_aft_tx.treasury == current_treasury + amount

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            assert (
                cluster.g_query.get_address_balance(src_addr.address)
                == dbsync_utils.get_utxo(address=src_addr.address).amount_sum
            ), f"Unexpected balance for source address `{src_addr.address}` in db-sync"
