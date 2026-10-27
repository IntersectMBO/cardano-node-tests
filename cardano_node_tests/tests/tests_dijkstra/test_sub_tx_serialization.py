"""Child identity, ordering, and supported canonical transaction workflows."""

import dataclasses
import json
import pathlib as pl

import allure
import cbor2
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests.tests_dijkstra import sub_tx_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import node_consistency
from cardano_node_tests.utils.versions import VERSIONS

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.DIJKSTRA_FIRST,
    reason="Sub-transactions are available only in Dijkstra+ eras",
)


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Cache two child owners, sponsor, and recipient with modest funding."""
    return addrs_common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=4,
        fund_idx=[0, 1, 2],
        amount=3_000_000,
        min_amount=2_500_000,
        caching_key=helpers.get_current_line_str(),
    )


@pytest.mark.usefixtures("require_sub_cli")
class TestSubTransactionIdentity:
    """Preserve independently signed child identities across wrapper and order changes."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("confirmed_order", (0, 1))
    def test_child_identity_across_wrappers(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        confirmed_order: int,
    ):
        """Confirm either order of unchanged children through independently signed wrappers.

        Record child IDs and predicted references before either wrapper exists.
        Build both sibling orders with different parent fees and change addresses,
        preserving the exact independently signed children. Confirm either variant
        on fresh test-owned inputs and check every predicted originating output.
        This key-payment test does not establish index-sensitive script behavior.
        """
        temp_template = common.get_test_id(cluster)
        first, second, sponsor, recipient = payment_addrs
        children = []
        for ix, owner in enumerate((first, second)):
            inputs = cluster.g_query.get_utxo(address=owner.address)
            children.append(
                sub_tx_utils.build_child(
                    cluster=cluster,
                    name=f"{temp_template}_child_{ix}",
                    txins=inputs,
                    txouts=[
                        clusterlib.TxOut(
                            address=recipient.address if ix == 0 else owner.address,
                            amount=clusterlib.calculate_utxos_balance(inputs),
                        )
                    ],
                    signing_keys=[owner.skey_file],
                )
            )
        original_bytes = [c.signed_file.read_bytes() for c in children]
        decoded_children = [
            cbor2.loads(bytes.fromhex(json.loads(b)["cborHex"])) for b in original_bytes
        ]
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        wrappers = []

        # Change serialized positions and parent terms without re-signing either child.
        for order in (0, 1):
            selected = children if order == 0 else list(reversed(children))
            fee_lovelace = 500_000 + order * 100_000
            output, signed = sub_tx_utils.build_parent(
                cluster=cluster,
                name=f"{temp_template}_wrapper_{order}",
                sponsor=sponsor,
                txins=sponsor_inputs,
                txouts=[
                    clusterlib.TxOut(
                        address=sponsor.address if order == 0 else first.address,
                        amount=sponsor_lovelace - fee_lovelace,
                    )
                ],
                children=selected,
                fee_lovelace=fee_lovelace,
            )
            encoded = bytes.fromhex(json.loads(signed.read_text())["cborHex"])
            batch = cbor2.loads(encoded)
            assert batch[0][23] == (
                decoded_children if order == 0 else list(reversed(decoded_children))
            )
            assert batch[0][2] == fee_lovelace
            assert all(bytes.fromhex(json.loads(b)["cborHex"]) in encoded for b in original_bytes)
            wrappers.append((output, signed))

        # Separate parameter cases confirm both variants without resetting ledger state.
        output, signed = wrappers[confirmed_order]
        cluster.g_transaction.submit_tx(tx_file=signed, txins=sponsor_inputs)
        assert not cluster.g_query.get_utxo(
            utxo=[*sponsor_inputs, *(u for c in children for u in c.output.txins)]
        )
        for child, original in zip(children, original_bytes, strict=True):
            sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
            assert child.signed_file.read_bytes() == original
        assert [(u.address, u.amount) for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
            (t.address, t.amount) for t in output.txouts
        ]
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)


@pytest.mark.usefixtures("require_sub_cli")
class TestSubTransactionCanonicalWorkflows:
    """Build canonical children before signing and preserve them through parent tooling."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("signing_path", ("body", "tx_file", "witness"))
    def test_canonical_child_workflow(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        signing_path: str,
    ):
        """Confirm canonical metadata-bearing children through three signing paths.

        Canonicalize a child at construction, then sign its fixed body. Embed
        it in a canonical parent and use direct body signing, adding signatures
        through a transaction file, or separate witness/assembly. Check exact
        child bytes, metadata/hash association and predicted outputs at each
        outer operation before confirming through the node. Script-purpose
        matrices and noncanonical rewrite refusal are separate coverage gaps.
        """
        temp_template = common.get_test_id(cluster)
        owner, _, sponsor, recipient = payment_addrs
        owner_inputs = cluster.g_query.get_utxo(address=owner.address)
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        owner_lovelace = clusterlib.calculate_utxos_balance(owner_inputs)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        metadata = pl.Path(f"{temp_template}_metadata.json")
        metadata.write_text(json.dumps({"0": {"message": "canonical child"}}), encoding="utf-8")

        # Canonicalization is safe before the user authorizes the child's final body.
        child_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_child.body",
            txins=owner_inputs,
            txouts=[clusterlib.TxOut(address=recipient.address, amount=owner_lovelace)],
            tx_files=clusterlib.TxFiles(metadata_json_files=[metadata]),
            out_canonical_cbor=True,
        )
        child_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=child_output.out_file,
            tx_name=f"{temp_template}_child",
            signing_key_files=[owner.skey_file],
        )
        child = sub_tx_utils.SignedChild(
            output=child_output,
            signed_file=child_signed,
            txid=cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed),
        )
        child_bytes = child_signed.read_bytes()
        child_cbor = bytes.fromhex(json.loads(child_bytes)["cborHex"])
        decoded_child = cbor2.loads(child_cbor)
        assert 7 in decoded_child[0] and decoded_child[2] is not None

        # Obtain the normal framework output record, then construct canonical parent bytes.
        output = cluster.g_transaction.build_raw_tx(
            src_address=sponsor.address,
            tx_name=f"{temp_template}_parent_seed",
            txins=sponsor_inputs,
            txouts=[clusterlib.TxOut(address=sponsor.address, amount=sponsor_lovelace - 500_000)],
            sub_transaction_files=[child_signed],
            fee=500_000,
        )
        parent_file = pl.Path(f"{temp_template}_canonical_parent.body")
        input_args = [
            arg for u in sponsor_inputs for arg in ("--tx-in", f"{u.utxo_hash}#{u.utxo_ix}")
        ]
        cluster.cli(
            [
                "transaction",
                "build-raw",
                *input_args,
                "--tx-out",
                f"{sponsor.address}+{sponsor_lovelace - 500_000}",
                "--fee",
                "500000",
                "--signed-sub-tx-file",
                str(child_signed),
                "--out-canonical-cbor",
                "--out-file",
                str(parent_file),
            ]
        )
        output = dataclasses.replace(output, out_file=parent_file)

        def _check_embedded(tx_file: pl.Path) -> None:
            """Keep the signed body, local witnesses and metadata association exact."""
            encoded = bytes.fromhex(json.loads(tx_file.read_text())["cborHex"])
            assert child_cbor in encoded
            embedded = cbor2.loads(encoded)[0][23]
            assert embedded == [decoded_child]
            assert embedded[0][0][7] == decoded_child[0][7]
            assert embedded[0][2] == decoded_child[2]

        _check_embedded(parent_file)
        signed = pl.Path(f"{temp_template}_canonical_parent.signed")
        if signing_path == "witness":
            witness = cluster.g_transaction.witness_tx(
                tx_body_file=parent_file,
                witness_name=f"{temp_template}_sponsor",
                signing_key_files=[sponsor.skey_file],
            )
            cluster.cli(
                [
                    "transaction",
                    "assemble",
                    "--tx-body-file",
                    str(parent_file),
                    "--witness-file",
                    str(witness),
                    "--out-canonical-cbor",
                    "--out-file",
                    str(signed),
                ]
            )
        else:
            source_flag = "--tx-body-file"
            source_file = parent_file
            if signing_path == "tx_file":
                source_file = cluster.g_transaction.sign_tx(
                    tx_body_file=parent_file,
                    tx_name=f"{temp_template}_unsigned_envelope",
                    signing_key_files=[],
                )
                source_flag = "--tx-file"
                _check_embedded(source_file)
            cluster.cli(
                [
                    "transaction",
                    "sign",
                    source_flag,
                    str(source_file),
                    "--signing-key-file",
                    str(sponsor.skey_file),
                    "--out-canonical-cbor",
                    "--out-file",
                    str(signed),
                ]
            )
        _check_embedded(signed)

        # CLI round-trips are not enough: confirm the fully signed batch and exact outputs.
        cluster.g_transaction.submit_tx(tx_file=signed, txins=sponsor_inputs)
        assert not cluster.g_query.get_utxo(utxo=[*owner_inputs, *sponsor_inputs])
        sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
        assert [u.amount for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
            sponsor_lovelace - output.fee
        ]
        assert child_signed.read_bytes() == child_bytes
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)
