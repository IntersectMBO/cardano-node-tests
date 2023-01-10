"""Tests for transactions chaining."""
import logging
import time
from pathlib import Path
from typing import Optional
from typing import Tuple

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
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
    invalid_hereafter: Optional[int] = None,
) -> Tuple[clusterlib.UTXOData, clusterlib.TxRawOutput, Path]:
    """Generate Tx and return Tx output in a format that can be used as input for next Tx."""
    send_amount = txin.amount - fee
    out_file = f"{tx_name}_tx.body"

    # create Tx data
    txout = clusterlib.TxOut(address=out_addr.address, amount=send_amount)
    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

    # build Tx
    tx_raw_output = cluster_obj.g_transaction.build_raw_tx_bare(
        out_file=out_file,
        txouts=[txout],
        tx_files=tx_files,
        fee=fee,
        txins=[txin],
        invalid_hereafter=invalid_hereafter,
    )

    # sign Tx
    tx_file = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        tx_name=tx_name,
        signing_key_files=tx_files.signing_key_files,
    )

    # transform output of this Tx (`TxOut`) to input for next Tx (`UTXOData`)
    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
    out_utxo = clusterlib.UTXOData(
        utxo_hash=txid,
        utxo_ix=0,
        amount=send_amount,
        address=out_addr.address,
    )

    return out_utxo, tx_raw_output, tx_file


class TestTxChaining:
    @pytest.fixture
    def cluster(self, cluster_manager: cluster_management.ClusterManager) -> clusterlib.ClusterLib:
        return cluster_manager.get(
            lock_resources=[cluster_management.Resources.PERF],
        )

    @pytest.fixture
    def payment_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.AddressRecord:
        """Create new payment address."""
        amount = 205_000_000

        addr = clusterlib_utils.create_payment_addr_records(
            f"chain_tx_addr_ci{cluster_manager.cluster_instance_num}",
            cluster_obj=cluster,
        )[0]

        # fund source address
        clusterlib_utils.fund_from_faucet(
            addr,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=amount,
        )

        return addr

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_tx_chaining(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
    ):
        """Test transactions chaining.

        Submit Txs one by one without waiting for them to appear on ledger and use output of one
        Tx as input for the next Tx. As a result, Txs are using inputs that doesn't exist on ledger
        yet. Node needs to lookup the Tx in mempool and correctly chain the dependent transactons.
        """
        temp_template = common.get_test_id(cluster)

        fee = 200_000
        iterations = 1_000
        min_utxo_value = 1_000_000

        init_utxo = cluster.g_query.get_utxo(address=payment_addr.address)[0]

        assert (
            init_utxo.amount - (fee * iterations) >= min_utxo_value
        ), f"Not enough funds to do {iterations} iterations"

        invalid_hereafter = (
            cluster.g_query.get_slot_no() + 4000
            if VERSIONS.transaction_era < VERSIONS.ALLEGRA
            else None
        )

        # generate signed Txs
        generated_txs = []
        tx_raw_outputs = []
        txin = init_utxo
        for idx in range(1, iterations + 1):
            txin, tx_raw_output, tx_file = _gen_signed_tx(
                cluster_obj=cluster,
                payment_addr=payment_addr,
                txin=txin,
                out_addr=payment_addr,
                tx_name=f"{temp_template}_{idx:04d}",
                fee=fee,
                invalid_hereafter=invalid_hereafter,
            )
            generated_txs.append(tx_file)
            tx_raw_outputs.append(tx_raw_output)

        def _repeat_submit(tx_file: Path):
            # we want to submit the Tx and then re-submit it and see the expected error, to make
            # sure the Tx made it to mempool
            for r in range(5):
                try:
                    cluster.g_transaction.submit_tx_bare(tx_file=tx_file)
                except clusterlib.CLIError as exc:
                    err_str = str(exc)
                    if r == 0 and "(BadInputsUTxO" in err_str:
                        raise Exception("Tx input is missing, maybe temporary fork?") from exc
                    if "(BadInputsUTxO" not in err_str:
                        raise
                    break
            else:
                raise AssertionError("Failed to make sure the Tx is in mempool")

        # submit Txs one by one without waiting for them to appear on ledger
        for tx_file in generated_txs:
            _repeat_submit(tx_file)

        if configuration.HAS_DBSYNC:
            # wait a bit for all Txs to appear in db-sync
            time.sleep(5)

            check_tx_outs = [
                dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=t) for t in tx_raw_outputs
            ]

            block_ids = [r.block_id for r in check_tx_outs if r]
            assert block_ids == sorted(block_ids), "Block IDs of Txs are not ordered"

            how_many_blocks = block_ids[-1] - block_ids[0]
            assert how_many_blocks < iterations // 25, "Expected more chained Txs per block"
