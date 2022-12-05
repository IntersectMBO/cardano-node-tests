import logging
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple

from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import clusterlib_utils

LOGGER = logging.getLogger(__name__)


def _fund_issuer(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    payment_addr: clusterlib.AddressRecord,
    issuer_addr: clusterlib.AddressRecord,
    minting_cost: plutus_common.ScriptCost,
    amount: int,
    reference_script: Optional[Path] = None,
    inline_datum: Optional[Path] = None,
) -> Tuple[
    List[clusterlib.UTXOData],
    List[clusterlib.UTXOData],
    Optional[clusterlib.UTXOData],
    clusterlib.TxRawOutput,
]:
    """Fund the token issuer."""
    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )
    txouts = [
        clusterlib.TxOut(
            address=issuer_addr.address,
            amount=amount,
        ),
        # for collateral
        clusterlib.TxOut(address=issuer_addr.address, amount=minting_cost.collateral),
    ]

    reference_amount = 0
    if reference_script:
        reference_amount = 20_000_000
        # for reference UTxO
        txouts.append(
            clusterlib.TxOut(
                address=issuer_addr.address,
                amount=reference_amount,
                reference_script_file=reference_script,
                inline_datum_file=inline_datum if inline_datum else "",
            )
        )

    tx_output = cluster_obj.g_transaction.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        tx_files=tx_files,
        txouts=txouts,
        fee_buffer=2_000_000,
        # don't join 'change' and 'collateral' txouts, we need separate UTxOs
        join_txouts=False,
    )
    tx_signed = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step1",
    )
    cluster_obj.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(utxos=out_utxos, txouts=tx_output.txouts)

    issuer_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=issuer_addr.address)
    assert (
        clusterlib.calculate_utxos_balance(utxos=issuer_utxos)
        == amount + minting_cost.collateral + reference_amount
    ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

    mint_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)
    collateral_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 1)

    reference_utxo = None
    if reference_script:
        reference_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 2)
        assert reference_utxos, "No reference script UTxO"
        reference_utxo = reference_utxos[0]

    return mint_utxos, collateral_utxos, reference_utxo, tx_output
