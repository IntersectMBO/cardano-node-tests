"""Tests for transactions in mempool."""
import logging
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@pytest.mark.testnets
class TestMempool:
    """Tests for transactions in mempool."""

    @pytest.fixture
    def payment_addrs_locked(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses for 'test_query_mempool_txin'."""
        temp_template = common.get_test_id(cluster_singleton)

        addrs = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_addr_0",
            f"{temp_template}_addr_1",
            cluster_obj=cluster_singleton,
        )

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *addrs,
            cluster_obj=cluster_singleton,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    def test_query_mempool_txin(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        payment_addrs_locked: List[clusterlib.AddressRecord],
    ):
        """Test that is possible to query txin of a transaction that is still in mempool.

        * check if 'query tx-mempool next-tx' is returning a TxId
        * check if 'query tx-mempool exists <TxId>' found the expected TxId
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_addr = payment_addrs_locked[0]
        dst_addr = payment_addrs_locked[1]

        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        destinations = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]

        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_addr.address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_addr.address,
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

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

        # we want to submit the Tx and then re-submit it and see the expected error, to make sure
        # the Tx made it to mempool
        for r in range(5):
            try:
                cluster.g_transaction.submit_tx_bare(tx_file=out_file_signed)
            except clusterlib.CLIError as exc:
                if r == 0 or "(BadInputsUTxO" not in str(exc):
                    raise
                break
        else:
            raise AssertionError("Failed to make sure the Tx is in mempool")

        # if the Tx is only in mempool, its txins can still be queried
        txin_queried = bool(cluster.g_query.get_utxo(utxo=tx_raw_output.txins[0]))

        if not txin_queried:
            pytest.skip("the Tx was removed from mempool before running `query utxo`")

        def _check_query_mempool() -> None:
            if not clusterlib_utils.cli_has("query tx-mempool"):
                return

            # check 'query tx-mempool next-tx'
            mempool_next_tx = cluster.g_query.get_mempool_next_tx()
            next_txid = mempool_next_tx["nextTx"]
            if next_txid is None:
                LOGGER.warning(
                    "The Tx was removed from mempool before running `query tx-mempool next-tx`"
                )
                return
            assert (
                next_txid == txid
            ), f"The reported nextTx is '{mempool_next_tx['nextTx']}', expected '{txid}'"

            # check 'query tx-mempool exists <TxId>'
            txid_exists_in_mempool = cluster.g_query.get_mempool_tx_exists(txid=txid)
            if not txid_exists_in_mempool["exists"]:
                LOGGER.warning(
                    "The Tx was removed from mempool before running `query tx-mempool exists`"
                )
                return
            assert (
                txid_exists_in_mempool["txId"] == txid
            ), f"The reported txId is '{txid_exists_in_mempool['txId']}', expected '{txid}'"

            # check that the slot reported by the tx-mempool commands is correct
            assert mempool_next_tx["slot"] == txid_exists_in_mempool["slot"], (
                f"The slots reported by the 'tx-mempool' commands don't match: "
                f"{mempool_next_tx['slot']} vs {txid_exists_in_mempool['slot']}"
            )

        _check_query_mempool()

        # make sure the txin is removed from mempool so it cannot be selected by other tests
        cluster.wait_for_new_block(new_blocks=2)
