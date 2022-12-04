import logging
from typing import List
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
    collateral_utxo_num: int = 1,
) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData], clusterlib.TxRawOutput]:
    """Fund the token issuer."""
    single_collateral_amount = minting_cost.collateral // collateral_utxo_num
    collateral_amounts = [single_collateral_amount for __ in range(collateral_utxo_num - 1)]
    collateral_subtotal = sum(collateral_amounts)
    collateral_amounts.append(minting_cost.collateral - collateral_subtotal)

    issuer_init_balance = cluster_obj.g_query.get_address_balance(issuer_addr.address)

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )
    txouts = [
        clusterlib.TxOut(
            address=issuer_addr.address,
            amount=amount,
        ),
        *[clusterlib.TxOut(address=issuer_addr.address, amount=a) for a in collateral_amounts],
    ]
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

    issuer_balance = cluster_obj.g_query.get_address_balance(issuer_addr.address)
    assert (
        issuer_balance == issuer_init_balance + amount + minting_cost.collateral
    ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(utxos=out_utxos, txouts=tx_output.txouts)

    mint_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)

    txid = out_utxos[0].utxo_hash
    collateral_utxos = [
        clusterlib.UTXOData(utxo_hash=txid, utxo_ix=idx, amount=a, address=issuer_addr.address)
        for idx, a in enumerate(collateral_amounts, start=utxo_ix_offset + 1)
    ]

    return mint_utxos, collateral_utxos, tx_output
