"""Tests for transactions chaining."""
import logging
import time
from typing import Tuple

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def payment_addr(
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


def _submit_no_wait(
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    txin: clusterlib.UTXOData,
    out_addr: clusterlib.AddressRecord,
    tx_name: str,
    fee: int,
) -> Tuple[clusterlib.UTXOData, clusterlib.TxRawOutput]:
    """Submit a Tx without waiting for it to appear on ledger.

    Return Tx output in a format that can be used as input for next Tx.
    """
    send_amount = txin.amount - fee
    out_file = f"{tx_name}_tx.body"

    # create Tx data
    txout = clusterlib.TxOut(address=out_addr.address, amount=send_amount)
    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

    # send Tx
    tx_raw_output = cluster_obj.build_raw_tx_bare(
        out_file=out_file,
        txouts=[txout],
        tx_files=tx_files,
        fee=fee,
        txins=[txin],
        invalid_hereafter=cluster_obj.calculate_tx_ttl()
        if VERSIONS.transaction_era < VERSIONS.ALLEGRA
        else None,
    )
    tx_signed_file = cluster_obj.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        tx_name=tx_name,
        signing_key_files=tx_files.signing_key_files,
    )
    cluster_obj.submit_tx_bare(tx_file=tx_signed_file)

    # transform output of this Tx (`TxOut`) to input for next Tx (`UTXOData`)
    txid = cluster_obj.get_txid(tx_body_file=tx_raw_output.out_file)
    out_utxo = clusterlib.UTXOData(
        utxo_hash=txid,
        utxo_ix=0,
        amount=send_amount,
        address=out_addr.address,
    )

    return out_utxo, tx_raw_output


class TestTxChaining:
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

        init_utxo = cluster.get_utxo(address=payment_addr.address)[0]

        assert (
            init_utxo.amount - (fee * iterations) >= min_utxo_value
        ), f"Not enough funds to do {iterations} iterations"

        tx_raw_outputs = []
        txin = next_txin = verified_txin = init_utxo

        # submit Txs one by one without waiting for them to appear on ledger
        for idx in range(1, iterations + 1):
            try:
                next_txin, tx_raw_output = _submit_no_wait(
                    cluster_obj=cluster,
                    payment_addr=payment_addr,
                    txin=txin,
                    out_addr=payment_addr,
                    tx_name=f"{temp_template}_{idx:04d}",
                    fee=fee,
                )
            except clusterlib.CLIError as err:
                # The "BadInputsUTxO" error can happen when the previous transaction has not made
                # it to mempool, so Tx input for this transaction doesn't exist. In that case,
                # revert `txin` to the output of last successful transaction.
                if "BadInputsUTxO" not in str(err):
                    raise
                txin = verified_txin
                continue

            verified_txin, txin = txin, next_txin
            tx_raw_outputs.append(tx_raw_output)

        if configuration.HAS_DBSYNC:
            # wait a bit for all Txs to appear in db-sync
            time.sleep(5)
            for t in tx_raw_outputs:
                dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=t)
