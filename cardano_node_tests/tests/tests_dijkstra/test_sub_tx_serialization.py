"""Child identity, ordering, and supported canonical transaction workflows."""

import json

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
