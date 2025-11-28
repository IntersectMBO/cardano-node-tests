"""Tests for transactions in mempool."""

import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


class TestMempool:
    """Tests for transactions in mempool."""

    @pytest.fixture
    def payment_addrs_locked(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create 2 new payment addresses for 'test_query_mempool_txin'."""
        cluster = cluster_singleton
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
            amount=1_000_000_000,
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_query_mempool_txin(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        payment_addrs_locked: list[clusterlib.AddressRecord],
    ):
        """Test that is possible to query txin of a transaction that is still in mempool.

        * check if 'query tx-mempool next-tx' is returning a TxId
        * check if 'query tx-mempool exists <TxId>' found the expected TxId
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        init_next_txid = ""
        # Make sure existing Txs are removed from mempool before submitting the new Tx
        cluster.wait_for_new_block(new_blocks=2)
        # Get the current `nextTx` before submitting the new Tx. There can be an earlier Tx
        # that is still "stuck" in mempool.
        init_next_txid = cluster.g_query.get_mempool_next_tx().get("nextTx") or ""

        src_addr = payment_addrs_locked[0]
        dst_addr = payment_addrs_locked[1]

        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]

        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

        # We want to submit the Tx and then re-submit it and see the expected error, to make sure
        # the Tx made it to mempool.
        for r in range(5):
            try:
                cluster.g_transaction.submit_tx_bare(tx_file=out_file_signed)
            except clusterlib.CLIError as exc:  # noqa: PERF203
                exc_str = str(exc)
                inputs_spent = (
                    "All inputs are spent" in exc_str  # In cardano-node >= 10.6.0
                    or "BadInputsUTxO" in exc_str
                )
                if r == 0 or not inputs_spent:
                    raise
                break
        else:
            msg = "Failed to make sure the Tx is in mempool"
            raise AssertionError(msg)

        # If the Tx is only in mempool, its txins can still be queried
        txin_queried = bool(cluster.g_query.get_utxo(utxo=tx_raw_output.txins[0]))

        if not txin_queried:
            pytest.skip("the Tx was removed from mempool before running `query utxo`")

        def _check_query_mempool() -> None:
            # Check 'query tx-mempool next-tx'
            mempool_next_tx = cluster.g_query.get_mempool_next_tx()
            next_txid = mempool_next_tx["nextTx"]
            if next_txid is None:
                LOGGER.warning(
                    "The Tx was removed from mempool before running `query tx-mempool next-tx`"
                )
                return
            assert next_txid in (
                txid,
                init_next_txid,
            ), f"The reported nextTx is '{mempool_next_tx['nextTx']}', expected '{txid}'"

            # Check 'query tx-mempool exists <TxId>'
            txid_exists_in_mempool = cluster.g_query.get_mempool_tx_exists(txid=txid)
            if not txid_exists_in_mempool["exists"]:
                LOGGER.warning(
                    "The Tx was removed from mempool before running `query tx-mempool exists`"
                )
                return
            assert txid_exists_in_mempool["txId"] == txid, (
                f"The reported txId is '{txid_exists_in_mempool['txId']}', expected '{txid}'"
            )

            # Check that the slot reported by the tx-mempool commands is correct
            assert mempool_next_tx["slot"] == txid_exists_in_mempool["slot"], (
                f"The slots reported by the 'tx-mempool' commands don't match: "
                f"{mempool_next_tx['slot']} vs {txid_exists_in_mempool['slot']}"
            )

        _check_query_mempool()

        # Make sure the txin is removed from mempool so it cannot be selected by other tests
        cluster.wait_for_new_block(new_blocks=2)
