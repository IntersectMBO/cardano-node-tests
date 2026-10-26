"""Spending conflicts, original UTxO references, and child output availability."""

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
