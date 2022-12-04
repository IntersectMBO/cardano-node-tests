import logging
from typing import List
from typing import Optional
from typing import Tuple

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


# approx. fee for Tx size
FEE_REDEEM_TXSIZE = 400_000


def _fund_script(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    plutus_op: plutus_common.PlutusOp,
    amount: int,
    fee_txsize: int = FEE_REDEEM_TXSIZE,
    deposit_amount: int = 0,
    tokens: Optional[List[plutus_common.Token]] = None,  # tokens must already be in `payment_addr`
    tokens_collateral: Optional[
        List[plutus_common.Token]
    ] = None,  # tokens must already be in `payment_addr`
    collateral_fraction_offset: float = 1.0,
    embed_datum: bool = False,
) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData], clusterlib.TxRawOutput]:
    """Fund a Plutus script and create the locked UTxO and collateral UTxO."""
    # pylint: disable=too-many-locals,too-many-arguments
    assert plutus_op.execution_cost  # for mypy

    stokens = tokens or ()
    ctokens = tokens_collateral or ()

    script_address = cluster_obj.g_address.gen_payment_addr(
        addr_name=temp_template, payment_script_file=plutus_op.script_file
    )

    redeem_cost = plutus_common.compute_cost(
        execution_cost=plutus_op.execution_cost,
        protocol_params=cluster_obj.g_query.get_protocol_params(),
        collateral_fraction_offset=collateral_fraction_offset,
    )

    # create a Tx output with a datum hash at the script address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )

    script_txout = plutus_common.txout_factory(
        address=script_address,
        amount=amount + redeem_cost.fee + fee_txsize + deposit_amount,
        plutus_op=plutus_op,
        embed_datum=embed_datum,
    )

    txouts = [
        script_txout,
        # for collateral
        clusterlib.TxOut(address=dst_addr.address, amount=redeem_cost.collateral),
    ]

    for token in stokens:
        txouts.append(script_txout._replace(amount=token.amount, coin=token.coin))

    for token in ctokens:
        txouts.append(
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=token.amount,
                coin=token.coin,
            )
        )

    tx_raw_output = cluster_obj.g_transaction.send_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        txouts=txouts,
        tx_files=tx_files,
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
        witness_count_add=2,
    )

    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

    script_utxos = cluster_obj.g_query.get_utxo(txin=f"{txid}#0")
    assert script_utxos, "No script UTxO"

    assert (
        clusterlib.calculate_utxos_balance(utxos=script_utxos) == txouts[0].amount
    ), f"Incorrect balance for script address `{script_address}`"

    collateral_utxos = cluster_obj.g_query.get_utxo(txin=f"{txid}#1")
    assert collateral_utxos, "No collateral UTxO"

    assert (
        clusterlib.calculate_utxos_balance(utxos=collateral_utxos) == redeem_cost.collateral
    ), f"Incorrect balance for collateral address `{dst_addr.address}`"

    for token in stokens:
        assert (
            clusterlib.calculate_utxos_balance(utxos=script_utxos, coin=token.coin) == token.amount
        ), f"Incorrect token balance for script address `{script_address}`"

    for token in ctokens:
        assert (
            clusterlib.calculate_utxos_balance(utxos=collateral_utxos, coin=token.coin)
            == token.amount
        ), f"Incorrect token balance for address `{dst_addr.address}`"

    if VERSIONS.transaction_era >= VERSIONS.ALONZO:
        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    return script_utxos, collateral_utxos, tx_raw_output


def _spend_locked_txin(  # noqa: C901
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    dst_addr: clusterlib.AddressRecord,
    script_utxos: List[clusterlib.UTXOData],
    collateral_utxos: List[clusterlib.UTXOData],
    plutus_op: plutus_common.PlutusOp,
    amount: int,
    fee_txsize: int = FEE_REDEEM_TXSIZE,
    txins: clusterlib.OptionalUTXOData = (),
    tx_files: Optional[clusterlib.TxFiles] = None,
    invalid_hereafter: Optional[int] = None,
    invalid_before: Optional[int] = None,
    tokens: Optional[List[plutus_common.Token]] = None,
    expect_failure: bool = False,
    script_valid: bool = True,
    submit_tx: bool = True,
) -> Tuple[str, clusterlib.TxRawOutput]:
    """Spend the locked UTxO."""
    # pylint: disable=too-many-arguments,too-many-locals
    assert plutus_op.execution_cost

    tx_files = tx_files or clusterlib.TxFiles()
    spent_tokens = tokens or ()

    # change will be returned to address of the first script
    change_rec = script_utxos[0]

    redeem_cost = plutus_common.compute_cost(
        execution_cost=plutus_op.execution_cost,
        protocol_params=cluster_obj.g_query.get_protocol_params(),
    )

    script_utxos_lovelace = [u for u in script_utxos if u.coin == clusterlib.DEFAULT_COIN]
    script_lovelace_balance = clusterlib.calculate_utxos_balance(
        utxos=[*script_utxos_lovelace, *txins]
    )

    # spend the "locked" UTxO

    plutus_txins = [
        clusterlib.ScriptTxIn(
            txins=script_utxos,
            script_file=plutus_op.script_file,
            collaterals=collateral_utxos,
            execution_units=(plutus_op.execution_cost.per_time, plutus_op.execution_cost.per_space),
            datum_file=plutus_op.datum_file if plutus_op.datum_file else "",
            datum_cbor_file=plutus_op.datum_cbor_file if plutus_op.datum_cbor_file else "",
            datum_value=plutus_op.datum_value if plutus_op.datum_value else "",
            redeemer_file=plutus_op.redeemer_file if plutus_op.redeemer_file else "",
            redeemer_cbor_file=plutus_op.redeemer_cbor_file if plutus_op.redeemer_cbor_file else "",
            redeemer_value=plutus_op.redeemer_value if plutus_op.redeemer_value else "",
        )
    ]

    tx_files = tx_files._replace(
        signing_key_files=list({*tx_files.signing_key_files, dst_addr.skey_file}),
    )
    txouts = [
        clusterlib.TxOut(address=dst_addr.address, amount=amount),
    ]
    # append change
    if script_lovelace_balance > amount + redeem_cost.fee + fee_txsize:
        txouts.append(
            clusterlib.TxOut(
                address=change_rec.address,
                amount=script_lovelace_balance - amount - redeem_cost.fee - fee_txsize,
                datum_hash=change_rec.datum_hash,
            )
        )

    for token in spent_tokens:
        txouts.append(
            clusterlib.TxOut(address=dst_addr.address, amount=token.amount, coin=token.coin)
        )
        # append change
        script_token_balance = clusterlib.calculate_utxos_balance(
            utxos=script_utxos, coin=token.coin
        )
        if script_token_balance > token.amount:
            txouts.append(
                clusterlib.TxOut(
                    address=change_rec.address,
                    amount=script_token_balance - token.amount,
                    coin=token.coin,
                    datum_hash=change_rec.datum_hash,
                )
            )

    tx_raw_output = cluster_obj.g_transaction.build_raw_tx_bare(
        out_file=f"{temp_template}_step2_tx.body",
        txins=txins,
        txouts=txouts,
        tx_files=tx_files,
        fee=redeem_cost.fee + fee_txsize,
        script_txins=plutus_txins,
        invalid_hereafter=invalid_hereafter,
        invalid_before=invalid_before,
        script_valid=script_valid,
    )
    tx_signed = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step2",
    )

    if not submit_tx:
        return "", tx_raw_output

    dst_init_balance = cluster_obj.g_query.get_address_balance(dst_addr.address)

    if not script_valid:
        cluster_obj.g_transaction.submit_tx(tx_file=tx_signed, txins=collateral_utxos)

        assert (
            cluster_obj.g_query.get_address_balance(dst_addr.address)
            == dst_init_balance - collateral_utxos[0].amount
        ), f"Collateral was NOT spent from `{dst_addr.address}`"

        for u in script_utxos_lovelace:
            assert cluster_obj.g_query.get_utxo(
                utxo=u, coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were unexpectedly spent for `{u.address}`"

        return "", tx_raw_output

    if expect_failure:
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.g_transaction.submit_tx_bare(tx_file=tx_signed)
        err = str(excinfo.value)
        assert (
            cluster_obj.g_query.get_address_balance(dst_addr.address) == dst_init_balance
        ), f"Collateral was spent from `{dst_addr.address}`"

        for u in script_utxos_lovelace:
            assert cluster_obj.g_query.get_utxo(
                utxo=u, coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were unexpectedly spent for `{u.address}`"

        return err, tx_raw_output

    cluster_obj.g_transaction.submit_tx(
        tx_file=tx_signed, txins=[t.txins[0] for t in tx_raw_output.script_txins if t.txins]
    )

    assert (
        cluster_obj.g_query.get_address_balance(dst_addr.address) == dst_init_balance + amount
    ), f"Incorrect balance for destination address `{dst_addr.address}`"

    for u in script_utxos_lovelace:
        assert not cluster_obj.g_query.get_utxo(
            utxo=u, coins=[clusterlib.DEFAULT_COIN]
        ), f"Inputs were NOT spent for `{u.address}`"

    for token in spent_tokens:
        script_utxos_token = [u for u in script_utxos if u.coin == token.coin]
        for u in script_utxos_token:
            assert not cluster_obj.g_query.get_utxo(
                utxo=u, coins=[token.coin]
            ), f"Token inputs were NOT spent for `{u.address}`"

    # check tx view
    tx_view.check_tx_view(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    return "", tx_raw_output
