import itertools
from pathlib import Path
from typing import List
from typing import NamedTuple
from typing import Optional

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType

DATA_DIR = Path(__file__).parent / "data"
PLUTUS_DIR = DATA_DIR / "plutus"
SCRIPTS_V1_DIR = PLUTUS_DIR / "v1"
SCRIPTS_V2_DIR = PLUTUS_DIR / "v2"
SEPC256K1_ECDSA_DIR = PLUTUS_DIR / "sepc256k1_ecdsa"
SEPC256K1_SCHNORR_DIR = PLUTUS_DIR / "sepc256k1_schnorr"

ALWAYS_SUCCEEDS_PLUTUS_V1 = SCRIPTS_V1_DIR / "always-succeeds-spending.plutus"
ALWAYS_FAILS_PLUTUS_V1 = SCRIPTS_V1_DIR / "always-fails.plutus"
GUESSING_GAME_PLUTUS_V1 = SCRIPTS_V1_DIR / "custom-guess-42-datum-42.plutus"
GUESSING_GAME_UNTYPED_PLUTUS_V1 = SCRIPTS_V1_DIR / "guess-42-datum-42-txin.plutus"
CONTEXT_EQUIVALENCE_PLUTUS_V1 = SCRIPTS_V1_DIR / "context-equivalence-test.plutus"

ALWAYS_SUCCEEDS_PLUTUS_V2 = SCRIPTS_V2_DIR / "always-succeeds-spending.plutus"
ALWAYS_FAILS_PLUTUS_V2 = SCRIPTS_V2_DIR / "always-fails.plutus"
GUESSING_GAME_PLUTUS_V2 = SCRIPTS_V2_DIR / "custom-guess-42-datum-42.plutus"
GUESSING_GAME_UNTYPED_PLUTUS_V2 = SCRIPTS_V2_DIR / "guess-42-datum-42-txin.plutus"
SECP256K1_LOOP_ECDSA_PLUTUS_V2 = SCRIPTS_V2_DIR / "ecdsa-secp256k1-loop.plutus"
SECP256K1_LOOP_SCHNORR_PLUTUS_V2 = SCRIPTS_V2_DIR / "schnorr-secp256k1-loop.plutus"

MINTING_PLUTUS_V1 = SCRIPTS_V1_DIR / "anyone-can-mint.plutus"
MINTING_TIME_RANGE_PLUTUS_V1 = SCRIPTS_V1_DIR / "time_range.plutus"
MINTING_CONTEXT_EQUIVALENCE_PLUTUS_V1 = SCRIPTS_V1_DIR / "minting-context-equivalence-test.plutus"
MINTING_WITNESS_REDEEMER_PLUTUS_V1 = SCRIPTS_V1_DIR / "witness-redeemer.plutus"
MINTING_TOKENNAME_PLUTUS_V1 = SCRIPTS_V1_DIR / "mint-tokenname.plutus"

MINTING_PLUTUS_V2 = SCRIPTS_V2_DIR / "anyone-can-mint.plutus"
MINTING_CHECK_REF_INPUTS_PLUTUS_V2 = SCRIPTS_V2_DIR / "check-mint-with-reference-inputs.plutus"
MINTING_CHECK_DATUM_HASH_PLUTUS_V2 = SCRIPTS_V2_DIR / "check-mint-datum-hash.plutus"
MINTING_CHECK_REF_SCRIPTS_PLUTUS_V2 = SCRIPTS_V2_DIR / "check-mint-with-reference-scripts.plutus"
MINTING_CHECK_INLINE_DATUM_PLUTUS_V2 = SCRIPTS_V2_DIR / "check-mint-inline-datum.plutus"
MINTING_SECP256K1_ECDSA_PLUTUS_V2 = SCRIPTS_V2_DIR / "secp256k1-ecdsa-policy.plutus"
MINTING_SECP256K1_SCHNORR_PLUTUS_V2 = SCRIPTS_V2_DIR / "secp256k1-schnorr-policy.plutus"

STAKE_GUESS_42_PLUTUS_V1 = SCRIPTS_V1_DIR / "guess-42-stake.plutus"

STAKE_PLUTUS_V2 = SCRIPTS_V2_DIR / "stake-script.plutus"

REDEEMER_42 = PLUTUS_DIR / "42.redeemer"
REDEEMER_42_TYPED = PLUTUS_DIR / "typed-42.redeemer"
REDEEMER_42_CBOR = PLUTUS_DIR / "42.redeemer.cbor"
REDEEMER_42_TYPED_CBOR = PLUTUS_DIR / "typed-42.redeemer.cbor"
REDEEMER_43_TYPED = PLUTUS_DIR / "typed-43.redeemer"

DATUM_42 = PLUTUS_DIR / "42.datum"
DATUM_42_TYPED = PLUTUS_DIR / "typed-42.datum"
DATUM_42_CBOR = PLUTUS_DIR / "42.datum.cbor"
DATUM_42_TYPED_CBOR = PLUTUS_DIR / "typed-42.datum.cbor"
DATUM_43_TYPED = PLUTUS_DIR / "typed-43.datum"
DATUM_WITNESS_GOLDEN_NORMAL = PLUTUS_DIR / "witness_golden_normal.datum"
DATUM_WITNESS_GOLDEN_EXTENDED = PLUTUS_DIR / "witness_golden_extended.datum"
DATUM_BIG = PLUTUS_DIR / "big.datum"
DATUM_FINITE_TYPED_CBOR = PLUTUS_DIR / "typed-finite.datum.cbor"

SIGNING_KEY_GOLDEN = DATA_DIR / "golden_normal.skey"
SIGNING_KEY_GOLDEN_EXTENDED = DATA_DIR / "golden_extended.skey"


class ExecutionCost(NamedTuple):
    per_time: int
    per_space: int
    fixed_cost: int


# scripts execution cost for Txs with single UTxO input and single Plutus script
ALWAYS_FAILS_COST = ExecutionCost(per_time=476_468, per_space=1_700, fixed_cost=133)
ALWAYS_SUCCEEDS_COST = ExecutionCost(per_time=368_100, per_space=1_700, fixed_cost=125)
GUESSING_GAME_COST = ExecutionCost(per_time=236_715_138, per_space=870_842, fixed_cost=67_315)
GUESSING_GAME_UNTYPED_COST = ExecutionCost(per_time=4_985_806, per_space=11_368, fixed_cost=1_016)
# TODO: fix once context equivalence tests can run again
CONTEXT_EQUIVALENCE_COST = ExecutionCost(per_time=100_000_000, per_space=1_000_00, fixed_cost=947)

ALWAYS_FAILS_V2_COST = ExecutionCost(per_time=230_100, per_space=1_100, fixed_cost=81)
ALWAYS_SUCCEEDS_V2_COST = ExecutionCost(per_time=230_100, per_space=1_100, fixed_cost=81)
GUESSING_GAME_V2_COST = ExecutionCost(per_time=168_868_800, per_space=540_612, fixed_cost=43_369)
GUESSING_GAME_UNTYPED_V2_COST = ExecutionCost(
    per_time=4_985_806, per_space=11_368, fixed_cost=1_016
)
SECP256K1_ECDSA_LOOP_COST = ExecutionCost(
    per_time=397_863_996, per_space=128_584, fixed_cost=36_106
)
SECP256K1_SCHNORR_LOOP_COST = ExecutionCost(
    per_time=430_445_916, per_space=128_584, fixed_cost=38_455
)

MINTING_COST = ExecutionCost(per_time=259_868_784, per_space=978_434, fixed_cost=74_960)
MINTING_TIME_RANGE_COST = ExecutionCost(
    per_time=277_239_670, per_space=1_044_064, fixed_cost=80_232
)
# TODO: fix once context equivalence tests can run again
MINTING_CONTEXT_EQUIVALENCE_COST = ExecutionCost(
    per_time=358_849_733, per_space=978_434, fixed_cost=82_329
)
MINTING_WITNESS_REDEEMER_COST = ExecutionCost(
    per_time=261_056_789, per_space=1_013_630, fixed_cost=75_278
)
MINTING_TOKENNAME_COST = ExecutionCost(per_time=162_418_952, per_space=539_860, fixed_cost=42_861)

MINTING_V2_COST = ExecutionCost(per_time=167_089_597, per_space=537_352, fixed_cost=43_053)
MINTING_V2_REF_COST = ExecutionCost(per_time=198_080_433, per_space=633_678, fixed_cost=50_845)
MINTING_V2_CHECK_REF_INPUTS_COST = ExecutionCost(
    per_time=214_916_514, per_space=696_858, fixed_cost=55_705
)
MINTING_V2_CHECK_DATUM_HASH_COST = ExecutionCost(
    per_time=244_944_118, per_space=797_302, fixed_cost=63_665
)
MINTING_V2_CHECK_REF_SCRIPTS_COST = ExecutionCost(
    per_time=208_713_230, per_space=678_512, fixed_cost=54_199
)
MINTING_V2_CHECK_INLINE_DATUM_COST = ExecutionCost(
    per_time=208_093_920, per_space=674_744, fixed_cost=53_937
)


class PlutusScriptData(NamedTuple):
    script_file: Path
    execution_cost: ExecutionCost


ALWAYS_FAILS = {
    "v1": PlutusScriptData(script_file=ALWAYS_FAILS_PLUTUS_V1, execution_cost=ALWAYS_FAILS_COST),
    "v2": PlutusScriptData(script_file=ALWAYS_FAILS_PLUTUS_V2, execution_cost=ALWAYS_FAILS_V2_COST),
}

ALWAYS_SUCCEEDS = {
    "v1": PlutusScriptData(
        script_file=ALWAYS_SUCCEEDS_PLUTUS_V1, execution_cost=ALWAYS_SUCCEEDS_COST
    ),
    "v2": PlutusScriptData(
        script_file=ALWAYS_SUCCEEDS_PLUTUS_V2, execution_cost=ALWAYS_SUCCEEDS_V2_COST
    ),
}

GUESSING_GAME = {
    "v1": PlutusScriptData(script_file=GUESSING_GAME_PLUTUS_V1, execution_cost=GUESSING_GAME_COST),
    "v2": PlutusScriptData(
        script_file=GUESSING_GAME_PLUTUS_V2, execution_cost=GUESSING_GAME_V2_COST
    ),
}

GUESSING_GAME_UNTYPED = {
    "v1": PlutusScriptData(
        script_file=GUESSING_GAME_UNTYPED_PLUTUS_V1, execution_cost=GUESSING_GAME_UNTYPED_COST
    ),
    "v2": PlutusScriptData(
        script_file=GUESSING_GAME_UNTYPED_PLUTUS_V2, execution_cost=GUESSING_GAME_UNTYPED_V2_COST
    ),
}

MINTING_PLUTUS = {
    "v1": PlutusScriptData(script_file=MINTING_PLUTUS_V1, execution_cost=MINTING_COST),
    "v2": PlutusScriptData(script_file=MINTING_PLUTUS_V2, execution_cost=MINTING_V2_COST),
}


class PlutusOp(NamedTuple):
    script_file: Path
    datum_file: Optional[Path] = None
    datum_cbor_file: Optional[Path] = None
    datum_value: Optional[str] = None
    redeemer_file: Optional[Path] = None
    redeemer_cbor_file: Optional[Path] = None
    redeemer_value: Optional[str] = None
    execution_cost: Optional[ExecutionCost] = None


class Token(NamedTuple):
    coin: str
    amount: int


class ScriptCost(NamedTuple):
    fee: int
    collateral: int  # Lovelace amount > minimum UTxO value
    min_collateral: int  # minimum needed collateral


def check_plutus_costs(
    plutus_costs: List[dict], expected_costs: List[ExecutionCost], frac: float = 0.15
):
    """Check plutus transaction cost.

    units: the time is in picoseconds and the space is in bytes.
    """
    # sort records by total cost
    sorted_plutus = sorted(
        plutus_costs,
        key=lambda x: x["executionUnits"]["memory"]  # type: ignore
        + x["executionUnits"]["steps"]
        + x["lovelaceCost"],
    )
    sorted_expected = sorted(expected_costs, key=lambda x: x.per_space + x.per_time + x.fixed_cost)

    errors = []
    for costs, expected_values in zip(sorted_plutus, sorted_expected):
        tx_time = costs["executionUnits"]["steps"]
        tx_space = costs["executionUnits"]["memory"]
        lovelace_cost = costs["lovelaceCost"]

        if not helpers.is_in_interval(tx_time, expected_values.per_time, frac=frac):
            errors.append(f"time: {tx_time} vs {expected_values.per_time}")
        if not helpers.is_in_interval(tx_space, expected_values.per_space, frac=frac):
            errors.append(f"space: {tx_space} vs {expected_values.per_space}")
        if not helpers.is_in_interval(lovelace_cost, expected_values.fixed_cost, frac=frac):
            errors.append(f"fixed cost: {lovelace_cost} vs {expected_values.fixed_cost}")

    if errors:
        raise AssertionError("\n".join(errors))


def get_cost_per_unit(protocol_params: dict) -> ExecutionCost:
    """Get execution cost per unit in Lovelace."""
    return ExecutionCost(
        per_time=protocol_params["executionUnitPrices"]["priceSteps"] or 0,
        per_space=protocol_params["executionUnitPrices"]["priceMemory"] or 0,
        fixed_cost=protocol_params["txFeeFixed"] or 0,
    )


def compute_cost(
    execution_cost: ExecutionCost, protocol_params: dict, collateral_fraction_offset: float = 1.0
) -> ScriptCost:
    """Compute fee and collateral required for the Plutus script."""
    cost_per_unit = get_cost_per_unit(protocol_params=protocol_params)
    fee_redeem = (
        round(
            execution_cost.per_time * cost_per_unit.per_time
            + execution_cost.per_space * cost_per_unit.per_space
        )
        + execution_cost.fixed_cost
    )

    collateral_fraction = protocol_params["collateralPercentage"] / 100
    min_collateral = int(fee_redeem * collateral_fraction * collateral_fraction_offset)
    collateral_amount = min_collateral if min_collateral >= 2_000_000 else 2_000_000

    return ScriptCost(fee=fee_redeem, collateral=collateral_amount, min_collateral=min_collateral)


def txout_factory(
    address: str,
    amount: int,
    plutus_op: PlutusOp,
    coin: str = clusterlib.DEFAULT_COIN,
    embed_datum: bool = False,
    inline_datum: bool = False,
) -> clusterlib.TxOut:
    """Create `TxOut` object."""
    datum_hash_file: FileType = ""
    datum_hash_cbor_file: FileType = ""
    datum_hash_value = ""
    datum_embed_file: FileType = ""
    datum_embed_cbor_file: FileType = ""
    datum_embed_value = ""
    inline_datum_file: FileType = ""
    inline_datum_cbor_file: FileType = ""
    inline_datum_value = ""

    if embed_datum:
        datum_embed_file = plutus_op.datum_file or ""
        datum_embed_cbor_file = plutus_op.datum_cbor_file or ""
        datum_embed_value = plutus_op.datum_value or ""
    elif inline_datum:
        inline_datum_file = plutus_op.datum_file or ""
        inline_datum_cbor_file = plutus_op.datum_cbor_file or ""
        inline_datum_value = plutus_op.datum_value or ""
    else:
        datum_hash_file = plutus_op.datum_file or ""
        datum_hash_cbor_file = plutus_op.datum_cbor_file or ""
        datum_hash_value = plutus_op.datum_value or ""

    txout = clusterlib.TxOut(
        address=address,
        amount=amount,
        coin=coin,
        datum_hash_file=datum_hash_file,
        datum_hash_cbor_file=datum_hash_cbor_file,
        datum_hash_value=datum_hash_value,
        datum_embed_file=datum_embed_file,
        datum_embed_cbor_file=datum_embed_cbor_file,
        datum_embed_value=datum_embed_value,
        inline_datum_file=inline_datum_file,
        inline_datum_cbor_file=inline_datum_cbor_file,
        inline_datum_value=inline_datum_value,
    )
    return txout


def check_return_collateral(cluster_obj: clusterlib.ClusterLib, tx_output: clusterlib.TxRawOutput):
    """Check if collateral is returned on Plutus script failure."""
    return_collateral_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    protocol_params = cluster_obj.g_query.get_protocol_params()

    # when total collateral amount is specified, it is necessary to specify also return
    # collateral `TxOut` to get the change, otherwise all collaterals will be collected
    if tx_output.total_collateral_amount and not tx_output.return_collateral_txouts:
        assert not return_collateral_utxos, "Return collateral UTxO was unexpectedly created"
        return

    if not (tx_output.return_collateral_txouts or tx_output.total_collateral_amount):
        return

    # check that correct return collateral UTxO was created
    assert return_collateral_utxos, "Return collateral UTxO was NOT created"

    # check that return collateral is the only output and that the index matches
    out_utxos_ix = {r.utxo_ix for r in return_collateral_utxos}
    assert len(out_utxos_ix) == 1, "There are other outputs other than return collateral"
    # TODO: the index of change can be either 0 (in old node versions) or `txouts_count`,
    # that affects index of return collateral UTxO
    assert return_collateral_utxos[0].utxo_ix in (
        tx_output.txouts_count,
        tx_output.txouts_count + 1,
    )

    returned_amount = clusterlib.calculate_utxos_balance(utxos=return_collateral_utxos)

    tx_collaterals_nested = [
        r.collaterals
        for r in (
            *tx_output.script_txins,
            *tx_output.mint,
            *tx_output.complex_certs,
            *tx_output.script_withdrawals,
        )
    ]
    tx_collaterals = list(set(itertools.chain.from_iterable(tx_collaterals_nested)))
    tx_collaterals_amount = clusterlib.calculate_utxos_balance(utxos=tx_collaterals)

    collateral_charged = tx_collaterals_amount - return_collateral_utxos[0].amount

    tx_tokens = {r.coin for r in tx_collaterals if r.coin != clusterlib.DEFAULT_COIN}

    if tx_output.return_collateral_txouts:
        return_txouts_amount = clusterlib.calculate_utxos_balance(
            utxos=list(tx_output.return_collateral_txouts)
        )
        assert (
            returned_amount == return_txouts_amount
        ), f"Incorrect amount for return collateral: {returned_amount} != {return_txouts_amount}"

        tx_return_addresses = {r.address for r in tx_output.return_collateral_txouts}
        return_utxos_addresses = {r.address for r in return_collateral_utxos}
        assert tx_return_addresses == return_utxos_addresses, (
            "Return collateral addresses don't match: "
            f"{tx_return_addresses} != {return_utxos_addresses}"
        )

        for coin in tx_tokens:
            assert clusterlib.calculate_utxos_balance(
                utxos=return_collateral_utxos, coin=coin
            ) == clusterlib.calculate_utxos_balance(
                utxos=tx_output.return_collateral_txouts, coin=coin
            ), f"Incorrect return collateral token balance for token '{coin}'"

    # automatic return collateral with `transaction build` command
    elif tx_output.change_address:
        # check that the collateral amount charged corresponds to 'collateralPercentage'
        assert collateral_charged == round(
            tx_output.fee * protocol_params["collateralPercentage"] / 100
        ), "The collateral amount charged is not the expected amount"

        assert (
            tx_output.change_address == return_collateral_utxos[0].address
        ), "Return collateral address doesn't match change address"

        # the returned amount is the total of all collaterals minus fee
        expected_return_amount = int(tx_collaterals_amount - collateral_charged)

        assert returned_amount == expected_return_amount, (
            "TX collateral output amount doesn't match "
            f"({returned_amount} != {expected_return_amount})"
        )

        for coin in tx_tokens:
            assert clusterlib.calculate_utxos_balance(
                utxos=return_collateral_utxos, coin=coin
            ) == clusterlib.calculate_utxos_balance(
                utxos=tx_collaterals, coin=coin
            ), f"Incorrect return collateral token balance for token '{coin}'"

    dbsync_utils.check_tx_phase_2_failure(
        cluster_obj=cluster_obj,
        tx_raw_output=tx_output,
        collateral_charged=collateral_charged,
    )


def check_secp_expected_error_msg(cluster_obj: clusterlib.ClusterLib, algorithm: str, err_msg: str):
    """Check expected error message when using SECP functions."""
    before_pv8 = cluster_obj.g_query.get_protocol_params()["protocolVersion"]["major"] < 8

    # the SECP256k1 functions should work from PV8
    # before PV8 the SECP256k1 is blocked or limited by high cost model
    is_forbidden = (
        "Forbidden builtin function: (builtin "
        f"verify{algorithm.capitalize()}Secp256k1Signature)" in err_msg
        or f"Builtin function Verify{algorithm.capitalize()}Secp256k1Signature "
        "is not available in language PlutusV2 at and protocol version 7.0" in err_msg
        or "MalformedScriptWitnesses" in err_msg
    )

    is_overspending = (
        "The machine terminated part way through evaluation due to "
        "overspending the budget." in err_msg
    )

    if before_pv8 and (is_forbidden or is_overspending):
        pytest.xfail("The SECP256k1 builtin functions are not allowed before protocol version 8")

    pytest.fail(err_msg)
