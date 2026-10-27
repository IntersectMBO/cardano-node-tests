"""Validity, offer lifecycle, and post-confirmation use of nested payments."""

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
