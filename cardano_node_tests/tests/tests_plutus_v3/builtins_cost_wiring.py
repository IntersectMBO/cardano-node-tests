"""Check CIP-0168 parameter wiring by evaluating real mint bodies with changed cost models."""

import copy
import json
import pathlib as pl

import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus_v3 import builtins_mint_build as mint_build

# These are counts of evaluated calls and their input sizes in the source policies,
# independent of the proposed cost coefficients. Subset uses p=1 and outer size m=3,
# hence p * (floor(log2(m)) + 1) = 2. The assetCount policy evaluates five calls.
_PROBE_DELTAS = {
    "succeedingValueAssetCountPolicyScript": {355: (5, 0), 356: (0, 5)},
    "succeedingValuePoliciesSortedPolicyScript": {
        357: (1, 0),
        358: (3, 0),
        359: (0, 1),
        360: (0, 3),
    },
    "succeedingValueKeepSubsetPolicyScript": {361: (1, 0), 362: (2, 0), 363: (0, 1), 364: (0, 2)},
    "succeedingValueDropSubsetPolicyScript": {365: (1, 0), 366: (2, 0), 367: (0, 1), 368: (0, 2)},
}


def _offline_cost(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    tx_file: pl.Path,
    utxo_file: pl.Path,
    era_history_file: pl.Path,
    pparams: dict,
) -> tuple[int, int]:
    """Evaluate a mint body against an explicit cost model without changing the cluster."""
    pparams_file = pl.Path(f"{temp_template}_pparams.json")
    pparams_file.write_text(json.dumps(pparams), encoding="utf-8")
    cost_file = pl.Path(f"{temp_template}_cost.json")
    cluster_obj.cli(
        [
            "transaction",
            "calculate-plutus-script-cost",
            "offline",
            "--start-time-utc",
            cluster_obj.genesis["systemStart"],
            # The policies ignore the time range; extend the snapshot's prediction
            # horizon so an epoch boundary cannot make this wiring check flaky.
            "--unsafe-extend-safe-zone",
            "--era-history-file",
            str(era_history_file),
            "--utxo-file",
            str(utxo_file),
            "--protocol-params-file",
            str(pparams_file),
            "--tx-file",
            str(tx_file),
            "--out-file",
            str(cost_file),
        ]
    )
    records = json.loads(cost_file.read_text(encoding="utf-8"))
    assert len(records) == 1, "Expected one offline script cost record"
    units = records[0]["executionUnits"]
    return units["steps"], units["memory"]


def check_cost_wiring(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    variant: str,
    scripts: tuple[plutus_common.PlutusScriptData, ...],
    subtests: pytest_subtests.SubTests,
) -> None:
    """Raise each of fourteen parameters by one and require exact budget deltas.

    Build four mint bodies using a real funded input and collateral. Snapshot their
    UTxOs and era history, then evaluate offline with the node's enacted cost models.
    Each parameter must affect only its matching builtin, in the expected resource
    and amount. This detects swapped parameter groups and optimized-away calls.
    No experimental cost model is submitted to the node and no probe mint is sent.
    """
    cluster = cluster_obj
    pparams = cluster.g_query.get_protocol_params()
    language = "PlutusV3" if variant.startswith("v3_") else "PlutusV4"
    payment_addr, issuer_addr = addrs_common.get_payment_addrs(
        name_template=temp_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=2,
        fund_idx=[0],
        caching_key=f"value_cost_wiring_{variant}",
        amount=1_000_000_000,
        min_amount=100_000_000,
    )
    funds = mint_build.fund_issuer_batch(
        cluster_obj=cluster,
        temp_template=temp_template,
        payment_addr=payment_addr,
        issuer_addr=issuer_addr,
        minting_costs=[
            plutus_common.compute_cost(
                execution_cost=plutus_common.UNDETERMINED_COST, protocol_params=pparams
            )
        ],
        amount=mint_build.SCRIPT_FUND,
    )[0]

    # Preserve the actual inputs for offline evaluation, independent of later spends.
    utxo_file = pl.Path(f"{temp_template}_utxo.json")
    txin_args = [
        arg
        for utxo in (*funds.mint_utxos, *funds.collateral_utxos)
        for arg in ("--tx-in", f"{utxo.utxo_hash}#{utxo.utxo_ix}")
    ]
    cluster.g_query.query_cli(["utxo", *txin_args, "--out-file", str(utxo_file)])
    era_history_file = pl.Path(f"{temp_template}_era_history.json")
    cluster.g_query.query_cli(["era-history", "--out-file", str(era_history_file)])

    for stem, expected in _PROBE_DELTAS.items():
        with subtests.test(variant=variant, probe=stem, check="cost_wiring"):
            script = next(s for s in scripts if s.script_file.stem.startswith(f"{stem}_"))
            probe_template = f"{temp_template}_{stem}"
            policyid = cluster.g_transaction.get_policyid(script.script_file)
            token = clusterlib.TxOut(address=issuer_addr.address, amount=1, coin=f"{policyid}.41")
            tx_output = cluster.g_transaction.build_raw_tx(
                src_address=issuer_addr.address,
                tx_name=probe_template,
                txins=funds.mint_utxos,
                txouts=[clusterlib.TxOut(address=issuer_addr.address, amount=2_000_000), token],
                mint=[
                    clusterlib.Mint(
                        txouts=[token],
                        script_file=script.script_file,
                        collaterals=funds.collateral_utxos,
                        redeemer_file=plutus_common.REDEEMER_42,
                        execution_units=(0, 0),
                    )
                ],
                fee=1_000_000,
            )
            reference = _offline_cost(
                cluster_obj=cluster,
                temp_template=f"{probe_template}_reference",
                tx_file=tx_output.out_file,
                utxo_file=utxo_file,
                era_history_file=era_history_file,
                pparams=pparams,
            )
            for parameter in range(355, 369):
                with subtests.test(variant=variant, probe=stem, parameter=parameter):
                    bumped = copy.deepcopy(pparams)
                    bumped["costModels"][language][parameter] += 1
                    measured = _offline_cost(
                        cluster_obj=cluster,
                        temp_template=f"{probe_template}_{parameter}",
                        tx_file=tx_output.out_file,
                        utxo_file=utxo_file,
                        era_history_file=era_history_file,
                        pparams=bumped,
                    )
                    delta = (measured[0] - reference[0], measured[1] - reference[1])
                    assert delta == expected.get(parameter, (0, 0)), (
                        f"{variant} {stem} parameter {parameter}: {delta} != "
                        f"{expected.get(parameter, (0, 0))}"
                    )
