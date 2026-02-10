import logging
import pathlib as pl

from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common

LOGGER = logging.getLogger(__name__)

# Approx. fee for Tx size
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
    reference_script: pl.Path | None = None,
    datum_file: pl.Path | None = None,
) -> tuple[
    list[clusterlib.UTXOData],
    list[clusterlib.UTXOData],
    clusterlib.UTXOData | None,
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

    # For reference script
    reference_amount = 0
    txouts_reference = []
    if reference_script:
        reference_amount = 20_000_000
        txouts_reference = [
            clusterlib.TxOut(
                address=issuer_addr.address,
                amount=reference_amount,
                reference_script_file=reference_script,
                datum_hash_file=datum_file or "",
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
        tx_name=f"{temp_template}_fund_issuer",
        txouts=txouts,
        tx_files=tx_files,
        # TODO: workaround for https://github.com/IntersectMBO/cardano-node/issues/1892
        witness_count_add=2,
        # Don't join 'change' and 'collateral' txouts, we need separate UTxOs
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


def check_missing_builtin(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    payment_addr: clusterlib.AddressRecord,
    issuer_addr: clusterlib.AddressRecord,
):
    """Check builtins added to PlutusV2 from PlutusV3."""
    lovelace_amount = 2_000_000
    token_amount = 5
    fee_txsize = 600_000

    plutus_v_record = plutus_common.BYTE_STRING_ROUNDTRIP_V2_REC

    minting_cost = plutus_common.compute_cost(
        execution_cost=plutus_v_record.execution_cost,
        protocol_params=cluster_obj.g_query.get_protocol_params(),
    )

    # Step 1: fund the token issuer

    mint_utxos, collateral_utxos, *__ = _fund_issuer(
        cluster_obj=cluster_obj,
        temp_template=temp_template,
        payment_addr=payment_addr,
        issuer_addr=issuer_addr,
        minting_cost=minting_cost,
        amount=lovelace_amount,
        fee_txsize=fee_txsize,
    )

    issuer_fund_balance = cluster_obj.g_query.get_address_balance(issuer_addr.address)

    # Step 2: mint the "qacoin"

    policyid = cluster_obj.g_transaction.get_policyid(plutus_v_record.script_file)
    asset_name_a = f"qacoina{clusterlib.get_rand_str(4)}".encode().hex()
    token_a = f"{policyid}.{asset_name_a}"
    mint_txouts = [
        clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token_a),
    ]

    plutus_mint_data = [
        clusterlib.Mint(
            txouts=mint_txouts,
            script_file=plutus_v_record.script_file,
            collaterals=collateral_utxos,
            execution_units=(
                plutus_v_record.execution_cost.per_time,
                plutus_v_record.execution_cost.per_space,
            ),
            redeemer_value="3735928559",
        )
    ]

    tx_files_step2 = clusterlib.TxFiles(
        signing_key_files=[issuer_addr.skey_file],
    )
    txouts_step2 = [
        clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
        *mint_txouts,
    ]
    tx_raw_output_step2 = cluster_obj.g_transaction.build_raw_tx_bare(
        out_file=f"{temp_template}_mint_tx.body",
        txins=mint_utxos,
        txouts=txouts_step2,
        mint=plutus_mint_data,
        tx_files=tx_files_step2,
        fee=minting_cost.fee + fee_txsize,
    )
    tx_signed_step2 = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_raw_output_step2.out_file,
        signing_key_files=tx_files_step2.signing_key_files,
        tx_name=f"{temp_template}_mint",
    )

    err = ""
    try:
        cluster_obj.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)
    except clusterlib.CLIError as excp:
        err = str(excp)

    def _check_txout() -> None:
        assert (
            cluster_obj.g_query.get_address_balance(issuer_addr.address)
            == issuer_fund_balance - tx_raw_output_step2.fee
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_raw_output_step2)

        token_utxo_a = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token_a
        )
        assert token_utxo_a and token_utxo_a[0].amount == token_amount, (
            "The 'token a' was not minted"
        )

        common.check_missing_utxos(cluster_obj=cluster_obj, utxos=out_utxos)

    prot_params = cluster_obj.g_query.get_protocol_params()
    prot_ver = prot_params["protocolVersion"]["major"]
    pv2_cost_model = prot_params["costModels"]["PlutusV2"]
    cost_model_len = len(pv2_cost_model)

    if prot_ver < 10:
        assert err, "Transaction succeeded but expected to fail"
        with common.allow_unstable_error_messages():
            assert "MalformedScriptWitnesses" in err, err
    elif cost_model_len < 185 or pv2_cost_model[-1] == 9223372036854775807:
        assert err, "Transaction succeeded but expected to fail"
        with common.allow_unstable_error_messages():
            assert "overspending the budget" in err, err
    elif not err:
        _check_txout()
    else:
        _msg = f"Unexpected error: {err}"
        raise AssertionError(_msg)
