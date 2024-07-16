import logging
import pathlib as pl
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import plutus_common

LOGGER = logging.getLogger(__name__)

# approx. fee for Tx size
FEE_MINT_TXSIZE = 400_000


def _fund_issuer(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    payment_addr: clusterlib.AddressRecord,
    issuer_addr: clusterlib.AddressRecord,
    minting_cost: plutus_common.ScriptCost,
    amount: int,
    fee_txsize: int = FEE_MINT_TXSIZE,
    collateral_utxo_num: int = 1,
    reference_script: tp.Optional[pl.Path] = None,
    datum_file: tp.Optional[pl.Path] = None,
) -> tp.Tuple[
    tp.List[clusterlib.UTXOData],
    tp.List[clusterlib.UTXOData],
    tp.Optional[clusterlib.UTXOData],
    clusterlib.TxRawOutput,
]:
    """Fund the token issuer."""
    single_collateral_amount = minting_cost.collateral // collateral_utxo_num
    collateral_amounts = [single_collateral_amount for __ in range(collateral_utxo_num - 1)]
    collateral_subtotal = sum(collateral_amounts)
    collateral_amounts.append(minting_cost.collateral - collateral_subtotal)

    issuer_init_balance = cluster_obj.g_query.get_address_balance(issuer_addr.address)

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )

    # for reference script
    reference_amount = 0
    txouts_reference = []
    if reference_script:
        reference_amount = 20_000_000
        txouts_reference = [
            clusterlib.TxOut(
                address=issuer_addr.address,
                amount=reference_amount,
                reference_script_file=reference_script,
                datum_hash_file=datum_file if datum_file else "",
            )
        ]

    txouts_collateral = [
        clusterlib.TxOut(address=issuer_addr.address, amount=a) for a in collateral_amounts
    ]

    txouts = [
        clusterlib.TxOut(
            address=issuer_addr.address,
            amount=amount + minting_cost.fee + fee_txsize,
        ),
        *txouts_reference,
        *txouts_collateral,
    ]

    tx_raw_output = cluster_obj.g_transaction.send_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        txouts=txouts,
        tx_files=tx_files,
        # TODO: workaround for https://github.com/IntersectMBO/cardano-node/issues/1892
        witness_count_add=2,
        # don't join 'change' and 'collateral' txouts, we need separate UTxOs
        join_txouts=False,
    )

    issuer_balance = cluster_obj.g_query.get_address_balance(issuer_addr.address)
    assert (
        issuer_balance
        == issuer_init_balance
        + amount
        + minting_cost.fee
        + fee_txsize
        + minting_cost.collateral
        + reference_amount
    ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
    mint_utxos = cluster_obj.g_query.get_utxo(txin=f"{txid}#0")

    reference_utxo = None
    if reference_script:
        reference_utxos = cluster_obj.g_query.get_utxo(txin=f"{txid}#1")
        assert reference_utxos, "No reference script UTxO"
        reference_utxo = reference_utxos[0]

    collateral_utxos = [
        clusterlib.UTXOData(utxo_hash=txid, utxo_ix=idx, amount=a, address=issuer_addr.address)
        for idx, a in enumerate(collateral_amounts, start=len(txouts) - len(txouts_collateral))
    ]

    return mint_utxos, collateral_utxos, reference_utxo, tx_raw_output
