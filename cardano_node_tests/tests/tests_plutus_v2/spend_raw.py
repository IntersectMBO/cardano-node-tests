import json
import logging

from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

# Approx. fee for Tx size
FEE_REDEEM_TXSIZE = 400_000

PLUTUS_OP_ALWAYS_SUCCEEDS = plutus_common.PlutusOp(
    script_file=plutus_common.ALWAYS_SUCCEEDS["v2"].script_file,
    datum_file=plutus_common.DATUM_42,
    redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
    execution_cost=plutus_common.ALWAYS_SUCCEEDS["v2"].execution_cost,
)

PLUTUS_OP_GUESSING_GAME_UNTYPED = plutus_common.PlutusOp(
    script_file=plutus_common.GUESSING_GAME_UNTYPED["v2"].script_file,
    datum_file=plutus_common.DATUM_42,
    redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
    execution_cost=plutus_common.GUESSING_GAME_UNTYPED["v2"].execution_cost,
)

PLUTUS_OP_ALWAYS_FAILS = plutus_common.PlutusOp(
    script_file=plutus_common.ALWAYS_FAILS["v2"].script_file,
    datum_file=plutus_common.DATUM_42,
    redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
    execution_cost=plutus_common.ALWAYS_FAILS["v2"].execution_cost,
)


def _fund_script(
    temp_template: str,
    cluster: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    plutus_op: plutus_common.PlutusOp,
    amount: int,
    redeem_cost: plutus_common.ScriptCost,
    use_reference_script: bool = False,
    use_inline_datum: bool = False,
    collateral_amount: int | None = None,
    tokens_collateral: list[clusterlib_utils.Token]
    | None = None,  # tokens must already be in `payment_addr`
) -> tuple[
    list[clusterlib.UTXOData],
    list[clusterlib.UTXOData],
    clusterlib.UTXOData | None,
    clusterlib.TxRawOutput,
]:
    """Fund a Plutus script and create the locked UTxO, collateral UTxO and reference script."""
    script_address = cluster.g_address.gen_payment_addr(
        addr_name=temp_template, payment_script_file=plutus_op.script_file
    )

    # Create a Tx output with a datum hash at the script address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )

    txouts = [
        clusterlib.TxOut(
            address=script_address,
            amount=amount + redeem_cost.fee + FEE_REDEEM_TXSIZE,
            inline_datum_file=(
                plutus_op.datum_file if plutus_op.datum_file and use_inline_datum else ""
            ),
            inline_datum_value=(
                plutus_op.datum_value if plutus_op.datum_value and use_inline_datum else ""
            ),
            inline_datum_cbor_file=(
                plutus_op.datum_cbor_file if plutus_op.datum_cbor_file and use_inline_datum else ""
            ),
            datum_hash_file=(
                plutus_op.datum_file if plutus_op.datum_file and not use_inline_datum else ""
            ),
            datum_hash_value=(
                plutus_op.datum_value if plutus_op.datum_value and not use_inline_datum else ""
            ),
            datum_hash_cbor_file=(
                plutus_op.datum_cbor_file
                if plutus_op.datum_cbor_file and not use_inline_datum
                else ""
            ),
        ),
        # For collateral
        clusterlib.TxOut(
            address=dst_addr.address, amount=collateral_amount or redeem_cost.collateral
        ),
    ]

    # For reference script
    if use_reference_script:
        txouts.append(
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount,
                reference_script_file=plutus_op.script_file,
            )
        )

    txouts.extend(
        clusterlib.TxOut(
            address=dst_addr.address,
            amount=token.amount,
            coin=token.coin,
        )
        for token in tokens_collateral or []
    )

    tx_raw_output = cluster.g_transaction.send_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_fund_script",
        txouts=txouts,
        tx_files=tx_files,
        # TODO: workaround for https://github.com/IntersectMBO/cardano-node/issues/1892
        witness_count_add=2,
    )

    txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

    script_utxos = cluster.g_query.get_utxo(txin=f"{txid}#0")
    assert script_utxos, "No script UTxO"

    collateral_utxos = cluster.g_query.get_utxo(txin=f"{txid}#1")
    assert collateral_utxos, "No collateral UTxO"

    reference_utxo = None
    if use_reference_script:
        reference_utxos = cluster.g_query.get_utxo(txin=f"{txid}#2")
        assert reference_utxos, "No reference script UTxO"
        reference_utxo = reference_utxos[0]

    if VERSIONS.transaction_era >= VERSIONS.BABBAGE:
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        # Check if inline datum is returned by 'query utxo'
        if use_inline_datum:
            expected_datum = None
            if plutus_op.datum_file:
                with open(plutus_op.datum_file, encoding="utf-8") as json_datum:
                    expected_datum = json.load(json_datum)
            elif plutus_op.datum_value:
                expected_datum = plutus_op.datum_value

            assert expected_datum is None or script_utxos[0].inline_datum == expected_datum, (
                "The inline datum returned by 'query utxo' is different than the expected"
            )

    # Check "transaction view"
    tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    return script_utxos, collateral_utxos, reference_utxo, tx_raw_output


def _build_reference_txin(
    temp_template: str,
    cluster: clusterlib.ClusterLib,
    amount: int,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord | None = None,
) -> list[clusterlib.UTXOData]:
    """Create a basic txin to use as readonly reference input.

    Uses `cardano-cli transaction build-raw` command for building the transaction.
    """
    temp_template = f"{temp_template}_readonly_input"

    dst_addr = dst_addr or cluster.g_address.gen_payment_addr_and_keys(name=temp_template)

    txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

    tx_raw_output = cluster.g_transaction.send_tx(
        src_address=payment_addr.address,
        tx_name=temp_template,
        txouts=txouts,
        tx_files=tx_files,
    )

    txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

    reference_txin = cluster.g_query.get_utxo(txin=f"{txid}#0")
    assert reference_txin, "UTxO not created"

    return reference_txin
