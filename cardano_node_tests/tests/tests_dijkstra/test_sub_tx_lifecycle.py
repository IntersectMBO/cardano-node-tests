"""Validity windows, competing wrappers, and replay of nested payments."""

import concurrent.futures
import json
import pathlib as pl
import threading

import allure
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests.tests_dijkstra import sub_tx_utils
from cardano_node_tests.utils import clusterlib_utils
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
    """Cache two users, fee sponsor and recipient with minimal reusable funding."""
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The CLI does not support sub-transactions")
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


class TestSubTransactionValidity:
    """Require every parent/child validity interval to hold at submission."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_validity_intersection(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        subtests: pytest_subtests.SubTests,
    ):
        """Reject an expired/future component or disjoint intervals, then settle their overlap.

        Independently sign expired and not-yet-valid children and parents around
        otherwise valid payments. Every rejection preserves owned inputs and
        creates no outputs. Confirm overlapping, unequal and open-ended bounds.
        This does not claim exact-slot boundary scheduling or guard range checks.
        """
        temp_template = common.get_test_id(cluster)
        first, second, sponsor, recipient = payment_addrs
        inputs = [cluster.g_query.get_utxo(address=a.address) for a in payment_addrs[:3]]
        balances_lovelace = [clusterlib.calculate_utxos_balance(ins) for ins in inputs]
        slot = cluster.g_query.get_slot_no()
        before = cluster.g_query.get_utxo(address=[a.address for a in payment_addrs])

        for scenario in (
            "child_expired",
            "child_future",
            "parent_expired",
            "parent_future",
            "disjoint",
            "overlap",
        ):
            name = f"{temp_template}_{scenario}"
            child_lower = max(0, slot - 50)
            child_upper = slot + 4_000
            parent_lower = max(0, slot - 100)
            parent_upper = slot + 5_000
            if scenario == "child_expired":
                child_upper = slot
            if scenario in ("child_future", "disjoint"):
                child_lower, child_upper = slot + 10_000, slot + 20_000
            if scenario == "parent_expired":
                parent_upper = slot
            if scenario == "parent_future":
                parent_lower, parent_upper = slot + 10_000, slot + 20_000
                child_upper = slot + 25_000
            if scenario == "child_future":
                parent_upper = slot + 25_000

            # All bodies are signed after setting their intended bounds, not mutated afterward.
            child = sub_tx_utils.build_child(
                cluster=cluster,
                name=f"{name}_first",
                txins=inputs[0],
                txouts=[clusterlib.TxOut(address=recipient.address, amount=balances_lovelace[0])],
                signing_keys=[first.skey_file],
                invalid_before=child_lower,
                invalid_hereafter=child_upper,
            )
            sibling = sub_tx_utils.build_child(
                cluster=cluster,
                name=f"{name}_second",
                txins=inputs[1],
                txouts=[clusterlib.TxOut(address=second.address, amount=balances_lovelace[1])],
                signing_keys=[second.skey_file],
                invalid_hereafter=slot
                + (30_000 if scenario in ("child_future", "parent_future") else 3_000),
            )
            output, signed = sub_tx_utils.build_parent(
                cluster=cluster,
                name=f"{name}_parent",
                sponsor=sponsor,
                txins=inputs[2],
                txouts=[
                    clusterlib.TxOut(address=sponsor.address, amount=balances_lovelace[2] - 500_000)
                ],
                children=[child, sibling],
                invalid_before=parent_lower,
                invalid_hereafter=parent_upper,
            )
            if scenario != "overlap":
                with (
                    subtests.test(interval=scenario),
                    pytest.raises(clusterlib.CLIError, match="OutsideValidityIntervalUTxO"),
                ):
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)

                assert (
                    cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
                )
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
                assert not cluster.g_query.get_utxo(txin=[f"{child.txid}#0", f"{sibling.txid}#0"])
                continue

            # Both bounded and open-ended intervals contain the actual confirmation slot.
            assert cluster.g_query.get_slot_no() < slot + 3_000, "The overlap expired during setup"
            cluster.g_transaction.submit_tx(tx_file=signed, txins=inputs[2])
            assert not cluster.g_query.get_utxo(utxo=[u for group in inputs for u in group])
            for confirmed in (child, sibling):
                sub_tx_utils.check_child_outputs(cluster=cluster, child=confirmed)
            assert [u.amount for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
                balances_lovelace[2] - 500_000
            ]
            node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)


@pytest.mark.usefixtures("require_sub_cli")
class TestSubTransactionReplay:
    """Settle competing wrappers once and reject spent-child replays."""

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


class TestSubTransactionOfferLifecycle:
    """Invalidate published offers without changing an aggregator's stored copy."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("invalidation", ("expiry", "cancellation"))
    def test_stale_offer(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        invalidation: str,
    ):
        """Reject a stored offer after expiry or a confirmed independent input spend.

        Publish one live signed payment and store identical copies for two
        assemblers. Build a funded wrapper before invalidation. Either wait
        until the signed upper bound or confirm an ordinary cancellation spend.
        Submit the stale wrapper and a freshly assembled wrapper from the
        other stored copy. Both must fail without charging the sponsor or
        creating child outputs. Expiry leaves the original input spendable;
        cancellation leaves only the ordinary cancellation outputs.
        """
        temp_template = common.get_test_id(cluster)
        user, other_assembler, sponsor, recipient = payment_addrs
        user_inputs = cluster.g_query.get_utxo(address=user.address)
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        user_lovelace = clusterlib.calculate_utxos_balance(user_inputs)

        # Leave ample time to publish and build while the offer is still live.
        upper_bound = cluster.g_query.get_slot_no() + 100
        child = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_offer",
            txins=user_inputs,
            txouts=[clusterlib.TxOut(address=recipient.address, amount=user_lovelace)],
            signing_keys=[user.skey_file],
            invalid_hereafter=upper_bound,
        )
        published_bytes = child.signed_file.read_bytes()
        copies = [pl.Path(f"{temp_template}_service_{ix}.signed") for ix in range(2)]
        for stored in copies:
            stored.write_bytes(published_bytes)

        # Each assembler knows the signed terms but never obtains the user's key.
        def _wrap(ix: int) -> tuple[clusterlib.TxRawOutput, pl.Path]:
            """Fund a wrapper using the assembler's independent fee inputs."""
            assembler = sponsor if ix == 0 else other_assembler
            txins = (
                sponsor_inputs
                if ix == 0
                else cluster.g_query.get_utxo(address=other_assembler.address)
            )
            stored_child = sub_tx_utils.SignedChild(
                output=child.output, signed_file=copies[ix], txid=child.txid
            )
            return sub_tx_utils.build_parent(
                cluster=cluster,
                name=f"{temp_template}_wrapper_{ix}",
                sponsor=assembler,
                txins=txins,
                txouts=[
                    clusterlib.TxOut(
                        address=assembler.address,
                        amount=clusterlib.calculate_utxos_balance(txins) - 500_000,
                    )
                ],
                children=[stored_child],
            )

        first_wrapper = _wrap(0)
        assert cluster.g_query.get_slot_no() < upper_bound, "Offer expired before publication"
        cancellation = None
        if invalidation == "expiry":
            cluster.wait_for_slot(slot=upper_bound + 1)
        else:
            # Cancellation is a confirmed input spend, not removal of service files.
            cancellation = cluster.g_transaction.send_tx(
                src_address=user.address,
                tx_name=f"{temp_template}_cancel",
                txins=user_inputs,
                txouts=[clusterlib.TxOut(address=user.address, amount=1_500_000)],
                tx_files=clusterlib.TxFiles(signing_key_files=[user.skey_file]),
            )
            assert not cluster.g_query.get_utxo(utxo=user_inputs)
            node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=cancellation)
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=cancellation)

        # Neither a previously built wrapper nor a new assembler can revive the offer.
        before = cluster.g_query.get_utxo(address=[a.address for a in payment_addrs])
        for output, signed in (first_wrapper, _wrap(1)):
            with pytest.raises(clusterlib.CLIError) as excinfo:
                cluster.g_transaction.submit_tx_bare(tx_file=signed)
            error = str(excinfo.value)
            if invalidation == "expiry":
                assert "OutsideValidityIntervalUTxO" in error, error
            else:
                assert helpers.is_inputs_spent_err(error), error
            assert cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
            assert not cluster.g_query.get_utxo(tx_raw_output=output)
            assert not cluster.g_query.get_utxo(txin=f"{child.txid}#0")

        assert cluster.g_query.get_utxo(utxo=sponsor_inputs) == sponsor_inputs
        assert all(stored.read_bytes() == published_bytes for stored in copies)
        if invalidation == "expiry":
            assert cluster.g_query.get_utxo(utxo=user_inputs) == user_inputs
        else:
            assert cancellation and cluster.g_query.get_utxo(tx_raw_output=cancellation)
