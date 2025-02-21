"""Tests for basic transactions."""

import dataclasses
import itertools
import logging
import re

import allure
import pytest
from _pytest.fixtures import SubRequest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import tx_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import smash
from cardano_node_tests.utils import smash_client
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_api
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


class TestBasicTransactions:
    """Test basic transactions - transferring funds, transaction IDs."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @pytest.fixture
    def payment_addrs_disposable(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=f"{common.get_test_id(cluster)}_disposable",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
        )
        #from IPython import embed; embed()
        return addrs


    def test_another_smash(self):
        # Initialize a SMASH client for cluster instance 0
        smash = smash_client.SmashClient.get_client()

        # Fetch metadata for a stake pool
        pool_id = "pool1g053jgx4qmcjxzqak3av23rs8ysh65qm8em2028qc2ldz3re4rq"
        pool_meta_hash = "0cf5e2d250905f1088c637d6b54149739f553fe7458fc0fae28ff107beccc430"

        metadata = smash.get_pool_metadata(pool_id, pool_meta_hash)
        if metadata:
            print(f"Metadata for pool {pool_id}: {metadata}")

        # Delist a pool
        #if smash.delist_pool(pool_id):
        #    print(f"Pool {pool_id} delisted successfully.")

        # Reserve a ticker
        #ticker_name = "SALAD"
        #pool_hash = "2560993cf1b6f3f1ebde429f062ce48751ed6551c2629ce62e4e169f140a3524"
        #if smash.reserve_ticker(ticker_name, pool_hash):
        #    print(f"Ticker {ticker_name} reserved for pool {pool_hash}.")

        # Close all SMASH clients
        #smash.SmashClient.close_all()


    def test_smash(self):
        smash_client = smash.SMASHClient(base_url="http://localhost:3100/api/v1", username="admin", password="password")
        # ✅ Fetch pool metadata
        metadata = smash_client.get_metadata("pool10alk47gm3maaaq2f80lks8sxyrdg5xm5u987g4u42jh0q9p8wzx", "0cf5e2d250905f1088c637d6b54149739f553fe7458fc0fae28ff107beccc430")
        print(metadata)


    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("submit_method", [pytest.param("cli")], ids=["submit_cli"])
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_isolated_transfer_all_funds(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs_disposable: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Send ALL funds from one payment address to another.

        * send all available funds from 1 source address to 1 destination address
        * check expected balance for destination addresses
        * check that balance for source address is 0 Lovelace
        * check output of the `transaction view` command
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs_disposable[1].address
        dst_address = payment_addrs_disposable[0].address

        # Amount value -1 means all available funds
        #from IPython import embed; embed()
        destinations = [clusterlib.TxOut(address=dst_address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs_disposable[1].skey_file])

        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=out_file_signed,
            txins=tx_raw_output.txins,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert not clusterlib.filter_utxos(utxos=out_utxos, address=src_address), (
            f"Incorrect balance for source address `{src_address}`"
        )
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for destination address `{dst_address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        # Check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    #@submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.parametrize("submit_method", [pytest.param("cli")], ids=["submit_cli"])
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_funds_to_valid_address(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Send funds to a valid payment address.

        The destination address is a valid address that was generated sometime
        in the past. The test verifies it is possible to use a valid address
        even though it was not generated while running a specific cardano
        network.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        * check min UTxO value
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = "addr_test1vpst87uzwafqkxumyf446zr2jsyn44cfpu9fe8yqanyuh6glj2hkl"

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=src_address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            txouts=destinations,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount, (
            f"Incorrect balance for destination address `{dst_address}`"
        )

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        # Check min UTxO value
        min_value = cluster.g_transaction.calculate_min_req_utxo(txouts=destinations)
        assert min_value.value in tx_common.MIN_UTXO_VALUE

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)