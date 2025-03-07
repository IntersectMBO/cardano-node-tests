"""Checks for `transaction view` CLI command."""

import itertools
import json
import logging
import pathlib as pl
import re
import typing as tp

import yaml
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

CERTIFICATES_INFORMATION = {
    "genesis key delegation": {"VRF key hash", "delegate key hash", "genesis key hash"},
    "MIR": {
        "pot",
        "target stake addresses",
        "send to treasury",
        "send to reserves",
        "MIR amount",  # node 8.3.0+
    },
    "stake address deregistration": {
        "stake credential key hash",
        "stake credential script hash",
        "key hash",  # node 8.3.0+
        "script hash",  # node 8.3.0+
        "keyHash",  # node 8.4.0+
        "scriptHash",  # node 8.4.0+
    },
    "stake address registration": {
        "stake credential key hash",
        "stake credential script hash",
        "key hash",  # node 8.3.0+
        "script hash",  # node 8.3.0+
        "keyHash",  # node 8.4.0+
        "scriptHash",  # node 8.4.0+
    },
    "stake address delegation": {
        "pool",
        "stake credential key hash",
        "stake credential script hash",
        "credential",  # node 8.3.0+
    },
    "stake pool retirement": {"epoch", "pool"},
    "stake pool registration": {
        "VRF key hash",
        "vrf",  # node 8.3.0+
        "cost",
        "margin",
        "metadata",
        "owners (stake key hashes)",
        "owners",  # node 8.3.0+
        "pledge",
        "pool",
        "relays",
        "reward account",
        "rewardAccount",  # node 8.3.0+
        "publicKey",  # node 8.3.0+
    },
}


def load_raw(tx_view: str) -> dict:
    """Load tx view output as YAML."""
    tx_loaded: dict = yaml.safe_load(tx_view)
    return tx_loaded


def _load_assets(assets: dict[str, dict[str, int]]) -> list[tuple[int, str]]:
    loaded_data = []

    for policy_key_rec, policy_rec in assets.items():
        if policy_key_rec == clusterlib.DEFAULT_COIN:
            continue
        policy_key = (
            policy_key_rec.replace("policy ", "") if "policy " in policy_key_rec else policy_key_rec
        )
        for asset_name_rec, amount in policy_rec.items():
            asset_name = asset_name_rec
            if "asset " in asset_name:
                asset_name = re.search(r"asset ([0-9a-f]*)", asset_name).group(1)  # type: ignore
            elif asset_name == "default asset":
                asset_name = ""
            token = f"{policy_key}.{asset_name}" if asset_name else policy_key
            loaded_data.append((amount, token))

    return loaded_data


def _load_coins_data(coins_data: dict | str) -> list[tuple[int, str]]:
    # `coins_data` for Mary+ Tx era has Lovelace amount and policies info,
    # for older Tx eras it's just Lovelace amount
    try:
        amount_lovelace = coins_data.get(clusterlib.DEFAULT_COIN)  # type: ignore
        policies_data: dict = coins_data  # type: ignore
    except AttributeError:
        amount_lovelace = int(coins_data.split()[0] or 0)  # type: ignore
        policies_data = {}

    loaded_data = []

    if amount_lovelace:
        loaded_data.append((amount_lovelace, clusterlib.DEFAULT_COIN))

    assets_data = _load_assets(assets=policies_data)

    return [*loaded_data, *assets_data]


def _check_collateral_inputs(tx_raw_output: clusterlib.TxRawOutput, tx_loaded: dict) -> None:
    """Check collateral inputs of tx_view."""
    view_collateral = set(tx_loaded.get("collateral inputs") or [])

    all_collateral_locations: list[tp.Any] = [
        *(tx_raw_output.script_txins or ()),
        *(tx_raw_output.script_withdrawals or ()),
        *(tx_raw_output.complex_certs or ()),
        *(tx_raw_output.mint or ()),
    ]

    _collateral_ins_nested = [
        r.collaterals for r in all_collateral_locations if getattr(r, "collaterals", None)
    ]

    collateral_ins = list(itertools.chain.from_iterable(_collateral_ins_nested))

    collateral_strings = {f"{c.utxo_hash}#{c.utxo_ix}" for c in collateral_ins}

    assert collateral_strings == view_collateral, (
        f"Unexpected collateral inputs: {collateral_strings} vs {view_collateral}"
    )


def _check_reference_inputs(tx_raw_output: clusterlib.TxRawOutput, tx_loaded: dict) -> None:
    """Check reference inputs in tx_view."""
    view_reference_inputs = set(tx_loaded.get("reference inputs") or [])

    reference_txin_locations = [
        *(tx_raw_output.script_txins or ()),
        *(tx_raw_output.script_withdrawals or ()),
        *(tx_raw_output.complex_certs or ()),
        *(tx_raw_output.mint or ()),
    ]
    reference_txins = [
        s.reference_txin for s in reference_txin_locations if getattr(s, "reference_txin", None)
    ]

    reference_txins_combined: list[tp.Any] = [
        *(tx_raw_output.readonly_reference_txins or []),
        *reference_txins,
    ]

    reference_strings = {f"{r.utxo_hash}#{r.utxo_ix}" for r in reference_txins_combined}

    assert reference_strings == view_reference_inputs, (
        f"Unexpected reference inputs: {reference_strings} vs {view_reference_inputs}"
    )


def _check_inline_datums(tx_raw_output: clusterlib.TxRawOutput, tx_loaded: dict) -> None:
    """Check inline datums in tx_view."""
    raw_inline_datums = []

    for out in tx_raw_output.txouts:
        if out.inline_datum_file:
            with open(out.inline_datum_file, encoding="utf-8") as json_datum:
                raw_inline_datums.append(json.load(json_datum))

        if out.inline_datum_value:
            raw_inline_datums.append(out.inline_datum_value)

    if not raw_inline_datums:
        return

    view_datums = [out.get("datum") for out in tx_loaded.get("outputs", []) if out.get("datum")]
    not_present = [i for i in raw_inline_datums if i not in view_datums]

    assert not not_present, f"Inline datums missing in tx view:\n{not_present}"


def _check_return_collateral(tx_raw_output: clusterlib.TxRawOutput, tx_loaded: dict) -> None:
    """Check return collateral in tx_view."""
    collateral_inputs = tx_loaded.get("collateral inputs") or []
    if not collateral_inputs:
        return

    if tx_raw_output.total_collateral_amount:
        assert tx_raw_output.total_collateral_amount == tx_loaded.get("total collateral"), (
            "Return collateral total collateral mismatch"
        )

    # Automatic return collateral works only with `transaction build`
    if not (tx_raw_output.return_collateral_txouts or tx_raw_output.change_address):
        return

    # When total collateral amount is specified, it is necessary to specify also return
    # collateral `TxOut` to get the change, otherwise all collaterals will be collected
    if tx_raw_output.total_collateral_amount and not tx_raw_output.return_collateral_txouts:
        return

    return_collateral = tx_loaded.get("return collateral") or {}
    assert return_collateral, "No return collateral in tx view"

    assert "lovelace" in return_collateral.get("amount", {}), (
        "Return collateral doesn't have lovelace amount"
    )

    if tx_raw_output.return_collateral_txouts:
        assert tx_raw_output.return_collateral_txouts[0].amount == return_collateral.get(
            "amount", {}
        ).get("lovelace"), "Return collateral amount mismatch"
        return_collateral_address = tx_raw_output.return_collateral_txouts[0].address
    else:
        return_collateral_address = tx_raw_output.change_address

    assert return_collateral_address == return_collateral.get("address"), (
        "Return collateral address mismatch"
    )


def load_tx_view(cluster_obj: clusterlib.ClusterLib, tx_body_file: pl.Path) -> dict[str, tp.Any]:
    tx_view_raw = cluster_obj.g_transaction.view_tx(tx_body_file=tx_body_file)
    tx_loaded: dict[str, tp.Any] = load_raw(tx_view=tx_view_raw)
    return tx_loaded


def check_tx_view(  # noqa: C901
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput
) -> dict[str, tp.Any]:
    """Check output of the `transaction view` command."""
    tx_loaded = load_tx_view(cluster_obj=cluster_obj, tx_body_file=tx_raw_output.out_file)

    # Check inputs
    loaded_txins = set(tx_loaded.get("inputs") or [])
    _tx_raw_script_txins = list(
        itertools.chain.from_iterable(r.txins for r in tx_raw_output.script_txins)
    )
    tx_raw_script_txins = {f"{r.utxo_hash}#{r.utxo_ix}" for r in _tx_raw_script_txins}
    tx_raw_simple_txins = {f"{r.utxo_hash}#{r.utxo_ix}" for r in tx_raw_output.txins}
    tx_raw_txins = tx_raw_simple_txins.union(tx_raw_script_txins)

    if tx_raw_txins != loaded_txins:
        msg = f"txins: {tx_raw_txins} != {loaded_txins}"
        raise AssertionError(msg)

    # Check outputs
    tx_loaded_outputs = tx_loaded.get("outputs") or []
    loaded_txouts: set[tuple[str, int, str]] = set()
    for txout in tx_loaded_outputs:
        address = txout["address"]
        for amount in _load_coins_data(txout["amount"]):
            loaded_txouts.add((address, amount[0], amount[1]))

    tx_raw_txouts = {(r.address, r.amount, r.coin) for r in tx_raw_output.txouts}

    if not tx_raw_txouts.issubset(loaded_txouts):
        msg = f"txouts: {tx_raw_txouts} not in {loaded_txouts}"
        raise AssertionError(msg)

    # Check fee
    fee = int(tx_loaded.get("fee", "").split()[0] or 0)
    # For `transaction build` the `tx_raw_output.fee` can be -1
    if tx_raw_output.fee not in (
        -1,
        fee,
    ):
        msg = f"fee: {tx_raw_output.fee} != {fee}"
        raise AssertionError(msg)

    # Check validity intervals
    validity_range = tx_loaded.get("validity range") or {}

    loaded_invalid_before = validity_range.get("lower bound")
    if tx_raw_output.invalid_before != loaded_invalid_before:
        msg = f"invalid before: {tx_raw_output.invalid_before} != {loaded_invalid_before}"
        raise AssertionError(msg)

    loaded_invalid_hereafter = validity_range.get("upper bound") or validity_range.get(
        "time to live"
    )
    if tx_raw_output.invalid_hereafter != loaded_invalid_hereafter:
        msg = f"invalid hereafter: {tx_raw_output.invalid_hereafter} != {loaded_invalid_hereafter}"
        raise AssertionError(msg)

    # Check minting and burning
    loaded_mint = set(_load_assets(assets=tx_loaded.get("mint") or {}))
    mint_txouts = list(itertools.chain.from_iterable(m.txouts for m in tx_raw_output.mint))
    tx_raw_mint = {(r.amount, r.coin) for r in mint_txouts}

    if tx_raw_mint != loaded_mint:
        msg = f"mint: {tx_raw_mint} != {loaded_mint}"
        raise AssertionError(msg)

    # Check withdrawals
    tx_loaded_withdrawals = tx_loaded.get("withdrawals")
    loaded_withdrawals = set()
    if tx_loaded_withdrawals:
        for withdrawal in tx_loaded_withdrawals:
            withdrawal_key = withdrawal.get("stake credential key hash") or withdrawal.get(
                "stake credential script hash"
            )
            withdrawal_amount = int(withdrawal["amount"].split()[0] or 0)
            loaded_withdrawals.add((withdrawal_key, withdrawal_amount))

    tx_raw_withdrawals_encoded = [
        *tx_raw_output.withdrawals,
        *[s.txout for s in tx_raw_output.script_withdrawals],
    ]
    tx_raw_withdrawals = {
        (helpers.decode_bech32(r.address)[2:], r.amount) for r in tx_raw_withdrawals_encoded
    }

    if tx_raw_withdrawals != loaded_withdrawals:
        msg = f"withdrawals: {tx_raw_withdrawals} != {loaded_withdrawals}"
        raise AssertionError(msg)

    # Check certificates
    tx_raw_len_certs = len(tx_raw_output.tx_files.certificate_files) + len(
        tx_raw_output.complex_certs
    )
    loaded_len_certs = len(tx_loaded.get("certificates") or [])

    if tx_raw_len_certs != loaded_len_certs:
        msg = f"certificates: {tx_raw_len_certs} != {loaded_len_certs}"
        raise AssertionError(msg)

    for certificate in tx_loaded.get("certificates") or []:
        certificate_name = next(iter(certificate.keys()))
        certificate_fields = set(next(iter(certificate.values())).keys())
        certificate_golden = CERTIFICATES_INFORMATION.get(certificate_name)
        certificate_diff = certificate_golden and certificate_fields.difference(certificate_golden)

        if certificate_diff:
            msg = (
                f"The output of the certificate '{certificate_name}' has unexpected fields:\n"
                f"{certificate_diff}"
            )
            raise AssertionError(msg)

    # Load and check transaction era
    loaded_tx_era: str = tx_loaded["era"]
    loaded_tx_version = getattr(VERSIONS, loaded_tx_era.upper())

    output_tx_version = (
        getattr(VERSIONS, tx_raw_output.era.upper())
        if tx_raw_output.era
        else VERSIONS.DEFAULT_TX_ERA
    )

    if loaded_tx_version != output_tx_version:
        msg = f"Unexpected transaction era: {loaded_tx_version} != {output_tx_version}"
        raise AssertionError(msg)

    # Check collateral inputs, this is only available on Alonzo+ TX
    if loaded_tx_version >= VERSIONS.ALONZO:
        _check_collateral_inputs(tx_raw_output=tx_raw_output, tx_loaded=tx_loaded)

    # Check reference inputs, this is only available on Babbage+ TX on node version 1.35.3+
    if loaded_tx_version >= VERSIONS.BABBAGE and "reference inputs" in tx_loaded:
        _check_reference_inputs(tx_raw_output=tx_raw_output, tx_loaded=tx_loaded)

    # Check inline datum, this is only available on Babbage+ TX
    if loaded_tx_version >= VERSIONS.BABBAGE:
        _check_inline_datums(tx_raw_output=tx_raw_output, tx_loaded=tx_loaded)

    # Check return collateral, this is only available on Babbage+ TX on node version 1.35.3+
    if loaded_tx_version >= VERSIONS.BABBAGE and "return collateral" in tx_loaded:
        _check_return_collateral(tx_raw_output=tx_raw_output, tx_loaded=tx_loaded)

    return tx_loaded
