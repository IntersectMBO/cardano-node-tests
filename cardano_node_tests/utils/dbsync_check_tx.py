"""Function for checking a transaction in db-sync."""

import dataclasses
import functools
import itertools
import json
import logging

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_types
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


def _sum_mint_txouts(txouts: clusterlib.OptionalTxOuts) -> list[clusterlib.TxOut]:
    """Calculate minting amount sum for records with the same token.

    Remove address information - minting tokens doesn't include address, only amount and asset ID,
    i.e. address information is not available in `ma_tx_mint` table.
    Remove also datum hash, which is not available as well.
    MA output is handled in Tx output checks.
    """
    mint_txouts: dict[str, clusterlib.TxOut] = {}

    for mt in txouts:
        if mt.coin in mint_txouts:
            mt_stored = mint_txouts[mt.coin]
            mint_txouts[mt.coin] = dataclasses.replace(
                mt_stored, address="", amount=mt_stored.amount + mt.amount, datum_hash=""
            )
        else:
            mint_txouts[mt.coin] = dataclasses.replace(mt, address="", datum_hash="")

    return list(mint_txouts.values())


def _get_scripts_hashes(
    cluster_obj: clusterlib.ClusterLib,
    records: clusterlib.OptionalScriptTxIn | clusterlib.OptionalMint,
) -> dict[str, clusterlib.OptionalScriptTxIn | clusterlib.OptionalMint]:
    """Create a hash table of Tx Plutus data indexed by script hash."""
    hashes_db: dict = {}

    for r in records:
        if not r.script_file:
            continue
        shash = cluster_obj.g_transaction.get_policyid(script_file=r.script_file)
        shash_rec = hashes_db.get(shash)
        if shash_rec is None:
            hashes_db[shash] = [r]
            continue
        shash_rec.append(r)

    return hashes_db


def _db_redeemer_hashes(
    records: list[dbsync_types.RedeemerRecord],
) -> dict[str, list[dbsync_types.RedeemerRecord]]:
    """Create a hash table of redeemers indexed by script hash."""
    hashes_db: dict = {}

    for r in records:
        shash = r.script_hash
        shash_rec = hashes_db.get(shash)
        if shash_rec is None:
            hashes_db[shash] = [r]
            continue
        shash_rec.append(r)

    return hashes_db


def _compare_redeemer_value(
    tx_rec: clusterlib.ScriptTxIn | clusterlib.Mint, db_redeemer: dict
) -> bool:
    """Compare the value of the tx redeemer with the value stored on dbsync."""
    if not (tx_rec.redeemer_file or tx_rec.redeemer_value):
        return True

    redeemer_value = None

    if tx_rec.redeemer_file:
        with open(tx_rec.redeemer_file, encoding="utf-8") as r:
            redeemer_value = json.loads(r.read())
    elif tx_rec.redeemer_value and db_redeemer.get("int"):
        redeemer_value = {"int": int(tx_rec.redeemer_value)}
    elif tx_rec.redeemer_value and db_redeemer.get("bytes"):
        # We should ignore the first and last 2 chars because they represent
        # the double quotes.
        tx_redeemer_bytes = tx_rec.redeemer_value.encode("utf-8").hex()[2:-2]
        redeemer_value = {"bytes": tx_redeemer_bytes}

    return bool(db_redeemer == redeemer_value) if redeemer_value else True


def _compare_redeemers(
    tx_data: dict[str, clusterlib.OptionalScriptTxIn | clusterlib.OptionalMint],
    db_data: dict[str, list[dbsync_types.RedeemerRecord]],
    purpose: str,
) -> None:
    """Compare redeemers data available in Tx data with data in db-sync."""
    for script_hash, tx_recs in tx_data.items():
        if not tx_recs:
            return

        # If redeemer is not present, it is not plutus script
        if not (
            tx_recs[0].redeemer_file or tx_recs[0].redeemer_value or tx_recs[0].redeemer_cbor_file
        ):
            return

        # When minting with one Plutus script and two (or more) redeemers, only the last redeemer
        # is used.
        if hasattr(tx_recs[0], "txouts"):  # Check if it is a minting record
            # We'll check only the last redeemer
            tx_recs = tx_recs[-1:]  # noqa: PLW2901

        db_redeemer_recs = db_data.get(script_hash)
        assert db_redeemer_recs, f"No redeemer info in db-sync for script hash `{script_hash}`"

        len_tx_recs, len_db_redeemer_recs = len(tx_recs), len(db_redeemer_recs)
        assert len_tx_recs == len_db_redeemer_recs, (
            f"Number of TX redeemers doesn't match ({len_tx_recs} != {db_redeemer_recs})"
        )

        for tx_rec in tx_recs:
            tx_unit_steps = tx_rec.execution_units[0] if tx_rec.execution_units else None
            tx_unit_mem = tx_rec.execution_units[1] if tx_rec.execution_units else None

            missing_tx_unit_steps = not (tx_unit_steps and tx_unit_mem)

            for db_redeemer in db_redeemer_recs:
                if db_redeemer.purpose != purpose:
                    continue
                if not _compare_redeemer_value(tx_rec=tx_rec, db_redeemer=db_redeemer.value):
                    continue
                if missing_tx_unit_steps or (
                    tx_unit_steps == db_redeemer.unit_steps and tx_unit_mem == db_redeemer.unit_mem
                ):
                    break
            else:
                msg = f"Couldn't find matching redeemer info in db-sync for\n{tx_rec}"
                raise AssertionError(msg)


def _sanitize_txout(
    cluster_obj: clusterlib.ClusterLib, txout: clusterlib.TxOut
) -> clusterlib.TxOut:
    """Transform txout so it can be compared to data from db-sync."""
    datum_hash = clusterlib_utils.datum_hash_from_txout(cluster_obj=cluster_obj, txout=txout)

    new_txout = dataclasses.replace(
        txout,
        datum_hash=datum_hash,
        datum_hash_file="",
        datum_hash_cbor_file="",
        datum_hash_value="",
        datum_embed_file="",
        datum_embed_cbor_file="",
        datum_embed_value="",
        inline_datum_file="",
        inline_datum_cbor_file="",
        inline_datum_value="",
        reference_script_file="",
    )
    return new_txout


def _txout_has_inline_datum(txout: clusterlib.TxOut) -> bool:
    return bool(txout.inline_datum_cbor_file or txout.inline_datum_file or txout.inline_datum_value)


def utxodata2txout(*, utxodata: dbsync_types.UTxORecord | clusterlib.UTXOData) -> clusterlib.TxOut:
    """Convert `UTxORecord` or `UTxOData` to `clusterlib.TxOut`."""
    return clusterlib.TxOut(
        address=utxodata.address,
        amount=utxodata.amount,
        coin=utxodata.coin,
        datum_hash=utxodata.datum_hash,
    )


def utxorecord2utxodata(utxorecord: dbsync_types.UTxORecord) -> clusterlib.UTXOData:
    """Convert `UTxORecord` to `clusterlib.UTXOData`."""
    return clusterlib.UTXOData(
        utxo_hash=utxorecord.utxo_hash,
        utxo_ix=utxorecord.utxo_ix,
        amount=utxorecord.amount,
        address=utxorecord.address,
        coin=utxorecord.coin,
        decoded_coin=utxorecord.decoded_coin,
        datum_hash=utxorecord.datum_hash,
        inline_datum_hash=utxorecord.inline_datum_hash,
        inline_datum=utxorecord.inline_datum,
        reference_script=utxorecord.reference_script,
    )


def check_tx_outs(
    *,
    cluster_obj: clusterlib.ClusterLib,
    tx_raw_output: clusterlib.TxRawOutput,
    response: dbsync_types.TxRecord,
) -> None:
    """Check that the Tx outputs match the data from db-sync."""
    tx_txouts = {_sanitize_txout(cluster_obj=cluster_obj, txout=r) for r in tx_raw_output.txouts}
    db_txouts = {utxodata2txout(utxodata=r) for r in response.txouts}

    len_db_txouts, len_out_txouts = len(response.txouts), len(tx_raw_output.txouts)

    # We don't have complete info about the transaction when `build` command
    # was used (change txout, fee in older node versions), so we'll skip some of the checks.
    if tx_raw_output.change_address:
        assert tx_txouts.issubset(db_txouts), f"TX outputs not subset: ({tx_txouts} vs {db_txouts})"
        assert len_db_txouts >= len_out_txouts, (
            f"Number of TX outputs doesn't match ({len_db_txouts} < {len_out_txouts})"
        )
    else:
        txouts_amount = clusterlib.calculate_utxos_balance(utxos=tx_raw_output.txouts)
        assert response.out_sum == txouts_amount, (
            f"Sum of TX amounts doesn't match ({response.out_sum} != {txouts_amount})"
        )

        assert len_db_txouts == len_out_txouts, (
            f"Number of TX outputs doesn't match ({len_db_txouts} != {len_out_txouts})"
        )

        assert tx_txouts == db_txouts, f"TX outputs don't match ({tx_txouts} != {db_txouts})"


def check_tx_ins(*, tx_raw_output: clusterlib.TxRawOutput, response: dbsync_types.TxRecord) -> None:
    """Check that the Tx inputs match the data from db-sync."""
    combined_txins: list[clusterlib.UTXOData] = [
        *tx_raw_output.txins,
        *[p.txins[0] for p in tx_raw_output.script_txins if p.txins],
    ]

    txin_utxos = {f"{r.utxo_hash}#{r.utxo_ix}" for r in combined_txins}

    db_utxos = {f"{r.utxo_hash}#{r.utxo_ix}" for r in response.txins}

    assert txin_utxos == db_utxos, (
        f"Not all TX inputs are present in the db ({txin_utxos} != {db_utxos})"
    )


def check_tx_fee(*, tx_raw_output: clusterlib.TxRawOutput, response: dbsync_types.TxRecord) -> None:
    """Check that the Tx fee matches the data from db-sync."""
    # Unknown fee is set to -1
    if tx_raw_output.fee == -1:
        return

    assert response.fee == tx_raw_output.fee, (
        f"TX fee doesn't match ({response.fee} != {tx_raw_output.fee})"
    )

    redeemer_fees = functools.reduce(lambda x, y: x + y.fee, response.redeemers, 0)
    assert tx_raw_output.fee > redeemer_fees, "Combined redeemer fees are >= than total TX fee"


def check_tx_validity(
    *, tx_raw_output: clusterlib.TxRawOutput, response: dbsync_types.TxRecord
) -> None:
    """Check that the Tx validity interval match the data from db-sync."""
    assert response.invalid_before == tx_raw_output.invalid_before, (
        "TX invalid_before doesn't match "
        f"({response.invalid_before} != {tx_raw_output.invalid_before})"
    )

    assert response.invalid_hereafter == tx_raw_output.invalid_hereafter, (
        "TX invalid_hereafter doesn't match "
        f"({response.invalid_hereafter} != {tx_raw_output.invalid_hereafter})"
    )


def check_tx_mint(
    *, tx_raw_output: clusterlib.TxRawOutput, response: dbsync_types.TxRecord
) -> None:
    """Check that the Tx minting matches the data from db-sync."""
    tx_mint_txouts = list(itertools.chain.from_iterable(m.txouts for m in tx_raw_output.mint))
    tx_mint_by_token = sorted(_sum_mint_txouts(tx_mint_txouts))

    len_db_mint, len_out_mint = len(response.mint), len(tx_mint_by_token)
    assert len_db_mint == len_out_mint, (
        f"Number of MA minting doesn't match ({len_db_mint} != {len_out_mint})"
    )

    db_mint_txouts = sorted(utxodata2txout(utxodata=r) for r in response.mint)
    assert tx_mint_by_token == db_mint_txouts, (
        f"MA minting outputs don't match ({tx_mint_by_token} != {db_mint_txouts})"
    )


def check_tx_withdrawals(
    *, tx_raw_output: clusterlib.TxRawOutput, response: dbsync_types.TxRecord
) -> None:
    """Check that the Tx withdrawals match the data from db-sync."""
    tx_withdrawals = sorted(
        [*tx_raw_output.withdrawals, *[s.txout for s in tx_raw_output.script_withdrawals]]
    )
    db_withdrawals = sorted(response.withdrawals)
    len_tx_withdrawals = len(tx_withdrawals)
    len_db_withdrawals = len(db_withdrawals)

    assert len_db_withdrawals == len_tx_withdrawals, (
        f"Number of TX withdrawals doesn't match ({len_db_withdrawals} != {len_tx_withdrawals})"
    )

    assert tx_withdrawals == db_withdrawals, (
        f"TX withdrawals don't match ({tx_withdrawals} != {db_withdrawals})"
    )


def check_tx_collaterals(
    *,
    cluster_obj: clusterlib.ClusterLib,
    tx_raw_output: clusterlib.TxRawOutput,
    response: dbsync_types.TxRecord,
) -> None:
    """Check that the Tx collaterals match the data from db-sync."""
    tx_collaterals_nested = [
        r.collaterals
        for r in (
            *tx_raw_output.script_txins,
            *tx_raw_output.mint,
            *tx_raw_output.complex_certs,
            *tx_raw_output.script_withdrawals,
        )
    ]
    tx_collaterals_flat = set(itertools.chain.from_iterable(tx_collaterals_nested))
    # TODO: support multi-assets in collateral inputs
    tx_collaterals = {r for r in tx_collaterals_flat if r.coin == clusterlib.DEFAULT_COIN}
    db_collaterals = {utxorecord2utxodata(r) for r in response.collaterals}

    assert tx_collaterals == db_collaterals, (
        f"TX collaterals don't match ({tx_collaterals} != {db_collaterals})"
    )

    # Test automatic return collateral only with `transaction build` command on node/dbsync versions
    # that support it.
    if (
        tx_collaterals
        and tx_raw_output.change_address
        and response.collateral_outputs
        and not (tx_raw_output.total_collateral_amount or tx_raw_output.return_collateral_txouts)
    ):
        protocol_params = cluster_obj.g_query.get_protocol_params()
        tx_collaterals_amount = clusterlib.calculate_utxos_balance(utxos=list(tx_collaterals))
        tx_collateral_output_amount = int(
            tx_collaterals_amount
            - tx_raw_output.fee * protocol_params["collateralPercentage"] / 100
        )
        db_collateral_output_amount = clusterlib.calculate_utxos_balance(
            utxos=list(response.collateral_outputs)
        )

        assert db_collateral_output_amount == tx_collateral_output_amount, (
            "TX collateral output amount doesn't match "
            f"({db_collateral_output_amount} != {tx_collateral_output_amount})"
        )


def check_tx_scripts(
    *,
    cluster_obj: clusterlib.ClusterLib,
    tx_raw_output: clusterlib.TxRawOutput,
    response: dbsync_types.TxRecord,
) -> None:
    """Check that the Tx script hashes match the data from db-sync."""
    # Check txouts reference scripts in db-sync
    tx_out_script_hashes = {
        cluster_obj.g_transaction.get_policyid(script_file=r.reference_script_file)
        for r in tx_raw_output.txouts
        if r.reference_script_file
    }

    db_out_script_hashes = {
        r.reference_script_hash for r in response.txouts if r.reference_script_hash
    }

    assert tx_out_script_hashes == db_out_script_hashes, (
        f"Reference scripts don't match ({tx_out_script_hashes} != {db_out_script_hashes})"
    )

    # Check scripts hashes in db-sync
    tx_in_script_hashes = _get_scripts_hashes(
        cluster_obj=cluster_obj, records=tx_raw_output.script_txins
    )
    tx_mint_script_hashes = _get_scripts_hashes(cluster_obj=cluster_obj, records=tx_raw_output.mint)

    # TODO: check also withdrawals and certificates scripts

    # A script is added to `script` table only the first time it is seen, so the record
    # can be empty for the current transaction.
    tx_script_hashes = {*tx_in_script_hashes, *tx_mint_script_hashes, *tx_out_script_hashes}
    if response.scripts and tx_script_hashes:
        db_script_hashes = {s.hash for s in response.scripts}

        assert db_script_hashes.issubset(tx_script_hashes), (
            f"Scripts hashes don't match: {db_script_hashes} is not subset of {tx_script_hashes}"
        )

        # On plutus scripts we should also check the serialised_size
        db_plutus_scripts = {r for r in response.scripts if r.type.startswith("plutus")}

        if db_plutus_scripts:
            assert all(r.serialised_size > 0 for r in db_plutus_scripts), (
                f"The `serialised_size` <= 0 for some of the Plutus scripts:\n{db_plutus_scripts}"
            )

    # Compare redeemers data
    db_redeemer_hashes = _db_redeemer_hashes(records=response.redeemers)
    _compare_redeemers(tx_data=tx_in_script_hashes, db_data=db_redeemer_hashes, purpose="spend")
    _compare_redeemers(tx_data=tx_mint_script_hashes, db_data=db_redeemer_hashes, purpose="mint")


def check_tx_datum(
    *,
    cluster_obj: clusterlib.ClusterLib,
    tx_raw_output: clusterlib.TxRawOutput,
    response: dbsync_types.TxRecord,
) -> None:
    """Check that the Tx datum match the data from db-sync."""
    # Compare datum hash and inline datum hash in db-sync
    wrong_db_datum_hashes = [
        tx_out
        for tx_out in response.txouts
        if tx_out.inline_datum_hash and tx_out.inline_datum_hash != tx_out.datum_hash
    ]

    assert not wrong_db_datum_hashes, (
        "Datum hash and inline datum hash returned by dbsync don't match for following records:\n"
        f"{wrong_db_datum_hashes}"
    )

    # Compare inline datums
    tx_txouts_inline_datums = {
        _sanitize_txout(cluster_obj=cluster_obj, txout=r)
        for r in tx_raw_output.txouts
        if _txout_has_inline_datum(r)
    }
    db_txouts_inline_datums = {
        utxodata2txout(utxodata=r) for r in response.txouts if r.inline_datum_hash
    }
    assert tx_txouts_inline_datums == db_txouts_inline_datums, (
        f"Inline datums don't match ({tx_txouts_inline_datums} != {db_txouts_inline_datums})"
    )


def check_tx_reference_inputs(
    *, tx_raw_output: clusterlib.TxRawOutput, response: dbsync_types.TxRecord
) -> None:
    """Check that the Tx reference inputs match the data from db-sync."""
    txins_utxos_reference_inputs = {
        f"{r.utxo_hash}#{r.utxo_ix}" for r in tx_raw_output.combined_reference_txins
    }
    db_utxos_reference_inputs = {
        f"{r.utxo_hash}#{r.utxo_ix}" for r in response.reference_inputs if r
    }
    assert txins_utxos_reference_inputs == db_utxos_reference_inputs, (
        "Reference inputs don't match "
        f"({txins_utxos_reference_inputs} != {db_utxos_reference_inputs})"
    )


def check_tx_reference_scripts(
    *,
    cluster_obj: clusterlib.ClusterLib,
    tx_raw_output: clusterlib.TxRawOutput,
    response: dbsync_types.TxRecord,
) -> None:
    """Check that reference scripts in Tx inputs match the data from db-sync."""
    reference_scripts = [
        r.reference_script
        for r in (*tx_raw_output.txins, *tx_raw_output.combined_reference_txins)
        if r.reference_script
    ]

    tx_hashes = {
        clusterlib_utils.get_script_data_policyid(
            cluster_obj=cluster_obj,
            script_name=helpers.get_timestamped_rand_str(),
            script_data=r["script"],
        )
        for r in reference_scripts
    }

    db_hashes = {
        r.reference_script_hash for r in response.reference_inputs if r.reference_script_hash
    }

    assert tx_hashes == db_hashes, f"Reference scripts don't match ({tx_hashes} != {db_hashes})"


def check_tx_required_signers(
    *, tx_raw_output: clusterlib.TxRawOutput, response: dbsync_types.TxRecord
) -> None:
    """Check that the Tx required signers match the data from db-sync."""
    if tx_raw_output.required_signers:
        assert len(tx_raw_output.required_signers) == len(response.extra_key_witness), (
            "Number of required signers doesn't match "
            f"({len(tx_raw_output.required_signers)} != {len(response.extra_key_witness)})"
        )

    if tx_raw_output.required_signer_hashes:
        assert tx_raw_output.required_signer_hashes == response.extra_key_witness, (
            "Required signer hashes don't match "
            f"({tx_raw_output.required_signer_hashes} != {response.extra_key_witness})"
        )


def check_tx(
    *,
    cluster_obj: clusterlib.ClusterLib,
    tx_raw_output: clusterlib.TxRawOutput,
    response: dbsync_types.TxRecord,
) -> None:
    """Check a transaction in db-sync."""
    check_tx_ins(tx_raw_output=tx_raw_output, response=response)
    check_tx_outs(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output, response=response)
    check_tx_fee(tx_raw_output=tx_raw_output, response=response)
    check_tx_validity(tx_raw_output=tx_raw_output, response=response)
    check_tx_mint(tx_raw_output=tx_raw_output, response=response)
    check_tx_withdrawals(tx_raw_output=tx_raw_output, response=response)
    check_tx_collaterals(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output, response=response)
    check_tx_scripts(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output, response=response)
    check_tx_datum(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output, response=response)
    check_tx_reference_inputs(tx_raw_output=tx_raw_output, response=response)
    check_tx_reference_scripts(
        cluster_obj=cluster_obj, tx_raw_output=tx_raw_output, response=response
    )
    check_tx_required_signers(tx_raw_output=tx_raw_output, response=response)
