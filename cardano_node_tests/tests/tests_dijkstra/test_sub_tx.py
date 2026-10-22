"""Tests for Dijkstra transactions with embedded sub-transactions."""

import json

import allure
import cbor2
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import node_consistency
from cardano_node_tests.utils.versions import VERSIONS

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.DIJKSTRA_FIRST,
    reason="Sub-transactions are available only in Dijkstra+ eras",
)


@pytest.fixture(scope="module")
def require_sub_cli() -> None:
    """Require CLI support for building and signing sub-transactions."""
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
    @pytest.mark.dbsync
    def test_sponsored_payment(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Settle Alice's independently signed payment with a sponsor's fee.

        * Alice signs a balanced child payment without fee or collateral fields.
        * The sponsor builds and signs a parent containing only its own change.
        * Check the serialized child is unchanged and only the sponsor witnesses
          the parent. Alice never signs the parent.
        * Submit and check every parent and child input is spent, the recipient
          and Alice receive the signed outputs under the child ID, and the sponsor
          receives its input value minus exactly the parent fee as change.
        """
        temp_template = common.get_test_id(cluster)
        sponsor, alice, recipient = payment_addrs
        payment_amount = 2_000_000
        fee = 1_000_000

        # Build Alice's balanced payment and sign it independently of the sponsor.
        child_data = clusterlib.collect_data_for_build(
            clusterlib_obj=cluster,
            src_address=alice.address,
            txouts=[clusterlib.TxOut(address=recipient.address, amount=payment_amount)],
        )
        assert child_data.txins, "No inputs available for Alice's payment"
        child_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_child.body",
            txins=child_data.txins,
            txouts=child_data.txouts,
        )
        child_body_bytes = child_output.out_file.read_bytes()
        child_id = cluster.g_sub_transaction.get_txid(sub_tx_file=child_output.out_file)
        child_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=child_output.out_file,
            tx_name=temp_template,
            signing_key_files=[alice.skey_file],
        )

        # Record the signed child and check that Alice contributes no fee or collateral.
        child_signed_bytes = child_signed.read_bytes()
        child_cbor = json.loads(child_signed_bytes)["cborHex"]
        child = cbor2.loads(bytes.fromhex(child_cbor))
        # CDDL body keys: fee, collateral inputs, collateral return, total collateral.
        assert not {2, 13, 16, 17}.intersection(child[0]), (
            "Alice's child contains fee or collateral fields"
        )
        assert cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed) == child_id
        child_input_amount = clusterlib.calculate_utxos_balance(child_output.txins)
        assert sum(t.amount for t in child_output.txouts) == child_input_amount, (
            "Alice's child is not independently balanced"
        )

        # Wrap the signed child in a parent funded and signed only by the sponsor.
        parent_files = clusterlib.TxFiles(signing_key_files=[sponsor.skey_file])
        parent_output = cluster.g_transaction.build_raw_tx(
            src_address=sponsor.address,
            tx_name=temp_template,
            tx_files=parent_files,
            fee=fee,
            sub_transactions=[
                clusterlib.SignedSubTx(out_file=child_signed, tx_raw_output=child_output)
            ],
        )
        parent_signed = cluster.g_transaction.sign_tx(
            tx_body_file=parent_output.out_file,
            signing_key_files=parent_files.signing_key_files,
            tx_name=temp_template,
        )

        # Check child byte preservation and the separate child and parent witnesses.
        parent_cbor = json.loads(parent_signed.read_text(encoding="utf-8"))["cborHex"]
        parent = cbor2.loads(bytes.fromhex(parent_cbor))
        assert child_cbor in parent_cbor, "Assembly or signing changed the signed child bytes"
        assert child_output.out_file.read_bytes() == child_body_bytes
        assert child_signed.read_bytes() == child_signed_bytes
        assert parent_output.signed_sub_tx_files == [child_signed]
        sponsor_vkey = cbor2.loads(
            bytes.fromhex(json.loads(sponsor.vkey_file.read_text(encoding="utf-8"))["cborHex"])
        )
        alice_vkey = cbor2.loads(
            bytes.fromhex(json.loads(alice.vkey_file.read_text(encoding="utf-8"))["cborHex"])
        )
        assert sponsor_vkey != alice_vkey, "Alice and the sponsor must use independent keys"
        assert {w[0] for w in child[1][0]} == {alice_vkey}, "Alice must sign the child only"
        assert {w[0] for w in parent[1][0]} == {sponsor_vkey}, (
            "Only the sponsor may witness the parent"
        )
        assert parent[0][2] == fee == parent_output.fee
        parent_id = cluster.g_transaction.get_txid(tx_body_file=parent_output.out_file)
        assert cluster.g_transaction.get_txid(tx_file=parent_signed) == parent_id

        # Submit the complete batch and verify that all spending inputs are consumed.
        cluster.g_transaction.submit_tx(tx_file=parent_signed, txins=parent_output.txins)

        assert not cluster.g_query.get_utxo(utxo=[*parent_output.txins, *child_output.txins]), (
            "Parent or child inputs were not spent"
        )

        # Query payment and change under the child's independently computed ID.
        child_utxos = cluster.g_query.get_utxo(
            txin=[f"{child_id}#{ix}" for ix in range(child_output.txouts_count)]
        )
        assert sorted((u.address, u.amount, u.coin) for u in child_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in child_output.txouts
        ), "Confirmed child outputs differ from Alice's signed payment and change"
        assert (
            clusterlib.calculate_utxos_balance(
                clusterlib.filter_utxos(utxos=child_utxos, address=recipient.address)
            )
            == payment_amount
        )
        assert (
            clusterlib.calculate_utxos_balance(
                clusterlib.filter_utxos(utxos=child_utxos, address=alice.address)
            )
            == child_input_amount - payment_amount
        )

        # The sponsor's sole output must return its inputs minus exactly the parent fee.
        parent_utxos = cluster.g_query.get_utxo(tx_raw_output=parent_output)
        assert len(parent_utxos) == 1, "Expected only the sponsor's change output"
        assert parent_utxos[0].address == sponsor.address
        assert parent_utxos[0].utxo_hash == parent_id
        assert parent_utxos[0].amount == (
            clusterlib.calculate_utxos_balance(parent_output.txins) - fee
        ), "The sponsor did not pay exactly the parent fee"

        # Check node agreement and optional parent indexing in db-sync.
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=parent_output)

        # Child indexing is not supported by the existing db-sync helpers.
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=parent_output)

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
