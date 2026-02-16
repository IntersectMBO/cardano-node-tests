"""Tests for transactions chaining."""

import logging
import pathlib as pl
import time

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


def _gen_signed_tx(
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    txin: clusterlib.UTXOData,
    out_addr: clusterlib.AddressRecord,
    tx_name: str,
    fee: int,
    invalid_hereafter: int | None = None,
) -> tuple[clusterlib.UTXOData, clusterlib.TxRawOutput, pl.Path]:
    """Generate Tx and return Tx output in a format that can be used as input for next Tx."""
    send_amount = txin.amount - fee
    out_file = f"{tx_name}_tx.body"

    # Create Tx data
    txout = clusterlib.TxOut(address=out_addr.address, amount=send_amount)
    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

    # Build Tx
    tx_raw_output = cluster_obj.g_transaction.build_raw_tx_bare(
        out_file=out_file,
        txouts=[txout],
        tx_files=tx_files,
        fee=fee,
        txins=[txin],
        invalid_hereafter=invalid_hereafter,
    )

    # Sign Tx
    tx_file = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        tx_name=tx_name,
        signing_key_files=tx_files.signing_key_files,
    )

    # Transform output of this Tx (`TxOut`) to input for next Tx (`UTXOData`)
    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
    out_utxo = clusterlib.UTXOData(
        utxo_hash=txid,
        utxo_ix=0,
        amount=send_amount,
        address=out_addr.address,
    )

    return out_utxo, tx_raw_output, tx_file


def _repeat_submit(cluster_obj: clusterlib.ClusterLib, tx_file: pl.Path) -> str:
    """Submit the Tx and then re-submit it.

    Expect and error on re-submit. This way we make sure the Tx made it to mempool.
    """
    err_str = ""
    for r in range(5):
        try:
            cluster_obj.g_transaction.submit_tx_bare(tx_file=tx_file)
        except clusterlib.CLIError as exc:
            exc_str = str(exc)
            inputs_spent = (
                "All inputs are spent" in exc_str  # In cardano-node >= 10.6.0
                or "BadInputsUTxO" in exc_str
            )
            if r == 0 and inputs_spent:
                err_str = "Tx input is missing, maybe temporary fork happened?"
            elif inputs_spent:
                break
            raise
        if r > 2:
            time.sleep(0.05)
    else:
        err_str = "Failed to make sure the Tx is in mempool"

    return err_str


class TestTxChaining:
    @pytest.fixture
    def cluster(self, cluster_manager: cluster_management.ClusterManager) -> clusterlib.ClusterLib:
        return cluster_manager.get(
            lock_resources=[cluster_management.Resources.PERF],
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_tx_chaining(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ):
        """Test transaction chaining with 1000 dependent transactions in mempool.

        Submit Txs one by one without waiting for them to appear on ledger and use output of one
        Tx as input for the next Tx. As a result, Txs are using inputs that don't exist on ledger
        yet. Node needs to lookup the Tx in mempool and correctly chain the dependent transactions.

        * Create payment address with 205 ADA
        * Generate 1000 signed transactions where each uses output of previous transaction
        * Submit all transactions to mempool without waiting for ledger confirmation
        * Verify node can chain dependent transactions through mempool lookups
        * Check balances after all transactions are processed
        * (optional) Check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        fee = 200_000
        iterations = 1_000
        min_utxo_value = 1_000_000

        tx_raw_outputs: list[clusterlib.TxRawOutput] = []
        submit_err = ""

        # It can happen that a Tx is removed from mempool without making it to the blockchain.
        # The whole chain of transactions is than distrupted. We'll just repeat the whole
        # process when this happens.
        for rep in range(3):
            if rep > 0:
                LOGGER.warning(f"Repeating Tx chaining for the {rep} time.")

            next_try = False
            tx_raw_outputs.clear()

            payment_addr = common.get_payment_addr(
                name_template=temp_template,
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                amount=205_000_000,
            )
            init_utxo = cluster.g_query.get_utxo(address=payment_addr.address)[0]
            assert init_utxo.amount - (fee * iterations) >= min_utxo_value, (
                f"Not enough funds to do {iterations} iterations"
            )

            # Generate signed Txs
            invalid_hereafter = (
                cluster.g_query.get_slot_no() + 4000
                if VERSIONS.transaction_era < VERSIONS.ALLEGRA
                else None
            )
            generated_txs = []
            txin = init_utxo
            for idx in range(1, iterations + 1):
                txin, tx_raw_output, tx_file = _gen_signed_tx(
                    cluster_obj=cluster,
                    payment_addr=payment_addr,
                    txin=txin,
                    out_addr=payment_addr,
                    tx_name=f"{temp_template}_r{rep}_{idx:04d}",
                    fee=fee,
                    invalid_hereafter=invalid_hereafter,
                )
                generated_txs.append(tx_file)
                tx_raw_outputs.append(tx_raw_output)

            # Submit Txs one by one without waiting for them to appear on ledger
            for tx_file in generated_txs:
                submit_err = _repeat_submit(cluster_obj=cluster, tx_file=tx_file)
                if submit_err:
                    next_try = True
                    break

            if next_try:
                continue

            break
        else:
            if submit_err:
                raise AssertionError(submit_err)

        if configuration.HAS_DBSYNC:
            # Wait a bit for all Txs to appear in db-sync
            time.sleep(5)

            check_tx_outs = [
                dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=t) for t in tx_raw_outputs
            ]

            block_ids = [r.block_id for r in check_tx_outs if r]
            assert block_ids == sorted(block_ids), "Block IDs of Txs are not ordered"

            how_many_blocks = block_ids[-1] - block_ids[0]
            assert how_many_blocks < iterations // 25, "Expected more chained Txs per block"
