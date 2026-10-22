"""Tests for Dijkstra transactions with embedded sub-transactions."""

import json

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import node_consistency
from cardano_node_tests.utils.versions import VERSIONS

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.DIJKSTRA_FIRST,
    reason="Sub-transactions are available only in Dijkstra+ eras",
)


@pytest.fixture(scope="module")
def require_sub_cli() -> None:
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The `cardano-cli` doesn't support sub-transactions.")


@pytest.mark.usefixtures("require_sub_cli")
class TestSubTransactions:
    """Test building and submitting transactions containing signed sub-transactions."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Cache two funded source addresses and a destination address."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=3,
            fund_idx=[0, 1],
            caching_key=helpers.get_current_line_str(),
            min_amount=10_000_000,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_build_raw_tx(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Build and submit a transaction containing a signed sub-transaction.

        * Collect inputs and balanced outputs, including change, for the sub-transaction,
          then build and sign it.
        * Build the top-level transaction with automatically selected inputs from a
          separate source address and the signed sub-transaction.
        * Check the embedded child and the batch balance with the top-level fee.
        * Sign and submit the top-level transaction, checking that signing preserves
          both transaction IDs.
        * Verify the child's inputs are spent and its payment and change outputs exist.
        * Verify the top-level source's change and transaction presence on all nodes.
        """
        temp_template = common.get_test_id(cluster)

        top_src, sub_src, dst_addr = payment_addrs

        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=2_000_000)]
        sub_tx_files = clusterlib.TxFiles(signing_key_files=[sub_src.skey_file])

        data_for_sub_tx = clusterlib.collect_data_for_build(
            clusterlib_obj=cluster,
            src_address=sub_src.address,
            txouts=txouts,
            tx_files=sub_tx_files,
        )
        assert data_for_sub_tx.txins, "No inputs available for the sub-transaction"

        sub_tx_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_sub_tx.body",
            txins=data_for_sub_tx.txins,
            txouts=data_for_sub_tx.txouts,
        )
        sub_tx_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=sub_tx_output.out_file,
            tx_name=temp_template,
            signing_key_files=sub_tx_files.signing_key_files,
        )

        sub_txid = cluster.g_sub_transaction.get_txid(sub_tx_file=sub_tx_output.out_file)
        assert sub_txid == cluster.g_sub_transaction.get_txid(signed_sub_tx_file=sub_tx_signed), (
            "Signing changed the sub-transaction ID"
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[top_src.skey_file])
        top_txouts = [clusterlib.TxOut(address=dst_addr.address, amount=3_000_000)]

        tx_output = cluster.g_transaction.build_raw_tx(
            src_address=top_src.address,
            tx_name=temp_template,
            txouts=top_txouts,
            tx_files=tx_files,
            fee=1_000_000,
            sub_transactions=[
                clusterlib.SignedSubTx(out_file=sub_tx_signed, tx_raw_output=sub_tx_output)
            ],
        )

        signed_sub_cbor = json.loads(sub_tx_signed.read_text(encoding="utf-8"))["cborHex"]
        top_cbor = json.loads(tx_output.out_file.read_text(encoding="utf-8"))["cborHex"]
        assert signed_sub_cbor in top_cbor, (
            "Signed sub-transaction is missing from the top-level body"
        )
        assert tx_output.signed_sub_tx_files == [sub_tx_signed]

        total_input = clusterlib.calculate_utxos_balance([*tx_output.txins, *sub_tx_output.txins])
        total_output = sum(t.amount for t in (*tx_output.txouts, *sub_tx_output.txouts))
        assert total_input == total_output + tx_output.fee, (
            "The top-level transaction does not balance the whole batch"
        )

        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        assert cluster.g_transaction.get_txid(
            tx_body_file=tx_output.out_file
        ) == cluster.g_transaction.get_txid(tx_file=out_file_signed), (
            "Signing changed the transaction ID"
        )

        cluster.g_transaction.submit_tx(
            tx_file=out_file_signed,
            txins=tx_output.txins,
        )

        assert not cluster.g_query.get_utxo(utxo=sub_tx_output.txins), (
            "Sub-transaction inputs were not spent"
        )
        sub_out_utxos = cluster.g_query.get_utxo(
            txin=[f"{sub_txid}#{ix}" for ix in range(sub_tx_output.txouts_count)]
        )
        assert sorted((u.address, u.amount, u.coin) for u in sub_out_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in sub_tx_output.txouts
        ), "Sub-transaction payment or change outputs are missing or incorrect"

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert clusterlib.filter_utxos(utxos=out_utxos, address=top_src.address)[
            0
        ].amount == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - sum(
            t.amount for t in top_txouts
        )

        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=tx_output)
