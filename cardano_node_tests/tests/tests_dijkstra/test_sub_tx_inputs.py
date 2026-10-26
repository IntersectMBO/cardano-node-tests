"""Spending conflicts, original UTxO references, and supplemental datum scope."""

import dataclasses
import pathlib as pl

import allure
import pytest
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
    """Cache three funded independent owners and an unfunded recipient."""
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The CLI does not support sub-transactions")
    return addrs_common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=4,
        fund_idx=[0, 1, 2],
        amount=4_000_000,
        min_amount=3_000_000,
        caching_key=helpers.get_current_line_str(),
    )


class TestSubTransactionInputs:
    """Check that spending is exclusive and reference lookups use original state."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("conflict", ("siblings", "reversed_siblings", "parent"))
    def test_spending_conflict(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        conflict: str,
    ):
        """Reject duplicate spending within the batch, then settle disjoint children.

        Independently sign two children sharing a live input, or share an input
        between parent and child. Reverse sibling order and check no owned input
        is consumed or output created. A disjoint baseline settles afterward.
        These payment-only batches have no certificate or governance effects.
        """
        temp_template = common.get_test_id(cluster)
        first, second, sponsor, recipient = payment_addrs
        inputs = [cluster.g_query.get_utxo(address=a.address) for a in payment_addrs[:3]]
        balances_lovelace = [clusterlib.calculate_utxos_balance(ins) for ins in inputs]
        before = cluster.g_query.get_utxo(address=[a.address for a in payment_addrs])

        # The conflicting child has different output bytes to avoid duplicate-child decoding.
        children = []
        for ix, (owner, txins, destination) in enumerate(
            ((first, inputs[0], first), (first, inputs[0], recipient), (second, inputs[1], second))
        ):
            children.append(
                sub_tx_utils.build_child(
                    cluster=cluster,
                    name=f"{temp_template}_child_{ix}",
                    txins=txins,
                    txouts=[
                        clusterlib.TxOut(
                            address=destination.address,
                            amount=clusterlib.calculate_utxos_balance(txins),
                        )
                    ],
                    signing_keys=[owner.skey_file],
                )
            )

        # Keep the arithmetic balanced even for duplicate inputs; only exclusivity is invalid.
        conflicting = children[:2] if conflict != "parent" else [children[0]]
        if conflict == "reversed_siblings":
            conflicting.reverse()
        parent_inputs = inputs[2] if conflict != "parent" else inputs[0]
        parent_lovelace = balances_lovelace[2] if conflict != "parent" else balances_lovelace[0]
        parent_owner = sponsor if conflict != "parent" else first
        rejected_output, rejected_signed = sub_tx_utils.build_parent(
            cluster=cluster,
            name=f"{temp_template}_conflict",
            sponsor=parent_owner,
            txins=parent_inputs,
            txouts=[
                clusterlib.TxOut(address=parent_owner.address, amount=parent_lovelace - 500_000)
            ],
            children=conflicting,
        )
        with pytest.raises(clusterlib.CLIError, match="BadInputsUTxO"):
            cluster.g_transaction.submit_tx_bare(tx_file=rejected_signed)

        assert cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
        assert not cluster.g_query.get_utxo(tx_raw_output=rejected_output)
        assert not cluster.g_query.get_utxo(txin=[f"{c.txid}#0" for c in conflicting])

        # The same first child settles when every spending input belongs to one component.
        baseline = [children[0], children[2]]
        output, signed = sub_tx_utils.build_parent(
            cluster=cluster,
            name=f"{temp_template}_disjoint",
            sponsor=sponsor,
            txins=inputs[2],
            txouts=[
                clusterlib.TxOut(address=sponsor.address, amount=balances_lovelace[2] - 500_000)
            ],
            children=baseline,
        )
        cluster.g_transaction.submit_tx(tx_file=signed, txins=inputs[2])
        assert not cluster.g_query.get_utxo(utxo=[u for group in inputs for u in group])
        for child in baseline:
            sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
        assert [(u.address, u.amount) for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
            (sponsor.address, balances_lovelace[2] - 500_000)
        ]
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)


class TestSubTransactionSupplementalDatums:
    """Check non-legacy supplemental datum scope without executing Plutus."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize(
        "scope", ("parent_child_output", "parent_child_reference", "sibling_only", "unrelated")
    )
    def test_supplemental_datum_scope(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        scope: str,
    ):
        """Allow the parent's child-related datums but reject sibling-only or unrelated data.

        Generate a datum witness/integrity hash through the CLI, then remove its
        supplying output's hash before signing. Only another child output or
        reference input relates to the datum, or no output does. Confirm allowed
        parent supply; reject other scopes without effects and settle a control
        without the extraneous witness. No legacy script executes in these cases.
        """
        temp_template = common.get_test_id(cluster)
        first, second, sponsor, _ = payment_addrs
        reference_inputs = []

        # A reference datum must belong to an output that already exists before the batch.
        if scope == "parent_child_reference":
            setup = cluster.g_transaction.send_tx(
                src_address=first.address,
                tx_name=f"{temp_template}_reference",
                txins=cluster.g_query.get_utxo(address=first.address),
                txouts=[
                    clusterlib.TxOut(address=first.address, amount=1_500_000, datum_hash_value="42")
                ],
                tx_files=clusterlib.TxFiles(signing_key_files=[first.skey_file]),
            )
            reference_inputs = [
                u for u in cluster.g_query.get_utxo(tx_raw_output=setup) if u.datum_hash
            ]
            assert len(reference_inputs) == 1
        inputs = [cluster.g_query.get_utxo(address=a.address) for a in payment_addrs[:3]]
        inputs[0] = [u for u in inputs[0] if u not in reference_inputs]
        balances_lovelace = [clusterlib.calculate_utxos_balance(ins) for ins in inputs]
        before = cluster.g_query.get_utxo(address=[a.address for a in payment_addrs])

        target = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_target",
            txins=inputs[0],
            txouts=[
                clusterlib.TxOut(
                    address=first.address,
                    amount=balances_lovelace[0],
                    datum_hash_value="42"
                    if scope in ("parent_child_output", "sibling_only")
                    else "",
                )
            ],
            reference_inputs=reference_inputs,
            signing_keys=[first.skey_file],
        )
        supplier_txouts = [
            clusterlib.TxOut(
                address=second.address,
                amount=balances_lovelace[1],
                datum_embed_value="42" if scope == "sibling_only" else "",
            )
        ]
        supplier = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_supplier_seed",
            txins=inputs[1],
            txouts=supplier_txouts,
            signing_keys=[] if scope == "sibling_only" else [second.skey_file],
        )
        if scope == "sibling_only":
            # Keep the datum but remove its only local justification before authorizing the body.
            mutated = sub_tx_utils.remove_output_datum_hash(
                tx_file=supplier.output.out_file,
                out_file=pl.Path(f"{temp_template}_supplier.body"),
            )
            signed = cluster.g_sub_transaction.sign_tx(
                sub_tx_file=mutated,
                tx_name=f"{temp_template}_supplier",
                signing_key_files=[second.skey_file],
            )
            supplier = dataclasses.replace(
                supplier,
                signed_file=signed,
                txid=cluster.g_sub_transaction.get_txid(signed_sub_tx_file=signed),
                output=dataclasses.replace(
                    supplier.output,
                    out_file=mutated,
                    txouts=[clusterlib.TxOut(address=second.address, amount=balances_lovelace[1])],
                ),
            )

        # Removing only the parent's own hash preserves every signed child's exact bytes.
        output = cluster.g_transaction.build_raw_tx(
            src_address=sponsor.address,
            tx_name=f"{temp_template}_parent_seed",
            txins=inputs[2],
            txouts=[
                clusterlib.TxOut(
                    address=sponsor.address,
                    amount=balances_lovelace[2] - 500_000,
                    datum_embed_value="" if scope == "sibling_only" else "42",
                )
            ],
            fee=500_000,
            sub_transaction_files=[target.signed_file, supplier.signed_file],
        )
        seed_output = output
        parent_file = output.out_file
        if scope != "sibling_only":
            parent_file = sub_tx_utils.remove_output_datum_hash(
                tx_file=parent_file, out_file=pl.Path(f"{temp_template}_parent.body")
            )
            output = dataclasses.replace(
                output,
                out_file=parent_file,
                txouts=[
                    clusterlib.TxOut(address=sponsor.address, amount=balances_lovelace[2] - 500_000)
                ],
            )
        signed_parent = cluster.g_transaction.sign_tx(
            tx_body_file=parent_file,
            tx_name=f"{temp_template}_parent",
            signing_key_files=[sponsor.skey_file],
        )
        if scope in ("sibling_only", "unrelated"):
            with pytest.raises(clusterlib.CLIError, match="NotAllowedSupplementalDatums"):
                cluster.g_transaction.submit_tx_bare(tx_file=signed_parent)

            assert cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
            assert not cluster.g_query.get_utxo(tx_raw_output=output)
            assert not cluster.g_query.get_utxo(txin=[f"{target.txid}#0", f"{supplier.txid}#0"])

            # Removing the extraneous witness, not changing target scope, restores validity.
            supplier = sub_tx_utils.build_child(
                cluster=cluster,
                name=f"{temp_template}_control_supplier",
                txins=inputs[1],
                txouts=[clusterlib.TxOut(address=second.address, amount=balances_lovelace[1])],
                signing_keys=[second.skey_file],
            )
            output, signed_parent = sub_tx_utils.build_parent(
                cluster=cluster,
                name=f"{temp_template}_control",
                sponsor=sponsor,
                txins=inputs[2],
                txouts=[
                    clusterlib.TxOut(address=sponsor.address, amount=balances_lovelace[2] - 500_000)
                ],
                children=[target, supplier],
            )

        missing_expanded_scope = False
        try:
            cluster.g_transaction.submit_tx(tx_file=signed_parent, txins=inputs[2])
        except clusterlib.CLIError as exc:
            if scope not in (
                "parent_child_output",
                "parent_child_reference",
            ) or "NotAllowedSupplementalDatums" not in str(exc):
                raise

            assert cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
            assert not cluster.g_query.get_utxo(tx_raw_output=output)
            assert not cluster.g_query.get_utxo(txin=[f"{target.txid}#0", f"{supplier.txid}#0"])
            missing_expanded_scope = True

            # Prove the original CLI datum/integrity bytes work when related to the parent.
            output = seed_output
            signed_parent = cluster.g_transaction.sign_tx(
                tx_body_file=output.out_file,
                tx_name=f"{temp_template}_own_output_control",
                signing_key_files=[sponsor.skey_file],
            )
            cluster.g_transaction.submit_tx(tx_file=signed_parent, txins=inputs[2])
        for child in (target, supplier):
            sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
        assert not cluster.g_query.get_utxo(utxo=[u for group in inputs for u in group])
        if reference_inputs:
            assert cluster.g_query.get_utxo(utxo=reference_inputs) == reference_inputs
        assert [u.amount for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
            balances_lovelace[2] - 500_000
        ]
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)
        if missing_expanded_scope:
            pytest.xfail("Parent child-related datum allowance needs ledger fix 846a63f764")


class TestSubTransactionReferences:
    """Distinguish original UTxO references from unavailable new child outputs."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("use", ("spend", "reference"))
    @pytest.mark.parametrize("placement", ("sibling", "reversed_siblings", "parent"))
    def test_no_output_chaining(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use: str,
        placement: str,
    ):
        """Reject spending or referencing a new child output within the same batch.

        Predict an output before assembly and use it in a sibling in either
        order, or in the parent. Check original-input rejection without effects.
        Confirm the producer separately, then use that exact output reference
        in a later ordinary transaction as a spend or read-only reference.
        """
        temp_template = common.get_test_id(cluster)
        first, second, sponsor, recipient = payment_addrs
        inputs = [cluster.g_query.get_utxo(address=a.address) for a in payment_addrs[:3]]
        balances_lovelace = [clusterlib.calculate_utxos_balance(ins) for ins in inputs]
        before = cluster.g_query.get_utxo(address=[a.address for a in payment_addrs])

        # Predict the producing child's output without putting it on chain.
        producer = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_producer",
            txins=inputs[0],
            txouts=[clusterlib.TxOut(address=recipient.address, amount=balances_lovelace[0])],
            signing_keys=[first.skey_file],
        )
        predicted = clusterlib.UTXOData(
            utxo_hash=producer.txid,
            utxo_ix=0,
            amount=balances_lovelace[0],
            address=recipient.address,
        )
        is_spend = use == "spend"
        children = [producer]
        parent_inputs = list(inputs[2])
        parent_lovelace = balances_lovelace[2]
        parent_keys = [sponsor.skey_file]
        parent_references = []

        # Fund each consumer independently; the sole fault is the unavailable input.
        if placement == "parent":
            if is_spend:
                parent_inputs.append(predicted)
                parent_lovelace += predicted.amount
                parent_keys.append(recipient.skey_file)
            else:
                parent_references = [predicted]
        else:
            consumer = sub_tx_utils.build_child(
                cluster=cluster,
                name=f"{temp_template}_consumer",
                txins=[predicted] if is_spend else inputs[1],
                txouts=[
                    clusterlib.TxOut(
                        address=second.address,
                        amount=predicted.amount if is_spend else balances_lovelace[1],
                    )
                ],
                reference_inputs=[] if is_spend else [predicted],
                signing_keys=[recipient.skey_file] if is_spend else [second.skey_file],
            )
            children.append(consumer)
            if placement == "reversed_siblings":
                children.reverse()

        rejected_output, rejected_signed = sub_tx_utils.build_parent(
            cluster=cluster,
            name=f"{temp_template}_chained",
            sponsor=sponsor,
            txins=parent_inputs,
            txouts=[clusterlib.TxOut(address=sponsor.address, amount=parent_lovelace - 500_000)],
            children=children,
            reference_inputs=parent_references,
            signing_keys=parent_keys,
        )
        with pytest.raises(clusterlib.CLIError, match="BadInputsUTxO"):
            cluster.g_transaction.submit_tx_bare(tx_file=rejected_signed)

        assert cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
        assert not cluster.g_query.get_utxo(tx_raw_output=rejected_output)
        assert not cluster.g_query.get_utxo(txin=[f"{c.txid}#0" for c in children])

        # The producer alone settles with exactly the originally predicted child identity.
        output, signed = sub_tx_utils.build_parent(
            cluster=cluster,
            name=f"{temp_template}_produce_first",
            sponsor=sponsor,
            txins=inputs[2],
            txouts=[
                clusterlib.TxOut(address=sponsor.address, amount=balances_lovelace[2] - 500_000)
            ],
            children=[producer],
        )
        cluster.g_transaction.submit_tx(tx_file=signed, txins=inputs[2])
        sub_tx_utils.check_child_outputs(cluster=cluster, child=producer)
        created = cluster.g_query.get_utxo(txin=f"{producer.txid}#0")
        assert created == [predicted]
        assert not cluster.g_query.get_utxo(utxo=inputs[0])
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)

        # A later ordinary transaction can now spend or reference the identical output.
        control_owner = recipient if is_spend else second
        control_inputs = created if is_spend else inputs[1]
        control = cluster.g_transaction.send_tx(
            src_address=control_owner.address,
            tx_name=f"{temp_template}_later_{use}",
            txins=control_inputs,
            readonly_reference_txins=[] if is_spend else created,
            tx_files=clusterlib.TxFiles(signing_key_files=[control_owner.skey_file]),
        )
        assert not cluster.g_query.get_utxo(utxo=control_inputs)
        assert bool(cluster.g_query.get_utxo(utxo=created)) is not is_spend
        assert (
            clusterlib.calculate_utxos_balance(cluster.g_query.get_utxo(tx_raw_output=control))
            == clusterlib.calculate_utxos_balance(control_inputs) - control.fee
        )
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=control)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=control)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize(
        "placement", ("siblings", "reversed_siblings", "parent_reference", "child_reference")
    )
    def test_original_utxo_references(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        placement: str,
    ):
        """Reference an existing input even when another component spends it.

        Exercise both sibling orders and each parent/child direction. In the
        sibling cases the parent also references the same consumed input.
        Confirm exact child and parent outputs, exclusive spending, and any
        unused owner inputs. No intra-batch output is used as a reference.
        """
        temp_template = common.get_test_id(cluster)
        first, second, sponsor, recipient = payment_addrs
        inputs = [cluster.g_query.get_utxo(address=a.address) for a in payment_addrs[:3]]
        balances_lovelace = [clusterlib.calculate_utxos_balance(ins) for ins in inputs]
        child_references = inputs[2] if placement == "child_reference" else []

        # The spending child is authorized locally; references need no owner signature.
        spender = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_spender",
            txins=inputs[0],
            txouts=[clusterlib.TxOut(address=recipient.address, amount=balances_lovelace[0])],
            reference_inputs=child_references,
            signing_keys=[first.skey_file],
        )
        children = [spender]
        consumed = [*inputs[0], *inputs[2]]
        if placement in ("siblings", "reversed_siblings"):
            reader = sub_tx_utils.build_child(
                cluster=cluster,
                name=f"{temp_template}_reader",
                txins=inputs[1],
                txouts=[clusterlib.TxOut(address=second.address, amount=balances_lovelace[1])],
                reference_inputs=inputs[0],
                signing_keys=[second.skey_file],
            )
            children.append(reader)
            consumed.extend(inputs[1])
            if placement == "reversed_siblings":
                children.reverse()

        # A parent lookup must still find an input consumed by an earlier child.
        output, signed = sub_tx_utils.build_parent(
            cluster=cluster,
            name=f"{temp_template}_batch",
            sponsor=sponsor,
            txins=inputs[2],
            txouts=[
                clusterlib.TxOut(address=sponsor.address, amount=balances_lovelace[2] - 500_000)
            ],
            children=children,
            reference_inputs=[] if placement == "child_reference" else inputs[0],
        )
        cluster.g_transaction.submit_tx(tx_file=signed, txins=inputs[2])

        # References do not double-spend the original UTxO or create extra outputs.
        assert not cluster.g_query.get_utxo(utxo=consumed)
        for child in children:
            sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
        if len(children) == 1:
            assert cluster.g_query.get_utxo(utxo=inputs[1]) == inputs[1]
        assert [(u.address, u.amount) for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
            (sponsor.address, balances_lovelace[2] - 500_000)
        ]
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)
