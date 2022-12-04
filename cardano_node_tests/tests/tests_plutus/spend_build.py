import logging
from typing import List
from typing import Optional
from typing import Tuple

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


def _build_fund_script(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    plutus_op: plutus_common.PlutusOp,
    tokens: Optional[List[plutus_common.Token]] = None,  # tokens must already be in `payment_addr`
    tokens_collateral: Optional[
        List[plutus_common.Token]
    ] = None,  # tokens must already be in `payment_addr`
    embed_datum: bool = False,
) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData], clusterlib.TxRawOutput]:
    """Fund a Plutus script and create the locked UTxO and collateral UTxO.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    assert plutus_op.execution_cost  # for mypy

    script_fund = 200_000_000

    stokens = tokens or ()
    ctokens = tokens_collateral or ()

    script_address = cluster_obj.g_address.gen_payment_addr(
        addr_name=temp_template, payment_script_file=plutus_op.script_file
    )

    redeem_cost = plutus_common.compute_cost(
        execution_cost=plutus_op.execution_cost,
        protocol_params=cluster_obj.g_query.get_protocol_params(),
    )

    # create a Tx output with a datum hash at the script address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )

    script_txout = plutus_common.txout_factory(
        address=script_address,
        amount=script_fund,
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

    tx_output = cluster_obj.g_transaction.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        tx_files=tx_files,
        txouts=txouts,
        fee_buffer=2_000_000,
    )
    tx_signed = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step1",
    )
    cluster_obj.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(utxos=out_utxos, txouts=tx_output.txouts)

    script_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)
    assert script_utxos, "No script UTxO"

    assert (
        clusterlib.calculate_utxos_balance(utxos=script_utxos) == script_fund
    ), f"Incorrect balance for script address `{script_address}`"

    collateral_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 1)
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
        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)

    return script_utxos, collateral_utxos, tx_output


def _build_spend_locked_txin(  # noqa: C901
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    script_utxos: List[clusterlib.UTXOData],
    collateral_utxos: List[clusterlib.UTXOData],
    plutus_op: plutus_common.PlutusOp,
    amount: int,
    deposit_amount: int = 0,
    txins: clusterlib.OptionalUTXOData = (),
    tx_files: Optional[clusterlib.TxFiles] = None,
    invalid_hereafter: Optional[int] = None,
    invalid_before: Optional[int] = None,
    tokens: Optional[List[plutus_common.Token]] = None,
    expect_failure: bool = False,
    script_valid: bool = True,
    submit_tx: bool = True,
) -> Tuple[str, Optional[clusterlib.TxRawOutput], list]:
    """Spend the locked UTxO.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    # pylint: disable=too-many-arguments,too-many-locals
    tx_files = tx_files or clusterlib.TxFiles()
    spent_tokens = tokens or ()

    # Change that was calculated manually will be returned to address of the first script.
    # The remaining change that is automatically handled by the `build` command will be returned
    # to `payment_addr`, because it would be inaccessible on script address without proper
    # datum hash (datum hash is not provided for change that is handled by `build` command).
    script_change_rec = script_utxos[0]

    # spend the "locked" UTxO

    plutus_txins = [
        clusterlib.ScriptTxIn(
            txins=script_utxos,
            script_file=plutus_op.script_file,
            collaterals=collateral_utxos,
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

    lovelace_change_needed = False
    for token in spent_tokens:
        txouts.append(
            clusterlib.TxOut(address=dst_addr.address, amount=token.amount, coin=token.coin)
        )
        # append change
        script_token_balance = clusterlib.calculate_utxos_balance(
            utxos=script_utxos, coin=token.coin
        )
        if script_token_balance > token.amount:
            lovelace_change_needed = True
            txouts.append(
                clusterlib.TxOut(
                    address=script_change_rec.address,
                    amount=script_token_balance - token.amount,
                    coin=token.coin,
                    datum_hash=script_change_rec.datum_hash,
                )
            )
    # add minimum (+ some) required Lovelace to change Tx output
    if lovelace_change_needed:
        txouts.append(
            clusterlib.TxOut(
                address=script_change_rec.address,
                amount=4_000_000,
                coin=clusterlib.DEFAULT_COIN,
                datum_hash=script_change_rec.datum_hash,
            )
        )

    if expect_failure:
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files,
                txins=txins,
                txouts=txouts,
                script_txins=plutus_txins,
                change_address=payment_addr.address,
            )
        return str(excinfo.value), None, []

    tx_output = cluster_obj.g_transaction.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step2",
        tx_files=tx_files,
        txins=txins,
        txouts=txouts,
        script_txins=plutus_txins,
        change_address=payment_addr.address,
        invalid_hereafter=invalid_hereafter,
        invalid_before=invalid_before,
        deposit=deposit_amount,
        script_valid=script_valid,
    )
    tx_signed = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step2",
    )

    if not submit_tx:
        return "", tx_output, []

    dst_init_balance = cluster_obj.g_query.get_address_balance(dst_addr.address)

    script_utxos_lovelace = [u for u in script_utxos if u.coin == clusterlib.DEFAULT_COIN]

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

        return "", tx_output, []

    # calculate cost of Plutus script
    plutus_costs = cluster_obj.g_transaction.calculate_plutus_script_cost(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step2",
        tx_files=tx_files,
        txouts=txouts,
        script_txins=plutus_txins,
        change_address=payment_addr.address,
        invalid_hereafter=invalid_hereafter,
        invalid_before=invalid_before,
        deposit=deposit_amount,
        script_valid=script_valid,
    )

    cluster_obj.g_transaction.submit_tx(
        tx_file=tx_signed, txins=[t.txins[0] for t in tx_output.script_txins if t.txins]
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
    tx_view.check_tx_view(cluster_obj=cluster_obj, tx_raw_output=tx_output)

    tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)
    # compare cost of Plutus script with data from db-sync
    if tx_db_record:
        dbsync_utils.check_plutus_costs(
            redeemer_records=tx_db_record.redeemers, cost_records=plutus_costs
        )

    return "", tx_output, plutus_costs
