"""Checks for `transaction view` CLI command."""
import itertools
import logging
import re
from typing import Dict
from typing import List
from typing import Set
from typing import Tuple
from typing import Union

import yaml
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


def load_tx_view(tx_view: str) -> dict:
    """Load tx view output as YAML."""
    tx_loaded: dict = yaml.safe_load(tx_view)
    return tx_loaded


def _load_assets(assets: Dict[str, Dict[str, int]]) -> List[Tuple[int, str]]:
    loaded_data = []

    for policy_key, policy_rec in assets.items():
        if policy_key == clusterlib.DEFAULT_COIN:
            continue
        if "policy " in policy_key:
            policy_key = policy_key.replace("policy ", "")
        for asset_name, amount in policy_rec.items():
            if "asset " in asset_name:
                asset_name = re.search(r"asset ([0-9a-f]*)", asset_name).group(1)  # type: ignore
            elif asset_name == "default asset":
                asset_name = ""
            token = f"{policy_key}.{asset_name}" if asset_name else policy_key
            loaded_data.append((amount, token))

    return loaded_data


def _load_coins_data(coins_data: Union[dict, str]) -> List[Tuple[int, str]]:
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


def check_tx_view(  # noqa: C901
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput
) -> dict:
    """Check output of the `transaction view` command."""
    # pylint: disable=too-many-branches
    tx_view_raw = cluster_obj.view_tx(tx_body_file=tx_raw_output.out_file)
    tx_loaded: dict = load_tx_view(tx_view=tx_view_raw)

    # check inputs
    loaded_txins = set(tx_loaded.get("inputs") or [])
    tx_raw_txins = {f"{r.utxo_hash}#{r.utxo_ix}" for r in tx_raw_output.txins}

    if tx_raw_txins != loaded_txins:
        raise AssertionError(f"txins: {tx_raw_txins} != {loaded_txins}")

    # check outputs
    tx_loaded_outputs = tx_loaded.get("outputs") or []
    loaded_txouts: Set[Tuple[str, int, str]] = set()
    for txout in tx_loaded_outputs:
        address = txout["address"]
        for amount in _load_coins_data(txout["amount"]):
            loaded_txouts.add((address, amount[0], amount[1]))

    tx_raw_txouts = {(r.address, r.amount, r.coin) for r in tx_raw_output.txouts}

    if not tx_raw_txouts.issubset(loaded_txouts):
        raise AssertionError(f"txouts: {tx_raw_txouts} not in {loaded_txouts}")

    # check fee
    fee = int(tx_loaded.get("fee", "").split()[0] or 0)
    # pylint: disable=consider-using-in
    if (
        tx_raw_output.fee != -1 and tx_raw_output.fee != fee
    ):  # for `transaction build` the `tx_raw_output.fee` can be -1
        raise AssertionError(f"fee: {tx_raw_output.fee} != {fee}")

    # check validity intervals
    validity_range = tx_loaded.get("validity range") or {}

    loaded_invalid_before = validity_range.get("lower bound")
    if tx_raw_output.invalid_before != loaded_invalid_before:
        raise AssertionError(
            f"invalid before: {tx_raw_output.invalid_before} != {loaded_invalid_before}"
        )

    loaded_invalid_hereafter = validity_range.get("upper bound") or validity_range.get(
        "time to live"
    )
    if tx_raw_output.invalid_hereafter != loaded_invalid_hereafter:
        raise AssertionError(
            f"invalid hereafter: {tx_raw_output.invalid_hereafter} != {loaded_invalid_hereafter}"
        )

    # check minting and burning
    loaded_mint = set(_load_assets(assets=tx_loaded.get("mint") or {}))
    mint_txouts = list(itertools.chain.from_iterable(m.txouts for m in tx_raw_output.mint))
    tx_raw_mint = {(r.amount, r.coin) for r in mint_txouts}

    if tx_raw_mint != loaded_mint:
        raise AssertionError(f"mint: {tx_raw_mint} != {loaded_mint}")

    # check withdrawals
    tx_loaded_withdrawals = tx_loaded.get("withdrawals")
    loaded_withdrawals = set()
    if tx_loaded_withdrawals:
        for withdrawal in tx_loaded_withdrawals:
            withdrawal_key = withdrawal["credential"]["key hash"]
            withdrawal_amount = int(withdrawal["amount"].split()[0] or 0)
            loaded_withdrawals.add((withdrawal_key, withdrawal_amount))

    tx_raw_withdrawals = {
        (helpers.decode_bech32(r.address)[2:], r.amount) for r in tx_raw_output.withdrawals
    }

    if tx_raw_withdrawals != loaded_withdrawals:
        raise AssertionError(f"withdrawals: {tx_raw_withdrawals} != {loaded_withdrawals}")

    # check certificates
    tx_raw_len_certs = len(set(tx_raw_output.tx_files.certificate_files))
    loaded_len_certs = len(set(tx_loaded.get("certificates") or ()))

    if tx_raw_len_certs != loaded_len_certs:
        raise AssertionError(f"certificates: {tx_raw_len_certs} != {loaded_len_certs}")

    return tx_loaded
