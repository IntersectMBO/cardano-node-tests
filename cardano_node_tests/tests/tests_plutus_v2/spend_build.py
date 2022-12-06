import json
import logging
from typing import List
from typing import Optional
from typing import Tuple

from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


PLUTUS_OP_ALWAYS_SUCCEEDS = plutus_common.PlutusOp(
    script_file=plutus_common.ALWAYS_SUCCEEDS["v2"].script_file,
    datum_file=plutus_common.DATUM_42,
    redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
    execution_cost=plutus_common.ALWAYS_SUCCEEDS["v2"].execution_cost,
)

PLUTUS_OP_GUESSING_GAME = plutus_common.PlutusOp(
    script_file=plutus_common.GUESSING_GAME["v2"].script_file,
    datum_file=plutus_common.DATUM_42_TYPED,
    redeemer_cbor_file=plutus_common.REDEEMER_42_TYPED_CBOR,
    execution_cost=plutus_common.GUESSING_GAME["v2"].execution_cost,
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


def _build_fund_script(
    temp_template: str,
    cluster: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    plutus_op: plutus_common.PlutusOp,
    use_reference_script: bool = False,
    use_inline_datum: bool = True,
    collateral_amount: Optional[int] = None,
    tokens_collateral: Optional[
        List[plutus_common.Token]
    ] = None,  # tokens must already be in `payment_addr`
) -> Tuple[
    List[clusterlib.UTXOData],
    List[clusterlib.UTXOData],
    Optional[clusterlib.UTXOData],
    clusterlib.TxRawOutput,
]:
    """Fund a Plutus script and create the locked UTxO and collateral UTxO and reference script.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    # pylint: disable=too-many-arguments

    # for mypy
    assert plutus_op.execution_cost

    script_fund = 200_000_000

    script_address = cluster.g_address.gen_payment_addr(
        addr_name=temp_template, payment_script_file=plutus_op.script_file
    )

    redeem_cost = plutus_common.compute_cost(
        execution_cost=plutus_op.execution_cost,
        protocol_params=cluster.g_query.get_protocol_params(),
    )

    # create a Tx output with a datum hash at the script address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )

    txouts = [
        clusterlib.TxOut(
            address=script_address,
            amount=script_fund,
            inline_datum_file=(
                plutus_op.datum_file if plutus_op.datum_file and use_inline_datum else ""
            ),
            inline_datum_value=(
                plutus_op.datum_value if plutus_op.datum_value and use_inline_datum else ""
            ),
            datum_hash_file=(
                plutus_op.datum_file if plutus_op.datum_file and not use_inline_datum else ""
            ),
            datum_hash_value=(
                plutus_op.datum_value if plutus_op.datum_value and not use_inline_datum else ""
            ),
        ),
        # for collateral
        clusterlib.TxOut(
            address=dst_addr.address, amount=collateral_amount or redeem_cost.collateral
        ),
    ]

    if use_reference_script:
        txouts.append(
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=10_000_000,
                reference_script_file=plutus_op.script_file,
            )
        )

    for token in tokens_collateral or []:
        txouts.append(
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=token.amount,
                coin=token.coin,
            )
        )

    tx_output = cluster.g_transaction.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        tx_files=tx_files,
        txouts=txouts,
        fee_buffer=2_000_000,
        join_txouts=bool(tokens_collateral),
    )
    tx_signed = cluster.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step1",
    )
    cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
    utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(utxos=out_utxos, txouts=tx_output.txouts)

    script_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)
    assert script_utxos, "No script UTxO"

    collateral_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 1)
    assert collateral_utxos, "No collateral UTxO"

    reference_utxo = None
    if use_reference_script:
        reference_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 2)
        assert reference_utxos, "No reference script UTxO"
        reference_utxo = reference_utxos[0]

    if VERSIONS.transaction_era >= VERSIONS.BABBAGE:
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

        # check if inline datum is returned by 'query utxo'
        if use_inline_datum:
            if plutus_op.datum_file:
                with open(plutus_op.datum_file, encoding="utf-8") as json_datum:
                    expected_datum = json.load(json_datum)
            else:
                expected_datum = plutus_op.datum_value

            assert (
                script_utxos[0].inline_datum == expected_datum
            ), "The inline datum returned by 'query utxo' is different than the expected"

    # check "transaction view"
    tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output)

    return script_utxos, collateral_utxos, reference_utxo, tx_output


def _build_reference_txin(
    temp_template: str,
    cluster: clusterlib.ClusterLib,
    amount: int,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: Optional[clusterlib.AddressRecord] = None,
) -> List[clusterlib.UTXOData]:
    """Create a basic txin to use as readonly reference input.

    Uses `cardano-cli transaction build` command for building the transaction.
    """
    dst_addr = dst_addr or cluster.g_address.gen_payment_addr_and_keys(
        name=f"{temp_template}_readonly_input"
    )

    txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

    tx_output = cluster.g_transaction.build_tx(
        src_address=payment_addr.address,
        tx_name=temp_template,
        tx_files=tx_files,
        txouts=txouts,
        fee_buffer=1_000_000,
    )
    tx_signed = cluster.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=temp_template,
    )
    cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
    utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(utxos=out_utxos, txouts=tx_output.txouts)

    reference_txin = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)
    assert reference_txin, "UTxO not created"

    return reference_txin
