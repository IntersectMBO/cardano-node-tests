"""Tests for Dijkstra transactions with embedded sub-transactions."""

import concurrent.futures
import dataclasses
import io
import json
import pathlib as pl
import threading

import allure
import cbor2
import pytest
import pytest_subtests
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
            # Explicit top-ups avoid the default funding of three times min_amount.
            amount=6_000_000,
            min_amount=5_000_000,
        )

    @pytest.fixture
    def swap_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Cache traders and issuer; fund only counterparties and issuer as needed."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=4,
            fund_idx=[1, 2, 3],
            caching_key=helpers.get_current_line_str(),
            amount=20_000_000,
            min_amount=18_000_000,
        )

    @pytest.fixture
    def babel_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Cache user, providers, recipient, and issuer; fund only the issuer."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=5,
            fund_idx=[4],
            caching_key=helpers.get_current_line_str(),
            amount=15_000_000,
            min_amount=13_000_000,
        )

    @pytest.fixture
    def clearing_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Cache three traders, aggregator fee/liquidity addresses, and a funded issuer."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=6,
            fund_idx=[5],
            caching_key=helpers.get_current_line_str(),
            amount=15_000_000,
            min_amount=13_000_000,
        )

    @pytest.fixture
    def replay_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Cache a user, three independently funded assemblers, and a recipient."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=5,
            fund_idx=[0, 1, 2, 3],
            caching_key=helpers.get_current_line_str(),
            amount=3_000_000,
            min_amount=2_500_000,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_replay_competing_wrappers(
        self,
        cluster: clusterlib.ClusterLib,
        replay_addrs: list[clusterlib.AddressRecord],
        subtests: pytest_subtests.SubTests,
    ):
        """Settle a raced child payment once and reject replay through any wrapper.

        * Publish one independently signed child payment and build two parents
          with distinct assembler keys and disjoint parent spending inputs.
        * Race their submissions to the same node and confirm exactly one parent.
        * Check the child payment and winning parent change, consumed inputs, and
          untouched funding inputs of the losing and third assemblers.
        * Build a fresh third parent around the unchanged, now-spent child.
        * Retry both raced parents and submit the third. Check spent-input errors,
          unchanged test-owned UTxOs, and no losing or third-parent outputs.

        This payment-only batch has no certificate or governance effects.
        """
        temp_template = common.get_test_id(cluster)
        user, first, second, third, recipient = replay_addrs
        fee = 500_000
        payment_lovelace = 2_000_000

        # Sign once before any assembler builds a wrapper around the published child.
        child_data = clusterlib.collect_data_for_build(
            clusterlib_obj=cluster,
            src_address=user.address,
            txouts=[clusterlib.TxOut(address=recipient.address, amount=payment_lovelace)],
        )
        child_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_child.body",
            txins=child_data.txins,
            txouts=child_data.txouts,
        )
        child_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=child_output.out_file,
            tx_name=f"{temp_template}_published",
            signing_key_files=[user.skey_file],
        )
        child_bytes = child_signed.read_bytes()
        child_cbor = json.loads(child_bytes)["cborHex"]
        child_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed)
        child_refs = [f"{child_id}#{ix}" for ix in range(child_output.txouts_count)]
        assemblers = (first, second, third)
        funding_inputs = [cluster.g_query.get_utxo(address=a.address) for a in assemblers]
        assert all(funding_inputs), "An assembler has no funding inputs"
        input_refs = [{(u.utxo_hash, u.utxo_ix) for u in txins} for txins in funding_inputs]
        assert all(
            not input_refs[ix].intersection(input_refs[other])
            for ix in range(3)
            for other in range(ix + 1, 3)
        ), "Parent funding inputs overlap"

        def _build_wrapper(name: str, assembler_ix: int) -> tuple[clusterlib.TxRawOutput, pl.Path]:
            """Independently fund and sign a parent without re-signing its child."""
            assembler = assemblers[assembler_ix]
            txins = funding_inputs[assembler_ix]
            output = cluster.g_transaction.build_raw_tx(
                src_address=assembler.address,
                tx_name=f"{temp_template}_{name}",
                txins=txins,
                txouts=[
                    clusterlib.TxOut(
                        address=assembler.address,
                        amount=clusterlib.calculate_utxos_balance(txins) - fee,
                    )
                ],
                fee=fee,
                sub_transaction_files=[child_signed],
            )
            signed = cluster.g_transaction.sign_tx(
                tx_body_file=output.out_file,
                signing_key_files=[assembler.skey_file],
                tx_name=f"{temp_template}_{name}",
            )
            assert child_cbor in json.loads(signed.read_text(encoding="utf-8"))["cborHex"], (
                "The assembler changed the published child"
            )
            return output, signed

        # The barrier launches both CLI submissions without waiting for either to finish.
        wrappers = [_build_wrapper("first", 0), _build_wrapper("second", 1)]
        start = threading.Barrier(2)

        def _submit(signed: pl.Path) -> str:
            """Submit at the race barrier and return any node rejection for inspection."""
            start.wait(timeout=10)
            try:
                cluster.g_transaction.submit_tx_bare(tx_file=signed)
            except clusterlib.CLIError as exc:
                return str(exc)
            return ""

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(_submit, signed) for _, signed in wrappers]
            errors = [future.result() for future in futures]

        assert any(not error for error in errors), f"Neither raced parent was admitted: {errors}"
        for error in errors:
            assert not error or helpers.is_inputs_spent_err(error), error

        # Admission alone is not confirmation. Wait for the shared child's inputs to be spent.
        clusterlib_utils.check_txins_spent(cluster_obj=cluster, txins=child_output.txins)
        confirmed_outputs = [
            cluster.g_query.get_utxo(tx_raw_output=output) for output, _ in wrappers
        ]
        winners = [ix for ix, utxos in enumerate(confirmed_outputs) if utxos]
        assert len(winners) == 1, "The race did not confirm exactly one parent"
        winner = winners[0]
        loser = 1 - winner
        winning_output, _ = wrappers[winner]
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=winning_output)

        child_utxos = cluster.g_query.get_utxo(txin=child_refs)
        assert sorted((u.address, u.amount, u.coin) for u in child_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in child_output.txouts
        ), "The child payment or change is missing or incorrect"
        assert [(u.address, u.amount, u.coin) for u in confirmed_outputs[winner]] == [
            (
                assemblers[winner].address,
                clusterlib.calculate_utxos_balance(funding_inputs[winner]) - fee,
                clusterlib.DEFAULT_COIN,
            )
        ], "The winning assembler did not pay exactly the parent fee"
        assert not cluster.g_query.get_utxo(utxo=funding_inputs[winner])
        unused_inputs = [*funding_inputs[loser], *funding_inputs[2]]
        unused_before = cluster.g_query.get_utxo(utxo=unused_inputs)
        assert sorted(unused_before) == sorted(unused_inputs), (
            "The race consumed an unused assembler's funding inputs"
        )

        # Build a fresh wrapper after confirmation, using a still-unspent third funding input.
        third_output, third_signed = _build_wrapper("third_after_confirmation", 2)
        owned_addresses = [a.address for a in replay_addrs]
        state_after_settlement = cluster.g_query.get_utxo(address=owned_addresses)
        retries = [
            ("winning_parent_all_inputs_spent", wrappers[winner][0], wrappers[winner][1]),
            ("losing_parent_input_unspent", wrappers[loser][0], wrappers[loser][1]),
            ("fresh_third_parent", third_output, third_signed),
        ]

        # Replays must neither charge an unused assembler nor duplicate the child's outputs.
        for name, output, signed in retries:
            with subtests.test(scenario=name):
                with pytest.raises(clusterlib.CLIError) as excinfo:
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)
                assert helpers.is_inputs_spent_err(str(excinfo.value)), str(excinfo.value)

                assert cluster.g_query.get_utxo(address=owned_addresses) == state_after_settlement
                assert cluster.g_query.get_utxo(utxo=unused_inputs) == unused_before
                assert cluster.g_query.get_utxo(txin=child_refs) == child_utxos
                if name != "winning_parent_all_inputs_spent":
                    assert not cluster.g_query.get_utxo(tx_raw_output=output)

        assert child_signed.read_bytes() == child_bytes
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=winning_output)

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

        Required-guard argument mutations need a valid PlutusV4 guard baseline and
        are not covered by this key-only test.
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

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("needs_liquidity", (False, True), ids=("cycle", "liquidity"))
    def test_multi_party_clearing(
        self,
        cluster: clusterlib.ClusterLib,
        clearing_addrs: list[clusterlib.AddressRecord],
        needs_liquidity: bool,
        subtests: pytest_subtests.SubTests,
    ):
        """Clear three independent offers atomically, optionally using aggregator liquidity.

        * Prepare offers exchanging A for B, B for C, and C for A. Each participant
          signs only its own child and publishes it for independent assembly.
        * In the liquidity variant, Alice offers one extra A and demands one extra B;
          the aggregator supplies that B and receives the extra A in parent change.
        * Build the complete valid batch, then omit each offer in turn and remove
          required liquidity. Check node rejection leaves every input unspent and
          creates no parent or child outputs.
        * Settle the complete batch and check all signed participant outputs,
          aggregator change and compensation, spent inputs, and unused liquidity.
        """
        temp_template = common.get_test_id(cluster)
        alice, bob, carol, aggregator, liquidity_addr, issuer = clearing_addrs
        participants = (alice, bob, carol)
        names = ("alice", "bob", "carol")
        participant_lovelace = 2_000_000
        aggregator_lovelace = 3_000_000
        liquidity_lovelace = 2_000_000
        trade_amount = 10
        alice_amount = trade_amount + int(needs_liquidity)
        fee = 500_000

        # Prepare all assets and spending inputs on the ledger before publishing offers.
        tokens = clusterlib_utils.new_tokens(
            *[clusterlib.get_rand_str(8).encode().hex() for _ in participants],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_assets",
            token_mint_addr=issuer,
            issuer_addr=issuer,
            amount=trade_amount + 1,
        )
        asset_a, asset_b, asset_c = [t.token for t in tokens]
        setup_txouts = [
            clusterlib.TxOut(address=aggregator.address, amount=aggregator_lovelace),
            clusterlib.TxOut(address=liquidity_addr.address, amount=liquidity_lovelace),
            clusterlib.TxOut(address=liquidity_addr.address, amount=1, coin=asset_b),
        ]
        for participant, asset, amount in zip(
            participants,
            (asset_a, asset_b, asset_c),
            (alice_amount, trade_amount, trade_amount),
            strict=True,
        ):
            # Join each ADA/token pair into a single participant UTxO.
            setup_txouts.extend(
                [
                    clusterlib.TxOut(address=participant.address, amount=participant_lovelace),
                    clusterlib.TxOut(address=participant.address, amount=amount, coin=asset),
                ]
            )
        setup_output = cluster.g_transaction.send_tx(
            src_address=issuer.address,
            tx_name=f"{temp_template}_setup",
            txouts=setup_txouts,
            tx_files=clusterlib.TxFiles(signing_key_files=[issuer.skey_file]),
        )
        setup_utxos = cluster.g_query.get_utxo(tx_raw_output=setup_output)
        fee_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=aggregator.address)
        liquidity_inputs = clusterlib.filter_utxos(
            utxos=setup_utxos, address=liquidity_addr.address
        )

        # Each trader fixes its own payout and signs without seeing the surrounding batch.
        offers: list[clusterlib.SubTxRawOutput] = []
        published_offers: list[pl.Path] = []
        published_bytes: list[bytes] = []
        offer_ids: list[str] = []
        for name, participant, wanted_asset, amount in zip(
            names,
            participants,
            (asset_b, asset_c, asset_a),
            (alice_amount, trade_amount, trade_amount),
            strict=True,
        ):
            offer = cluster.g_sub_transaction.build_raw_tx_bare(
                out_file=f"{temp_template}_{name}_offer.body",
                txins=clusterlib.filter_utxos(utxos=setup_utxos, address=participant.address),
                txouts=[
                    clusterlib.TxOut(address=participant.address, amount=participant_lovelace),
                    clusterlib.TxOut(address=participant.address, amount=amount, coin=wanted_asset),
                ],
            )
            signed_offer = cluster.g_sub_transaction.sign_tx(
                sub_tx_file=offer.out_file,
                tx_name=f"{temp_template}_{name}_published",
                signing_key_files=[participant.skey_file],
            )
            offers.append(offer)
            published_offers.append(signed_offer)
            published_bytes.append(signed_offer.read_bytes())
            offer_ids.append(cluster.g_sub_transaction.get_txid(signed_sub_tx_file=signed_offer))

        def _assemble(
            name: str, selected: list[int], include_liquidity: bool
        ) -> tuple[clusterlib.TxRawOutput, pl.Path]:
            """Assemble published offers using only aggregator-controlled signing keys."""
            txins = [*fee_inputs, *(liquidity_inputs if include_liquidity else [])]
            txouts = [
                clusterlib.TxOut(
                    address=aggregator.address,
                    amount=clusterlib.calculate_utxos_balance(txins) - fee,
                )
            ]
            if needs_liquidity:
                txouts.append(clusterlib.TxOut(address=aggregator.address, amount=1, coin=asset_a))
            parent_output = cluster.g_transaction.build_raw_tx(
                src_address=aggregator.address,
                tx_name=f"{temp_template}_{name}",
                txins=txins,
                txouts=txouts,
                fee=fee,
                sub_transaction_files=[published_offers[ix] for ix in selected],
            )
            parent_signed = cluster.g_transaction.sign_tx(
                tx_body_file=parent_output.out_file,
                signing_key_files=[
                    aggregator.skey_file,
                    *([liquidity_addr.skey_file] if include_liquidity else []),
                ],
                tx_name=f"{temp_template}_{name}",
            )
            parent_cbor = json.loads(parent_signed.read_text(encoding="utf-8"))["cborHex"]
            for ix in selected:
                assert json.loads(published_bytes[ix])["cborHex"] in parent_cbor, (
                    "Assembly changed a participant's signed offer"
                )
            return parent_output, parent_signed

        # Establish the full funded baseline before constructing incomplete batches.
        parent_output, parent_signed = _assemble("complete", [0, 1, 2], needs_liquidity)
        all_inputs = [u for offer in offers for u in offer.txins] + fee_inputs + liquidity_inputs
        inputs_before = cluster.g_query.get_utxo(utxo=all_inputs)
        child_refs = [
            f"{txid}#{ix}"
            for txid, offer in zip(offer_ids, offers, strict=True)
            for ix in range(offer.txouts_count)
        ]

        # Missing offers leave asset deficits that parent ADA change cannot cover.
        invalid_batches = [
            (f"omit_{name}", [ix for ix in range(3) if ix != omitted], needs_liquidity)
            for omitted, name in enumerate(names)
        ]
        if needs_liquidity:
            invalid_batches.append(("missing_liquidity", [0, 1, 2], False))
        for name, selected, include_liquidity in invalid_batches:
            with subtests.test(scenario=name):
                invalid_output, invalid_signed = _assemble(name, selected, include_liquidity)
                with pytest.raises(clusterlib.CLIError, match="ValueNotConservedUTxO"):
                    cluster.g_transaction.submit_tx_bare(tx_file=invalid_signed)

                assert cluster.g_query.get_utxo(utxo=all_inputs) == inputs_before
                assert not cluster.g_query.get_utxo(txin=child_refs)
                assert not cluster.g_query.get_utxo(tx_raw_output=invalid_output)

        # Settle all three offers atomically and verify every participant's fixed payout.
        cluster.g_transaction.submit_tx(tx_file=parent_signed, txins=parent_output.txins)
        assert not cluster.g_query.get_utxo(
            utxo=[u for offer in offers for u in offer.txins] + parent_output.txins
        ), "Some inputs of the complete clearing batch remain unspent"
        for txid, offer, published, original in zip(
            offer_ids, offers, published_offers, published_bytes, strict=True
        ):
            utxos = cluster.g_query.get_utxo(
                txin=[f"{txid}#{ix}" for ix in range(offer.txouts_count)]
            )
            assert sorted((u.address, u.amount, u.coin) for u in utxos) == sorted(
                (t.address, t.amount, t.coin) for t in offer.txouts
            ), "A participant did not receive its independently signed payout"
            assert published.read_bytes() == original

        # The aggregator pays the ADA fee and, when needed, exchanges one B for one A.
        parent_utxos = cluster.g_query.get_utxo(tx_raw_output=parent_output)
        assert sorted((u.address, u.amount, u.coin) for u in parent_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in parent_output.txouts
        ), "Aggregator change or liquidity compensation is incorrect"
        if not needs_liquidity:
            assert cluster.g_query.get_utxo(utxo=liquidity_inputs) == liquidity_inputs

        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=parent_output)
        # Child payouts are checked through the node; only the parent has a db-sync helper.
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=parent_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("output_kind", ("assets", "inline_datum", "reference_script"))
    def test_min_utxo_sponsorship(
        self,
        cluster: clusterlib.ClusterLib,
        swap_addrs: list[clusterlib.AddressRecord],
        output_kind: str,
    ):
        """Sponsor child outputs at their minimum ADA without changing signed terms.

        * Prepare one token-bearing input and two asset-bearing child outputs,
          optionally carrying a large inline datum or native reference script.
        * Calculate each output's minimum ADA using current protocol parameters.
          The child cannot fund both outputs; the sponsor covers the deficit and fee.
        * Build a valid baseline, then reduce one child output by one lovelace and
          add that lovelace to parent change, preserving complete-batch balance.
        * Check node rejection leaves all inputs unspent and creates no outputs,
          despite sufficient ADA elsewhere in the batch.
        * Submit the original signed child and verify exact-minimum outputs,
          attached data/scripts, and the sponsor's exact contribution.
        """
        temp_template = common.get_test_id(cluster)
        user, sponsor, recipient, issuer = swap_addrs
        fee = 500_000

        # Mint two tokens, one for each independently checked child output.
        token = clusterlib_utils.new_tokens(
            clusterlib.get_rand_str(8).encode().hex(),
            cluster_obj=cluster,
            temp_template=f"{temp_template}_asset",
            token_mint_addr=issuer,
            issuer_addr=issuer,
            amount=2,
        )[0].token

        # Size the actual output contents, not an estimate based only on asset count.
        output_template = clusterlib.TxOut(address=user.address, amount=2_000_000)
        datum = {"bytes": "ab" * 512}
        script = {
            "type": "all",
            "scripts": [
                {
                    "type": "sig",
                    "keyHash": cluster.g_address.get_payment_vkey_hash(
                        payment_vkey_file=issuer.vkey_file
                    ),
                }
            ]
            * 32,
        }
        if output_kind == "inline_datum":
            datum_file = pl.Path(f"{temp_template}_datum.json")
            datum_file.write_text(json.dumps(datum), encoding="utf-8")
            output_template = dataclasses.replace(output_template, inline_datum_file=datum_file)
        elif output_kind == "reference_script":
            script_file = pl.Path(f"{temp_template}_reference.script")
            script_file.write_text(json.dumps(script), encoding="utf-8")
            output_template = dataclasses.replace(
                output_template, reference_script_file=script_file
            )

        child_txouts = []
        for addr in (user, recipient):
            lovelace_output = dataclasses.replace(output_template, address=addr.address)
            # Matching attributes join the ADA and token records into one UTxO.
            asset_output = dataclasses.replace(lovelace_output, amount=1, coin=token)
            minimum = cluster.g_transaction.calculate_min_req_utxo(
                txouts=[lovelace_output, asset_output]
            ).value
            lovelace_output = dataclasses.replace(lovelace_output, amount=minimum)
            assert (
                cluster.g_transaction.calculate_min_req_utxo(
                    txouts=[lovelace_output, asset_output]
                ).value
                == minimum
            ), "Minimum ADA changed after setting the output amount"
            child_txouts.extend([lovelace_output, asset_output])

        # The input funds only one plain asset-bearing UTxO, not both child outputs.
        source_txouts = [
            clusterlib.TxOut(address=user.address, amount=2_000_000),
            clusterlib.TxOut(address=user.address, amount=2, coin=token),
        ]
        source_minimum = cluster.g_transaction.calculate_min_req_utxo(txouts=source_txouts).value
        source_txouts[0] = dataclasses.replace(source_txouts[0], amount=source_minimum)
        setup_output = cluster.g_transaction.send_tx(
            src_address=issuer.address,
            tx_name=f"{temp_template}_setup",
            txouts=source_txouts,
            tx_files=clusterlib.TxFiles(signing_key_files=[issuer.skey_file]),
        )
        user_inputs = clusterlib.filter_utxos(
            utxos=cluster.g_query.get_utxo(tx_raw_output=setup_output), address=user.address
        )
        sponsor_utxos = cluster.g_query.get_utxo(address=sponsor.address)
        asset_refs = {
            (u.utxo_hash, u.utxo_ix) for u in sponsor_utxos if u.coin != clusterlib.DEFAULT_COIN
        }
        sponsor_inputs = [u for u in sponsor_utxos if (u.utxo_hash, u.utxo_ix) not in asset_refs]
        child_lovelace = sum(t.amount for t in child_txouts if t.coin == clusterlib.DEFAULT_COIN)
        deficit = child_lovelace - clusterlib.calculate_utxos_balance(user_inputs)
        assert deficit > 0, "The child does not need ADA sponsorship"
        sponsor_change = clusterlib.calculate_utxos_balance(sponsor_inputs) - deficit - fee
        assert sponsor_change >= 2_000_000, "Not enough surplus ADA for the negative control"
        inputs_before = cluster.g_query.get_utxo(utxo=[*user_inputs, *sponsor_inputs])

        def _build_batch(
            name: str, txouts: list[clusterlib.TxOut], change: int
        ) -> tuple[clusterlib.SubTxRawOutput, pl.Path, clusterlib.TxRawOutput, pl.Path]:
            """Sign fixed child outputs and wrap them without automatic balancing."""
            name_template = f"{temp_template}_{name}"
            child_output = cluster.g_sub_transaction.build_raw_tx_bare(
                out_file=f"{name_template}_child.body", txins=user_inputs, txouts=txouts
            )
            child_signed = cluster.g_sub_transaction.sign_tx(
                sub_tx_file=child_output.out_file,
                tx_name=name_template,
                signing_key_files=[user.skey_file],
            )
            parent_output = cluster.g_transaction.build_raw_tx(
                src_address=sponsor.address,
                tx_name=name_template,
                txins=sponsor_inputs,
                txouts=[clusterlib.TxOut(address=sponsor.address, amount=change)],
                tx_files=clusterlib.TxFiles(signing_key_files=[sponsor.skey_file]),
                fee=fee,
                sub_transaction_files=[child_signed],
            )
            parent_signed = cluster.g_transaction.sign_tx(
                tx_body_file=parent_output.out_file,
                signing_key_files=[sponsor.skey_file],
                tx_name=name_template,
            )
            child_cbor = json.loads(child_signed.read_text(encoding="utf-8"))["cborHex"]
            parent_cbor = json.loads(parent_signed.read_text(encoding="utf-8"))["cborHex"]
            assert child_cbor in parent_cbor, "The sponsor changed the signed child"
            assert clusterlib.calculate_utxos_balance([*user_inputs, *sponsor_inputs]) == (
                sum(t.amount for t in child_output.txouts if t.coin == clusterlib.DEFAULT_COIN)
                + change
                + fee
            ), "The batch is not balanced in ADA"
            return child_output, child_signed, parent_output, parent_signed

        # A balanced parent cannot repair an individual child's undersized output.
        child_output, child_signed, parent_output, parent_signed = _build_batch(
            "minimum", child_txouts, sponsor_change
        )
        child_bytes = child_signed.read_bytes()
        invalid_txouts = list(child_txouts)
        invalid_txouts[0] = dataclasses.replace(
            invalid_txouts[0], amount=invalid_txouts[0].amount - 1
        )
        invalid_child, invalid_signed_child, invalid_parent, invalid_signed = _build_batch(
            "below_minimum", invalid_txouts, sponsor_change + 1
        )
        with pytest.raises(clusterlib.CLIError, match="OutputTooSmallUTxO"):
            cluster.g_transaction.submit_tx_bare(tx_file=invalid_signed)

        invalid_child_id = cluster.g_sub_transaction.get_txid(
            signed_sub_tx_file=invalid_signed_child
        )
        assert cluster.g_query.get_utxo(utxo=[*user_inputs, *sponsor_inputs]) == inputs_before
        assert not cluster.g_query.get_utxo(tx_raw_output=invalid_parent)
        assert not cluster.g_query.get_utxo(
            txin=[f"{invalid_child_id}#{ix}" for ix in range(invalid_child.txouts_count)]
        )

        # The unchanged valid baseline must settle with both outputs exactly at minimum ADA.
        cluster.g_transaction.submit_tx(tx_file=parent_signed, txins=sponsor_inputs)
        assert not cluster.g_query.get_utxo(utxo=[*user_inputs, *sponsor_inputs])
        child_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed)
        child_utxos = cluster.g_query.get_utxo(
            txin=[f"{child_id}#{ix}" for ix in range(child_output.txouts_count)]
        )
        assert child_output.txouts_count == 2
        assert sorted((u.address, u.amount, u.coin) for u in child_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in child_output.txouts
        ), "Confirmed child outputs differ from the signed minimum-ADA outputs"
        for utxo in child_utxos:
            if utxo.coin != clusterlib.DEFAULT_COIN:
                continue
            if output_kind == "inline_datum":
                assert utxo.inline_datum == datum, "The child's inline datum changed"
            elif output_kind == "reference_script":
                assert utxo.reference_script, "The child's reference script is missing"
                # Node queries return a CBOR envelope, not the original native-script JSON.
                clusterlib_utils.check_reference_script_policyid(
                    name_template=f"{temp_template}_check_{utxo.utxo_ix}",
                    cluster_obj=cluster,
                    script_file=output_template.reference_script_file,
                    script_data=utxo.reference_script["script"],
                )

        parent_utxos = cluster.g_query.get_utxo(tx_raw_output=parent_output)
        assert [(u.address, u.amount, u.coin) for u in parent_utxos] == [
            (sponsor.address, sponsor_change, clusterlib.DEFAULT_COIN)
        ], "The sponsor did not pay exactly the child's deficit and parent fee"
        assert child_signed.read_bytes() == child_bytes

        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=parent_output)
        # Child outputs are checked through the node; only the parent has a db-sync helper.
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=parent_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_babel_payment(
        self,
        cluster: clusterlib.ClusterLib,
        babel_addrs: list[clusterlib.AddressRecord],
        subtests: pytest_subtests.SubTests,
    ):
        """Pay a recipient using a token offer and provider-funded ADA and fees.

        * Give the user a token-bearing input but no separate ADA-only input.
        * Publish a signed child fixing the recipient payment, token change, and
          provider compensation. The child needs additional ADA, not collateral.
        * Model a provider declining the offer without any on-chain reservation.
        * Build a valid wrapper, then submit variants lacking fee funding, using
          insufficient provider ADA, or claiming excess token compensation.
          Check node rejection leaves all inputs and outputs unchanged.
        * Settle the original child without another user signature and verify
          exact recipient and provider outputs and an ADA-denominated parent fee.
        """
        temp_template = common.get_test_id(cluster)
        user, provider, poor_provider, recipient, issuer = babel_addrs
        user_lovelace = 4_000_000
        payment_amount = 3_000_000
        output_lovelace = 2_000_000
        provider_lovelace = 6_000_000
        compensation = 5
        fee = 500_000
        lovelace_deficit = payment_amount + 2 * output_lovelace - user_lovelace

        # Prepare one multi-asset user UTxO and dedicated ADA inputs for the providers.
        token = clusterlib_utils.new_tokens(
            clusterlib.get_rand_str(8).encode().hex(),
            cluster_obj=cluster,
            temp_template=f"{temp_template}_asset",
            token_mint_addr=issuer,
            issuer_addr=issuer,
            amount=2 * compensation,
        )[0].token
        setup_output = cluster.g_transaction.send_tx(
            src_address=issuer.address,
            tx_name=f"{temp_template}_setup",
            txouts=[
                # Joining these records gives the user's tokens their required ADA.
                clusterlib.TxOut(address=user.address, amount=user_lovelace),
                clusterlib.TxOut(address=user.address, amount=2 * compensation, coin=token),
                clusterlib.TxOut(address=provider.address, amount=provider_lovelace),
                clusterlib.TxOut(address=poor_provider.address, amount=output_lovelace),
            ],
            tx_files=clusterlib.TxFiles(signing_key_files=[issuer.skey_file]),
        )
        setup_utxos = cluster.g_query.get_utxo(tx_raw_output=setup_output)
        user_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=user.address)
        provider_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=provider.address)
        poor_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=poor_provider.address)

        user_utxos = cluster.g_query.get_utxo(address=user.address)
        asset_refs = {
            (u.utxo_hash, u.utxo_ix) for u in user_utxos if u.coin != clusterlib.DEFAULT_COIN
        }
        assert user_inputs and all((u.utxo_hash, u.utxo_ix) in asset_refs for u in user_utxos), (
            "The user has a separate ADA-only input"
        )

        # The signed outputs set the complete terms, including the provider's tokens.
        child_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_child.body",
            txins=user_inputs,
            txouts=[
                clusterlib.TxOut(address=recipient.address, amount=payment_amount),
                clusterlib.TxOut(address=user.address, amount=output_lovelace),
                clusterlib.TxOut(address=user.address, amount=compensation, coin=token),
                clusterlib.TxOut(address=provider.address, amount=output_lovelace),
                clusterlib.TxOut(address=provider.address, amount=compensation, coin=token),
            ],
        )
        child_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=child_output.out_file,
            tx_name=temp_template,
            signing_key_files=[user.skey_file],
        )
        published_bytes = child_signed.read_bytes()
        child_cbor = json.loads(published_bytes)["cborHex"]
        child = cbor2.loads(bytes.fromhex(child_cbor))
        assert not {2, 13, 16, 17}.intersection(child[0]), (
            "The user's child contains fee or collateral fields"
        )
        child_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed)
        child_refs = [f"{child_id}#{ix}" for ix in range(child_output.txouts_count)]
        all_inputs = [*user_inputs, *provider_inputs, *poor_inputs]
        inputs_before = cluster.g_query.get_utxo(utxo=all_inputs)

        # Declining an uneconomic offer is an off-chain choice, not a ledger action.
        declined_offer = pl.Path(f"{temp_template}_declined_offer.signed")
        declined_offer.write_bytes(published_bytes)
        assert cluster.g_query.get_utxo(utxo=all_inputs) == inputs_before
        assert not cluster.g_query.get_utxo(txin=child_refs)

        def _build_wrapper(
            name: str,
            src: clusterlib.AddressRecord,
            txins: list[clusterlib.UTXOData],
            change: int,
            extra_tokens: int = 0,
        ) -> tuple[clusterlib.TxRawOutput, pl.Path]:
            """Wrap the fixed child without balancing away the condition under test."""
            txouts = [clusterlib.TxOut(address=src.address, amount=change)]
            if extra_tokens:
                txouts.append(
                    clusterlib.TxOut(address=src.address, amount=extra_tokens, coin=token)
                )
            tx_output = cluster.g_transaction.build_raw_tx(
                src_address=src.address,
                tx_name=f"{temp_template}_{name}",
                txins=txins,
                txouts=txouts,
                tx_files=clusterlib.TxFiles(signing_key_files=[src.skey_file]),
                fee=fee,
                sub_transaction_files=[child_signed],
            )
            signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=[src.skey_file],
                tx_name=f"{temp_template}_{name}",
            )
            signed_cbor = json.loads(signed.read_text(encoding="utf-8"))["cborHex"]
            assert child_cbor in signed_cbor, "The provider changed the signed terms"
            assert cbor2.loads(bytes.fromhex(signed_cbor))[0][2] == fee
            return tx_output, signed

        # Start with a valid baseline, then isolate each funding or compensation error.
        change = provider_lovelace - lovelace_deficit - fee
        parent_output, parent_signed = _build_wrapper("valid", provider, provider_inputs, change)
        for name, src, txins, invalid_change, extra_tokens in (
            ("missing_fee_funding", provider, provider_inputs, change + fee, 0),
            ("insufficient_provider_ada", poor_provider, poor_inputs, output_lovelace, 0),
            ("excess_token_compensation", provider, provider_inputs, change, 1),
        ):
            with subtests.test(scenario=name):
                invalid_output, invalid_signed = _build_wrapper(
                    name, src, txins, invalid_change, extra_tokens
                )
                with pytest.raises(clusterlib.CLIError, match="ValueNotConservedUTxO"):
                    cluster.g_transaction.submit_tx_bare(tx_file=invalid_signed)

                assert cluster.g_query.get_utxo(utxo=all_inputs) == inputs_before
                assert not cluster.g_query.get_utxo(txin=child_refs)
                assert not cluster.g_query.get_utxo(tx_raw_output=invalid_output)

        # A willing provider can still settle the unchanged offer and pay the fee in ADA.
        cluster.g_transaction.submit_tx(tx_file=parent_signed, txins=provider_inputs)
        assert not cluster.g_query.get_utxo(utxo=[*user_inputs, *provider_inputs])
        assert cluster.g_query.get_utxo(utxo=poor_inputs) == poor_inputs

        child_utxos = cluster.g_query.get_utxo(txin=child_refs)
        assert sorted((u.address, u.amount, u.coin) for u in child_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in child_output.txouts
        ), "The recipient, user change, or provider compensation differs from the signed terms"

        parent_utxos = cluster.g_query.get_utxo(tx_raw_output=parent_output)
        assert [(u.address, u.amount, u.coin) for u in parent_utxos] == [
            (provider.address, change, clusterlib.DEFAULT_COIN)
        ], "Provider change does not account for the child's ADA deficit and parent fee"
        assert (
            user_lovelace + provider_lovelace
            == sum(
                u.amount for u in [*child_utxos, *parent_utxos] if u.coin == clusterlib.DEFAULT_COIN
            )
            + fee
        )
        assert child_signed.read_bytes() == published_bytes

        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=parent_output)
        # Child outputs are checked through the node; only the parent has a db-sync helper.
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=parent_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("winner", ("bob", "carol"))
    @pytest.mark.parametrize("counterparty_child", (False, True), ids=("parent", "children"))
    def test_open_swap(
        self,
        cluster: clusterlib.ClusterLib,
        swap_addrs: list[clusterlib.AddressRecord],
        winner: str,
        counterparty_child: bool,
    ):
        """Settle a published offer with either of two independent counterparties.

        * Prepare Alice's 12 ADA input and five tokens each for Bob and Carol.
        * Alice signs and publishes an unguarded offer returning 2 ADA and demanding
          five tokens. Neither counterparty needs another signature from Alice.
        * Bob and Carol independently assemble wrappers using the published file,
          supplying tokens in the parent or in a complementary signed child.
        * Confirm one wrapper and check exact signed outputs and batch conservation.
        * Submit the competing wrapper and check rejection due to Alice's spent
          input, unchanged counterparty inputs, and no additional outputs.
        """
        temp_template = common.get_test_id(cluster)
        alice, bob, carol, issuer = swap_addrs
        swap_amount = 10_000_000
        output_lovelace = 2_000_000
        counterparty_lovelace = 2_000_000
        token_amount = 5
        fee = 500_000

        # Mint a fresh asset and prepare distinct pre-batch inputs for all participants.
        token = clusterlib_utils.new_tokens(
            clusterlib.get_rand_str(8).encode().hex(),
            cluster_obj=cluster,
            temp_template=f"{temp_template}_asset",
            token_mint_addr=issuer,
            issuer_addr=issuer,
            amount=2 * token_amount,
        )[0].token

        # Join each counterparty's ADA and token records into one multi-asset UTxO.
        setup_output = cluster.g_transaction.send_tx(
            src_address=issuer.address,
            tx_name=f"{temp_template}_setup",
            txouts=[
                clusterlib.TxOut(address=alice.address, amount=swap_amount + output_lovelace),
                *[
                    txout
                    for addr in (bob, carol)
                    for txout in (
                        clusterlib.TxOut(address=addr.address, amount=counterparty_lovelace),
                        clusterlib.TxOut(address=addr.address, amount=token_amount, coin=token),
                    )
                ],
            ],
            tx_files=clusterlib.TxFiles(signing_key_files=[issuer.skey_file]),
        )
        setup_utxos = cluster.g_query.get_utxo(tx_raw_output=setup_output)
        alice_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=alice.address)

        # Publish Alice's fixed terms once, without any counterparty key or guard.
        # Her ADA and token records likewise form a single multi-asset output.
        offer_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_offer.body",
            txins=alice_inputs,
            txouts=[
                clusterlib.TxOut(address=alice.address, amount=output_lovelace),
                clusterlib.TxOut(address=alice.address, amount=token_amount, coin=token),
            ],
        )
        offer_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=offer_output.out_file,
            tx_name=f"{temp_template}_offer",
            signing_key_files=[alice.skey_file],
        )
        published_offer = pl.Path(f"{temp_template}_published_offer.signed")
        published_bytes = offer_signed.read_bytes()
        published_offer.write_bytes(published_bytes)
        offer_cbor = json.loads(published_bytes)["cborHex"]
        offer_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=published_offer)

        # Each assembler sees only the published offer, not Alice's signing key.
        def _assemble(
            counterparty: clusterlib.AddressRecord,
            name: str,
        ) -> tuple[clusterlib.TxRawOutput, pl.Path, clusterlib.SubTxRawOutput | None]:
            """Supply the missing tokens and claim the offered ADA independently."""
            name_template = f"{temp_template}_{name}"
            token_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=counterparty.address)
            child_output = None
            child_files = [published_offer]

            if counterparty_child:
                # The second child supplies tokens and receives Alice's 10 ADA.
                child_output = cluster.g_sub_transaction.build_raw_tx_bare(
                    out_file=f"{name_template}_child.body",
                    txins=token_inputs,
                    txouts=[
                        clusterlib.TxOut(
                            address=counterparty.address,
                            amount=counterparty_lovelace + swap_amount,
                        )
                    ],
                )
                child_files.append(
                    cluster.g_sub_transaction.sign_tx(
                        sub_tx_file=child_output.out_file,
                        tx_name=f"{name_template}_child",
                        signing_key_files=[counterparty.skey_file],
                    )
                )
                # Cached addresses may hold assets from previous swaps. Use only
                # ADA-only UTxOs for the separate parent fee, preserving those assets.
                address_utxos = cluster.g_query.get_utxo(address=counterparty.address)
                asset_refs = {
                    (u.utxo_hash, u.utxo_ix)
                    for u in address_utxos
                    if u.coin != clusterlib.DEFAULT_COIN
                }
                parent_inputs = [
                    u for u in address_utxos if (u.utxo_hash, u.utxo_ix) not in asset_refs
                ]
                assert parent_inputs, "No ADA-only inputs available for the parent fee"
                parent_amount = clusterlib.calculate_utxos_balance(parent_inputs) - fee
            else:
                # The parent itself supplies tokens and claims the ADA, less its fee.
                parent_inputs = token_inputs
                parent_amount = counterparty_lovelace + swap_amount - fee

            # File-only assembly avoids balancing or modifying the published offer.
            parent_output = cluster.g_transaction.build_raw_tx(
                src_address=counterparty.address,
                tx_name=name_template,
                txins=parent_inputs,
                txouts=[clusterlib.TxOut(address=counterparty.address, amount=parent_amount)],
                tx_files=clusterlib.TxFiles(signing_key_files=[counterparty.skey_file]),
                fee=fee,
                sub_transaction_files=child_files,
            )
            parent_signed = cluster.g_transaction.sign_tx(
                tx_body_file=parent_output.out_file,
                signing_key_files=[counterparty.skey_file],
                tx_name=name_template,
            )
            assert offer_cbor in json.loads(parent_signed.read_text(encoding="utf-8"))["cborHex"], (
                "The wrapper changed Alice's published offer"
            )
            return parent_output, parent_signed, child_output

        wrappers = {"bob": _assemble(bob, "bob"), "carol": _assemble(carol, "carol")}
        winning_output, winning_signed, winning_child = wrappers[winner]
        losing_output, losing_signed, losing_child = wrappers["carol" if winner == "bob" else "bob"]

        # The offer and counterparty contribution balance only as a complete swap.
        winning_components: list[clusterlib.SubTxRawOutput | clusterlib.TxRawOutput] = [
            offer_output,
            winning_output,
        ]
        if winning_child:
            winning_components.append(winning_child)
        for coin in (clusterlib.DEFAULT_COIN, token):
            input_amount = clusterlib.calculate_utxos_balance(
                [u for component in winning_components for u in component.txins], coin=coin
            )
            output_amount = sum(
                t.amount
                for component in winning_components
                for t in component.txouts
                if t.coin == coin
            )
            assert input_amount == output_amount + (fee if coin == clusterlib.DEFAULT_COIN else 0)

        losing_inputs = [*losing_output.txins, *(losing_child.txins if losing_child else [])]
        losing_before = cluster.g_query.get_utxo(utxo=losing_inputs)
        assert losing_before, "The competing wrapper has no unspent counterparty inputs"

        # Either assembler can settle first without asking Alice to sign its wrapper.
        cluster.g_transaction.submit_tx(tx_file=winning_signed, txins=winning_output.txins)
        assert not cluster.g_query.get_utxo(
            utxo=[u for component in winning_components for u in component.txins]
        ), "Some inputs of the successful swap remain unspent"

        def _check_outputs(
            txid: str, component: clusterlib.TxRawOutput | clusterlib.SubTxRawOutput
        ) -> None:
            """Check every output under its originating transaction ID."""
            utxos = cluster.g_query.get_utxo(
                txin=[f"{txid}#{ix}" for ix in range(component.txouts_count)]
            )
            assert sorted((u.address, u.amount, u.coin) for u in utxos) == sorted(
                (t.address, t.amount, t.coin) for t in component.txouts
            ), "Confirmed swap outputs differ from the signed terms"

        _check_outputs(offer_id, offer_output)
        _check_outputs(
            cluster.g_transaction.get_txid(tx_body_file=winning_output.out_file), winning_output
        )
        if winning_child:
            _check_outputs(
                cluster.g_sub_transaction.get_txid(sub_tx_file=winning_child.out_file),
                winning_child,
            )

        # A stale wrapper must fail atomically, leaving its counterparty's inputs untouched.
        with pytest.raises(clusterlib.CLIError, match="BadInputsUTxO"):
            cluster.g_transaction.submit_tx_bare(tx_file=losing_signed)

        assert cluster.g_query.get_utxo(utxo=losing_inputs) == losing_before
        assert not cluster.g_query.get_utxo(tx_raw_output=losing_output)
        if losing_child:
            losing_child_id = cluster.g_sub_transaction.get_txid(sub_tx_file=losing_child.out_file)
            assert not cluster.g_query.get_utxo(
                txin=[f"{losing_child_id}#{ix}" for ix in range(losing_child.txouts_count)]
            )
        _check_outputs(offer_id, offer_output)
        assert published_offer.read_bytes() == published_bytes

        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=winning_output)
        # Only the parent has an established representation in the db-sync helpers.
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=winning_output)

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
        fee = 500_000

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
            fee=500_000,
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
