"""Measured transaction-size and fee boundaries for complete nested batches."""

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
    """Cache two independent metadata-bearing child owners and a fee sponsor."""
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The CLI does not support sub-transactions")
    return addrs_common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=3,
        amount=3_000_000,
        min_amount=2_500_000,
        caching_key=helpers.get_current_line_str(),
    )


class TestSubTransactionLimits:
    """Measure actual serialized batches rather than sum guessed child sizes."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_complete_batch_size_limit(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Enforce measured whole-batch size and fee boundaries for individually fitting children.

        Search using actual signed parent/child CBOR sizes and current protocol
        parameters. Child metadata uses valid at-most-64-byte chunks. Reject the
        smallest oversized candidate without effects, then settle the adjacent
        valid candidate at its exact linear minimum fee. Reject one lovelace
        below that fee, checking every consumed input and originating output.
        """
        temp_template = common.get_test_id(cluster)
        sponsor = payment_addrs[2]
        inputs = [cluster.g_query.get_utxo(address=a.address) for a in payment_addrs]
        balances_lovelace = [clusterlib.calculate_utxos_balance(ins) for ins in inputs]
        pparams = cluster.g_query.get_protocol_params()
        max_size = pparams["maxTxSize"]
        before = cluster.g_query.get_utxo(address=[a.address for a in payment_addrs])

        def _child(padding: int, ix: int, name: str) -> sub_tx_utils.SignedChild:
            """Create legitimate local metadata with byte-granularity adjustable size."""
            chunks = [b"x" * min(64, padding - offset) for offset in range(0, padding, 64)]
            metadata = pl.Path(f"{name}.metadata.cbor")
            metadata.write_bytes(cbor2.dumps({ix: chunks}))
            owner = payment_addrs[ix]
            return sub_tx_utils.build_child(
                cluster=cluster,
                name=name,
                txins=inputs[ix],
                txouts=[clusterlib.TxOut(address=owner.address, amount=balances_lovelace[ix])],
                signing_keys=[owner.skey_file],
                tx_files=clusterlib.TxFiles(metadata_cbor_files=[metadata]),
            )

        def _size(signed: pl.Path) -> int:
            """Measure bytes actually submitted, including every child and key witness."""
            return len(bytes.fromhex(json.loads(signed.read_text(encoding="utf-8"))["cborHex"]))

        first_child = _child(max_size // 2, 0, f"{temp_template}_first")
        assert _size(first_child.signed_file) < max_size
        candidates: dict[
            int, tuple[clusterlib.TxRawOutput, pl.Path, sub_tx_utils.SignedChild, int]
        ] = {}

        def _candidate(
            padding: int,
        ) -> tuple[clusterlib.TxRawOutput, pl.Path, sub_tx_utils.SignedChild, int]:
            """Cache uniquely named signed candidates with an adequate measured-size fee."""
            if padding in candidates:
                return candidates[padding]
            name = f"{temp_template}_padding_{padding}"
            child = _child(padding, 1, f"{name}_second")
            assert _size(child.signed_file) < max_size
            output, signed = sub_tx_utils.build_parent(
                cluster=cluster,
                name=f"{name}_probe",
                sponsor=sponsor,
                txins=inputs[2],
                txouts=[
                    clusterlib.TxOut(address=sponsor.address, amount=balances_lovelace[2] - 500_000)
                ],
                children=[first_child, child],
            )
            size = _size(signed)
            # There are no scripts/reference fees here; both fee and change remain uint32.
            fee_lovelace = pparams["txFeeFixed"] + pparams["txFeePerByte"] * size + 1_000
            output, signed = sub_tx_utils.build_parent(
                cluster=cluster,
                name=f"{name}_funded",
                sponsor=sponsor,
                txins=inputs[2],
                txouts=[
                    clusterlib.TxOut(
                        address=sponsor.address, amount=balances_lovelace[2] - fee_lovelace
                    )
                ],
                fee_lovelace=fee_lovelace,
                children=[first_child, child],
            )
            assert _size(signed) == size, "Fee/change encoding changed the measured size"
            result = (output, signed, child, size)
            candidates[padding] = result
            return result

        # Search for the adjacent encodable sizes straddling the actual protocol boundary.
        low, high = 0, max_size // 2 + 2_048
        assert _candidate(low)[3] <= max_size < _candidate(high)[3]
        while high - low > 1:
            middle = (low + high) // 2
            if _candidate(middle)[3] <= max_size:
                low = middle
            else:
                high = middle
        accepted_output, accepted_signed, accepted_child, accepted_size = _candidate(low)
        rejected_output, rejected_signed, rejected_child, rejected_size = _candidate(high)
        assert accepted_size <= max_size < rejected_size
        # A CBOR chunk/list header can add bytes at an adjacent padding boundary.
        assert rejected_size - accepted_size <= 3

        with pytest.raises(clusterlib.CLIError, match="MaxTxSizeUTxO"):
            cluster.g_transaction.submit_tx_bare(tx_file=rejected_signed)

        assert cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
        assert not cluster.g_query.get_utxo(tx_raw_output=rejected_output)
        assert not cluster.g_query.get_utxo(
            txin=[f"{first_child.txid}#0", f"{rejected_child.txid}#0"]
        )

        # Both children and their witnesses/metadata contribute to the parent's minimum fee.
        minimum_fee_lovelace = pparams["txFeeFixed"] + pparams["txFeePerByte"] * accepted_size
        for name, fee_lovelace in (
            ("below_minimum_fee", minimum_fee_lovelace - 1),
            ("exact_minimum_fee", minimum_fee_lovelace),
        ):
            output, signed = sub_tx_utils.build_parent(
                cluster=cluster,
                name=f"{temp_template}_{name}",
                sponsor=sponsor,
                txins=inputs[2],
                txouts=[
                    clusterlib.TxOut(
                        address=sponsor.address, amount=balances_lovelace[2] - fee_lovelace
                    )
                ],
                children=[first_child, accepted_child],
                fee_lovelace=fee_lovelace,
            )
            assert _size(signed) == accepted_size
            if name == "below_minimum_fee":
                with pytest.raises(clusterlib.CLIError, match="FeeTooSmallUTxO"):
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)

                assert (
                    cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
                )
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
                assert not cluster.g_query.get_utxo(
                    txin=[f"{first_child.txid}#0", f"{accepted_child.txid}#0"]
                )
            else:
                accepted_output, accepted_signed = output, signed

        # Exact signed child bodies survive the valid near-limit parent and node decoding.
        cluster.g_transaction.submit_tx(tx_file=accepted_signed, txins=inputs[2])
        assert not cluster.g_query.get_utxo(utxo=[u for group in inputs for u in group])
        for child in (first_child, accepted_child):
            sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
        assert [u.amount for u in cluster.g_query.get_utxo(tx_raw_output=accepted_output)] == [
            balances_lovelace[2] - accepted_output.fee
        ]
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=accepted_output)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=accepted_output)
