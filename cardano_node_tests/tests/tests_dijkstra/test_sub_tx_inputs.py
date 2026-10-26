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
