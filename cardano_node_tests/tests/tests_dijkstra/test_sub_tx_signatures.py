"""Signature isolation for child bodies, witnesses, and required guard arguments."""

import dataclasses
import io
import json
import pathlib as pl

import allure
import cbor2
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import markers
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import node_consistency
from cardano_node_tests.utils.versions import VERSIONS

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.DIJKSTRA_FIRST,
    reason="Sub-transactions are available only in Dijkstra+ eras",
)


@pytest.mark.usefixtures("require_sub_cli")
class TestSubTransactionSignatures:
    """Keep child authorization independent of parent assembly."""

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
            # Explicit top-ups avoid the default funding of three times min_amount.
            amount=6_000_000,
            min_amount=5_000_000,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_signature_isolation(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        subtests: pytest_subtests.SubTests,
    ):
        """Reject unauthorized child changes while allowing independently signed wrappers.

        * Build a valid payment child with metadata and record its body and witness bytes.
        * Change its output, validity interval, or metadata hash independently, retaining
          the original witness. Keep metadata and batch balances otherwise valid.
        * Remove or corrupt the child signature; separately sign the parent with the
          child's key but leave the child without a witness.
        * Check node signature failures leave inputs unspent and create no outputs.
        * Change only the parent fee and change address, sign with the sponsor alone,
          and confirm the original child with its exact payment and metadata.

        Required-guard argument mutations are covered by TestSubTransactionGuardSignatures.
        """
        temp_template = common.get_test_id(cluster)
        sponsor, user, recipient = payment_addrs
        payment_lovelace = 2_000_000
        fee = 500_000
        metadata_file = pl.Path(f"{temp_template}_metadata.json")
        metadata_file.write_text(json.dumps({"0": {"message": "original"}}), encoding="utf-8")
        changed_metadata_file = pl.Path(f"{temp_template}_changed_metadata.json")
        changed_metadata_file.write_text(
            json.dumps({"0": {"message": "changed"}}), encoding="utf-8"
        )

        # Prepare the balanced child independently of the sponsor's inputs and wrapper.
        child_data = clusterlib.collect_data_for_build(
            clusterlib_obj=cluster,
            src_address=user.address,
            txouts=[clusterlib.TxOut(address=recipient.address, amount=payment_lovelace)],
        )
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        inputs = [*child_data.txins, *sponsor_inputs]
        inputs_before = cluster.g_query.get_utxo(utxo=inputs)

        def _build_child(
            name: str,
            txouts: list[clusterlib.TxOut],
            invalid_before: int = 0,
            metadata: pl.Path = metadata_file,
            sign: bool = False,
        ) -> tuple[clusterlib.SubTxRawOutput, pl.Path]:
            """Build a child, adding the user's signature only for the original baseline."""
            output = cluster.g_sub_transaction.build_raw_tx_bare(
                out_file=f"{temp_template}_{name}_child.body",
                txins=child_data.txins,
                txouts=txouts,
                invalid_before=invalid_before,
                tx_files=clusterlib.TxFiles(metadata_json_files=[metadata]),
            )
            signed = cluster.g_sub_transaction.sign_tx(
                sub_tx_file=output.out_file,
                tx_name=f"{temp_template}_{name}",
                signing_key_files=[user.skey_file] if sign else [],
            )
            return output, signed

        def _child_parts(signed: pl.Path) -> tuple[bytes, bytes, bytes]:
            """Extract child body, witness, and auxiliary-data CBOR without re-encoding."""
            envelope = json.loads(signed.read_text(encoding="utf-8"))
            encoded = bytes.fromhex(envelope["cborHex"])
            stream = io.BytesIO(encoded)
            # A signed child is the fixed three-element array [body, witnesses, metadata].
            assert stream.read(1) == b"\x83", "Unexpected signed child array encoding"
            decoder = cbor2.CBORDecoder(stream)
            parts = []
            for _ in range(3):
                start = stream.tell()
                decoder.decode()
                parts.append(encoded[start : stream.tell()])
            assert stream.tell() == len(encoded), "Unexpected trailing child CBOR"
            return parts[0], parts[1], parts[2]

        child_output, child_signed = _build_child("original", child_data.txouts, sign=True)
        child_bytes = child_signed.read_bytes()
        original_body, original_witnesses, original_metadata = _child_parts(child_signed)
        original_envelope = json.loads(child_bytes)

        def _wrap(
            name: str,
            signed_child: pl.Path,
            parent_user_key: bool = False,
            changed_parent: bool = False,
        ) -> tuple[clusterlib.TxRawOutput, pl.Path]:
            """Sign the wrapper without repairing any supplied child body or witnesses."""
            parent_fee = fee + 100_000 if changed_parent else fee
            output = cluster.g_transaction.build_raw_tx(
                src_address=sponsor.address,
                tx_name=f"{temp_template}_{name}_parent",
                txins=sponsor_inputs,
                txouts=[
                    clusterlib.TxOut(
                        address=user.address if changed_parent else sponsor.address,
                        amount=sponsor_lovelace - parent_fee,
                    )
                ],
                fee=parent_fee,
                sub_transaction_files=[signed_child],
            )
            signed = cluster.g_transaction.sign_tx(
                tx_body_file=output.out_file,
                signing_key_files=[
                    sponsor.skey_file,
                    *([user.skey_file] if parent_user_key else []),
                ],
                tx_name=f"{temp_template}_{name}_parent",
            )
            child_cbor = json.loads(signed_child.read_text(encoding="utf-8"))["cborHex"]
            assert child_cbor in json.loads(signed.read_text(encoding="utf-8"))["cborHex"], (
                "The parent builder changed the supplied child"
            )
            return output, signed

        # Construct the valid baseline before making controlled wire-level mutations.
        baseline_parent, _ = _wrap("baseline", child_signed)
        redirected_txouts = [
            dataclasses.replace(t, address=sponsor.address) if t.address == recipient.address else t
            for t in child_data.txouts
        ]
        current_slot = cluster.g_query.get_slot_no()
        assert current_slot > 0, "The validity mutation needs a nonzero current slot"
        body_mutations = [
            ("output", _build_child("output", redirected_txouts)[1]),
            (
                "validity",
                _build_child(
                    "validity", child_data.txouts, invalid_before=max(1, current_slot - 1)
                )[1],
            ),
            (
                "metadata_hash",
                _build_child("metadata", child_data.txouts, metadata=changed_metadata_file)[1],
            ),
        ]
        mutations: list[tuple[str, bytes, bytes, bytes, bool, str]] = []
        original_fields = cbor2.loads(original_body)
        for name, mutated_child in body_mutations:
            body, _, metadata = _child_parts(mutated_child)
            mutated_fields = cbor2.loads(body)
            # Body keys: outputs, validity lower bound, and auxiliary-data hash.
            expected_key = {"output": 1, "validity": 8, "metadata_hash": 7}[name]
            assert {
                key
                for key in original_fields.keys() | mutated_fields.keys()
                if original_fields.get(key) != mutated_fields.get(key)
            } == {expected_key}, "The mutation did not isolate the intended child field"
            # Retain the old signature but keep auxiliary metadata consistent with its hash.
            mutations.append(
                (name, body, original_witnesses, metadata, False, "SubInvalidWitnessesUTXOW")
            )

        # Witness-only mutations preserve the exact body bytes signed by the user.
        witnesses = cbor2.loads(original_witnesses)
        key, signature = next(iter(witnesses[0]))
        corrupted_signature = bytes([signature[0] ^ 1]) + signature[1:]
        witnesses[0] = cbor2.CBORTag(258, [[key, corrupted_signature]])
        for name, witness_bytes, parent_user_key, error in (
            ("missing_child_witness", cbor2.dumps({}), False, "SubMissingVKeyWitnessesUTXOW"),
            ("invalid_child_signature", cbor2.dumps(witnesses), False, "SubInvalidWitnessesUTXOW"),
            ("user_key_only_in_parent", cbor2.dumps({}), True, "SubMissingVKeyWitnessesUTXOW"),
        ):
            mutations.append(
                (name, original_body, witness_bytes, original_metadata, parent_user_key, error)
            )

        # All failures must originate in node witness validation, not local CLI decoding.
        for name, body, witness_bytes, metadata, parent_user_key, error in mutations:
            with subtests.test(scenario=name):
                mutated_child = pl.Path(f"{temp_template}_{name}_mutated.signed")
                mutated_child.write_text(
                    json.dumps(
                        {
                            **original_envelope,
                            "cborHex": (b"\x83" + body + witness_bytes + metadata).hex(),
                        }
                    ),
                    encoding="utf-8",
                )
                parent_output, parent_signed = _wrap(name, mutated_child, parent_user_key)
                with pytest.raises(clusterlib.CLIError, match=error):
                    cluster.g_transaction.submit_tx_bare(tx_file=parent_signed)

                child_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=mutated_child)
                assert cluster.g_query.get_utxo(utxo=inputs) == inputs_before
                assert not cluster.g_query.get_utxo(tx_raw_output=parent_output)
                assert not cluster.g_query.get_utxo(
                    txin=[f"{child_id}#{ix}" for ix in range(child_output.txouts_count)]
                )

        # Parent-only changes need no new user signature and preserve the original metadata.
        changed_output, changed_signed = _wrap("changed", child_signed, changed_parent=True)
        assert changed_output.txouts != baseline_parent.txouts
        cluster.g_transaction.submit_tx(tx_file=changed_signed, txins=sponsor_inputs)
        assert not cluster.g_query.get_utxo(utxo=inputs)
        child_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed)
        child_utxos = cluster.g_query.get_utxo(
            txin=[f"{child_id}#{ix}" for ix in range(child_output.txouts_count)]
        )
        assert sorted((u.address, u.amount, u.coin) for u in child_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in child_output.txouts
        ), "Parent changes altered the child's payment or change"
        parent_utxos = cluster.g_query.get_utxo(tx_raw_output=changed_output)
        assert [(u.address, u.amount, u.coin) for u in parent_utxos] == [
            (user.address, sponsor_lovelace - changed_output.fee, clusterlib.DEFAULT_COIN)
        ]
        assert child_signed.read_bytes() == child_bytes

        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=changed_output)
        # Child outputs are checked through the node; only the parent has a db-sync helper.
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=changed_output)


@pytest.mark.usefixtures("require_sub_cli")
class TestSubTransactionGuardSignatures:
    """Preserve signed child authorization of required Plutus guard arguments."""

    @pytest.fixture
    def cluster_plutus(
        self,
        cluster_manager: cluster_management.ClusterManager,
    ) -> clusterlib.ClusterLib:
        """Protect Plutus execution parameters while running guarded transactions."""
        return cluster_manager.get(use_resources=[cluster_management.Resources.PLUTUS])

    @pytest.fixture
    def plutus_guard_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_plutus: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Cache sponsor, user, recipient, and separate sponsor collateral addresses."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster_plutus),
            cluster_manager=cluster_manager,
            cluster_obj=cluster_plutus,
            num=4,
            fund_idx=[0, 1, 3],
            caching_key=helpers.get_current_line_str(),
            amount=3_000_000,
            min_amount=2_500_000,
        )

    @allure.link(helpers.get_vcs_link())
    @markers.SKIPIF_PLUTUSV4_UNUSABLE
    @pytest.mark.plutus
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_required_guard_signature_isolation(
        self,
        cluster_plutus: clusterlib.ClusterLib,
        plutus_guard_addrs: list[clusterlib.AddressRecord],
        subtests: pytest_subtests.SubTests,
    ):
        """Reject a changed required-guard argument with the original child signature.

        * Sign a balanced payment child requiring an always-succeeding V4 guard.
        * Change only its required guard datum, preserving original witness bytes.
        * Supply the parent guard and collateral and check node signature rejection,
          unchanged owned UTxOs, and absence of parent and child outputs.
        * Settle the unchanged original child through a sponsor-only signed parent,
          checking the payment, sponsor fee, and untouched collateral.
        """
        cluster = cluster_plutus
        temp_template = common.get_test_id(cluster)
        sponsor, user, recipient, collateral = plutus_guard_addrs
        script_file = plutus_common.ALWAYS_SUCCEEDS["v4"].script_file
        fee_lovelace = 500_000
        payment_lovelace = 2_000_000

        # Select explicit inputs and change without the selector's preferred-change buffer.
        user_inputs = cluster.g_query.get_utxo(address=user.address)
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        collateral_inputs = cluster.g_query.get_utxo(address=collateral.address)
        user_lovelace = clusterlib.calculate_utxos_balance(user_inputs)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        child_txouts = [
            clusterlib.TxOut(address=recipient.address, amount=payment_lovelace),
            clusterlib.TxOut(address=user.address, amount=user_lovelace - payment_lovelace),
        ]
        owned_addresses = [a.address for a in plutus_guard_addrs]
        state_before = cluster.g_query.get_utxo(address=owned_addresses)

        def _parts(tx_file: pl.Path, count: int) -> tuple[bytes, ...]:
            """Read serialized transaction fields without changing signed child bytes."""
            encoded = bytes.fromhex(json.loads(tx_file.read_text(encoding="utf-8"))["cborHex"])
            stream = io.BytesIO(encoded)
            assert stream.read(1) == bytes([0x80 + count]), "Unexpected transaction encoding"
            decoder = cbor2.CBORDecoder(stream)
            parts = []
            for _ in range(count):
                start = stream.tell()
                decoder.decode()
                parts.append(encoded[start : stream.tell()])
            assert stream.tell() == len(encoded), "Unexpected trailing transaction CBOR"
            return tuple(parts)

        # Build both arguments through the CLI so the mutation changes only body field 24.
        children = []
        for name, datum in (("original", "42"), ("changed_argument", "43")):
            output = cluster.g_sub_transaction.build_raw_tx_bare(
                out_file=f"{temp_template}_{name}_child.body",
                txins=user_inputs,
                txouts=child_txouts,
                top_level_guards=[
                    clusterlib.RequiredTopLevelGuard(script_file=script_file, datum_value=datum)
                ],
            )
            signed = cluster.g_sub_transaction.sign_tx(
                sub_tx_file=output.out_file,
                tx_name=f"{temp_template}_{name}",
                signing_key_files=[user.skey_file] if name == "original" else [],
            )
            children.append((output, signed))

        child_output, child_signed = children[0]
        changed_output, changed_signed = children[1]
        child_bytes = child_signed.read_bytes()
        original_body, original_witnesses, metadata = _parts(child_signed, 3)
        changed_body, _, changed_metadata = _parts(changed_signed, 3)
        original_fields = cbor2.loads(original_body)
        changed_fields = cbor2.loads(changed_body)
        assert {
            k
            for k in original_fields.keys() | changed_fields.keys()
            if original_fields.get(k) != changed_fields.get(k)
        } == {24}, "The mutation changed more than the required-guard argument"
        assert original_fields[24].keys() == changed_fields[24].keys()
        assert metadata == changed_metadata

        mutated_child = pl.Path(f"{temp_template}_stale_signature_child.signed")
        mutated_child.write_text(
            json.dumps(
                {
                    **json.loads(child_bytes),
                    "cborHex": (b"\x83" + changed_body + original_witnesses + metadata).hex(),
                }
            ),
            encoding="utf-8",
        )
        assert _parts(mutated_child, 3) == (changed_body, original_witnesses, metadata)

        # The parent CLI lacks guards. Obtain guard witnesses and their integrity hash
        # from a CLI-built seed, then transplant only those fields into each parent.
        guard_seed = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_guard_seed.body",
            txins=sponsor_inputs,
            txouts=[clusterlib.TxOut(address=sponsor.address, amount=sponsor_lovelace)],
            guards=[
                clusterlib.TxGuard(
                    script_file=script_file, redeemer_value="0", execution_units=(1_000_000, 10_000)
                )
            ],
        )
        seed_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=guard_seed.out_file, tx_name=f"{temp_template}_guard_seed"
        )
        seed_body, guard_witnesses, _ = _parts(seed_signed, 3)
        seed_fields = cbor2.loads(seed_body)
        assert seed_fields[14] == set(original_fields[24])

        def _wrap(name: str, signed_child: pl.Path) -> tuple[clusterlib.TxRawOutput, pl.Path]:
            """Add the CLI-generated guard without re-encoding the embedded child."""
            output = cluster.g_transaction.build_raw_tx(
                src_address=sponsor.address,
                tx_name=f"{temp_template}_{name}_skeleton",
                txins=sponsor_inputs,
                txouts=[
                    clusterlib.TxOut(
                        address=sponsor.address, amount=sponsor_lovelace - fee_lovelace
                    )
                ],
                collateral_txins=collateral_inputs,
                fee=fee_lovelace,
                sub_transaction_files=[signed_child],
            )
            skeleton = cluster.g_transaction.sign_tx(
                tx_body_file=output.out_file,
                tx_name=f"{temp_template}_{name}_skeleton",
                signing_key_files=[],
            )
            envelope = json.loads(skeleton.read_text(encoding="utf-8"))
            body, _, auxiliary = _parts(skeleton, 3)
            fields = cbor2.loads(body)
            assert 11 not in fields and 14 not in fields
            assert body[0] == 0xA0 + len(fields) and len(fields) + 2 < 24
            guarded_body = (
                bytes([0xA0 + len(fields) + 2])
                + body[1:]
                + cbor2.dumps(11)
                + cbor2.dumps(seed_fields[11])
                + cbor2.dumps(14)
                + cbor2.dumps(seed_fields[14])
            )
            guarded_file = pl.Path(f"{temp_template}_{name}_guarded.tx")
            guarded_file.write_text(
                json.dumps(
                    {
                        **envelope,
                        "cborHex": (b"\x83" + guarded_body + guard_witnesses + auxiliary).hex(),
                    }
                ),
                encoding="utf-8",
            )
            signed = cluster.g_transaction.sign_tx(
                tx_file=guarded_file,
                tx_name=f"{temp_template}_{name}_parent",
                signing_key_files=[sponsor.skey_file, collateral.skey_file],
            )
            child_cbor = json.loads(signed_child.read_text(encoding="utf-8"))["cborHex"]
            assert child_cbor in json.loads(signed.read_text(encoding="utf-8"))["cborHex"]
            return dataclasses.replace(output, out_file=guarded_file), signed

        # An always-succeeding guard accepts either datum, but cannot authorize a stale signature.
        rejected_output, rejected_signed = _wrap("stale_signature", mutated_child)
        with (
            subtests.test(scenario="changed_required_guard_argument"),
            pytest.raises(clusterlib.CLIError, match="SubInvalidWitnessesUTXOW"),
        ):
            cluster.g_transaction.submit_tx_bare(tx_file=rejected_signed)

        assert cluster.g_query.get_utxo(address=owned_addresses) == state_before
        assert not cluster.g_query.get_utxo(tx_raw_output=rejected_output)
        changed_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=changed_signed)
        assert not cluster.g_query.get_utxo(
            txin=[f"{changed_id}#{ix}" for ix in range(changed_output.txouts_count)]
        )

        # Confirm the original signed child with the fully witnessed parent guard.
        parent_output, parent_signed = _wrap("original", child_signed)
        cluster.g_transaction.submit_tx(tx_file=parent_signed, txins=sponsor_inputs)
        assert not cluster.g_query.get_utxo(utxo=[*user_inputs, *sponsor_inputs])
        assert sorted(cluster.g_query.get_utxo(utxo=collateral_inputs)) == sorted(collateral_inputs)
        child_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed)
        child_utxos = cluster.g_query.get_utxo(
            txin=[f"{child_id}#{ix}" for ix in range(child_output.txouts_count)]
        )
        assert sorted((u.address, u.amount, u.coin) for u in child_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in child_txouts
        )
        assert [
            (u.address, u.amount) for u in cluster.g_query.get_utxo(tx_raw_output=parent_output)
        ] == [(sponsor.address, sponsor_lovelace - fee_lovelace)]
        assert child_signed.read_bytes() == child_bytes
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=parent_output)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=parent_output)
