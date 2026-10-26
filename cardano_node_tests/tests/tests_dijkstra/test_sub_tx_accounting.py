"""Complete-batch value conservation and atomic state transitions."""

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
    """Cache two participants and a sponsor/issuer; fund only the sponsor."""
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The CLI does not support sub-transactions")
    return addrs_common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=3,
        fund_idx=[2],
        amount=12_000_000,
        min_amount=10_000_000,
        caching_key=helpers.get_current_line_str(),
    )


class TestSubTransactionConservation:
    """Enforce ADA and native-asset conservation over the complete batch."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_global_value_conservation(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        subtests: pytest_subtests.SubTests,
    ):
        """Balance complementary children collectively, rejecting one-unit mismatches.

        Move ADA and a fresh native asset between individually unbalanced,
        independently signed children. Re-sign each one-lovelace or one-token
        mismatch to isolate conservation from authorization. Check unchanged
        owned state on rejection, then confirm the balanced batch and outputs.
        """
        temp_template = common.get_test_id(cluster)
        first, second, sponsor = payment_addrs
        fee_lovelace = 500_000

        # Mint only the five tokens needed by the offers and isolate pre-batch inputs.
        token = clusterlib_utils.new_tokens(
            clusterlib.get_rand_str(8).encode().hex(),
            cluster_obj=cluster,
            temp_template=f"{temp_template}_asset",
            token_mint_addr=sponsor,
            issuer_addr=sponsor,
            amount=5,
        )[0].token
        setup = cluster.g_transaction.send_tx(
            src_address=sponsor.address,
            tx_name=f"{temp_template}_setup",
            txouts=[
                clusterlib.TxOut(address=first.address, amount=3_000_000),
                clusterlib.TxOut(address=first.address, amount=5, coin=token),
                clusterlib.TxOut(address=second.address, amount=3_000_000),
            ],
            tx_files=clusterlib.TxFiles(signing_key_files=[sponsor.skey_file]),
        )
        setup_utxos = cluster.g_query.get_utxo(tx_raw_output=setup)
        first_inputs = clusterlib.filter_utxos(setup_utxos, address=first.address)
        second_inputs = clusterlib.filter_utxos(setup_utxos, address=second.address)
        sponsor_inputs = clusterlib.filter_utxos(setup_utxos, address=sponsor.address)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        before = cluster.g_query.get_utxo(address=[a.address for a in payment_addrs])
        first_child = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_first",
            txins=first_inputs,
            txouts=[clusterlib.TxOut(address=first.address, amount=2_000_000)],
            signing_keys=[first.skey_file],
        )

        # File-only parent assembly cannot repair a deliberately unbalanced child.
        for name, delta_lovelace, delta_token in (
            ("extra_lovelace", 1, 0),
            ("missing_lovelace", -1, 0),
            ("extra_token", 0, 1),
            ("missing_token", 0, -1),
            ("balanced", 0, 0),
        ):
            second_child = sub_tx_utils.build_child(
                cluster=cluster,
                name=f"{temp_template}_{name}_second",
                txins=second_inputs,
                txouts=[
                    clusterlib.TxOut(address=second.address, amount=4_000_000 + delta_lovelace),
                    clusterlib.TxOut(address=second.address, amount=5 + delta_token, coin=token),
                ],
                signing_keys=[second.skey_file],
            )
            output, signed = sub_tx_utils.build_parent(
                cluster=cluster,
                name=f"{temp_template}_{name}",
                sponsor=sponsor,
                txins=sponsor_inputs,
                txouts=[
                    clusterlib.TxOut(
                        address=sponsor.address, amount=sponsor_lovelace - fee_lovelace
                    )
                ],
                children=[first_child, second_child],
                fee_lovelace=fee_lovelace,
            )
            if name != "balanced":
                with (
                    subtests.test(mismatch=name),
                    pytest.raises(clusterlib.CLIError, match="ValueNotConservedUTxO"),
                ):
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)

                assert (
                    cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
                )
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
                assert not cluster.g_query.get_utxo(
                    txin=[f"{first_child.txid}#0", f"{second_child.txid}#0"]
                )
                continue

            # Each child is unbalanced alone, but their aggregate ADA/assets balance exactly.
            cluster.g_transaction.submit_tx(tx_file=signed, txins=sponsor_inputs)
            assert not cluster.g_query.get_utxo(
                utxo=[*first_inputs, *second_inputs, *sponsor_inputs]
            )
            for child in (first_child, second_child):
                sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
            assert [
                (u.address, u.amount) for u in cluster.g_query.get_utxo(tx_raw_output=output)
            ] == [(sponsor.address, sponsor_lovelace - fee_lovelace)]
            node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)
