from pathlib import Path
from typing import List
from typing import NamedTuple
from typing import Optional

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

DATA_DIR = Path(__file__).parent / "data"
PLUTUS_DIR = DATA_DIR / "plutus"
SCRIPTS_V1_DIR = PLUTUS_DIR / "v1"
SCRIPTS_V2_DIR = PLUTUS_DIR / "v2"

ALWAYS_SUCCEEDS_PLUTUS_V1 = SCRIPTS_V1_DIR / "always-succeeds-spending.plutus"
ALWAYS_FAILS_PLUTUS_V1 = SCRIPTS_V1_DIR / "always-fails.plutus"
GUESSING_GAME_PLUTUS_V1 = SCRIPTS_V1_DIR / "custom-guess-42-datum-42.plutus"
GUESSING_GAME_UNTYPED_PLUTUS_V1 = SCRIPTS_V1_DIR / "guess-42-datum-42-txin.plutus"
CONTEXT_EQUIVALENCE_PLUTUS_V1 = SCRIPTS_V1_DIR / "context-equivalence-test.plutus"

ALWAYS_SUCCEEDS_PLUTUS_V2 = SCRIPTS_V2_DIR / "always-succeeds-spending.plutus"
GUESSING_GAME_PLUTUS_V2 = SCRIPTS_V2_DIR / "custom-guess-42-datum-42.plutus"
GUESSING_GAME_UNTYPED_PLUTUS_V2 = SCRIPTS_V2_DIR / "guess-42-datum-42-txin.plutus"

MINTING_PLUTUS_V1 = SCRIPTS_V1_DIR / "anyone-can-mint.plutus"
MINTING_TIME_RANGE_PLUTUS_V1 = SCRIPTS_V1_DIR / "time_range.plutus"
MINTING_CONTEXT_EQUIVALENCE_PLUTUS_V1 = SCRIPTS_V1_DIR / "minting-context-equivalence-test.plutus"
MINTING_WITNESS_REDEEMER_PLUTUS_V1 = SCRIPTS_V1_DIR / "witness-redeemer.plutus"
MINTING_TOKENNAME_PLUTUS_V1 = SCRIPTS_V1_DIR / "mint-tokenname.plutus"

STAKE_GUESS_42_PLUTUS_V1 = SCRIPTS_V1_DIR / "guess-42-stake.plutus"

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

ALWAYS_SUCCEEDS_V2_COST = ExecutionCost(per_time=230_100, per_space=1_100, fixed_cost=81)
GUESSING_GAME_V2_COST = ExecutionCost(per_time=168_868_800, per_space=540_612, fixed_cost=43_369)
GUESSING_GAME_UNTYPED_V2_COST = ExecutionCost(per_time=4_985_806, per_space=11_368, fixed_cost=1016)

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
MINTING_TOKENNAME_COST = ExecutionCost(per_time=230_732_000, per_space=539_860, fixed_cost=47_786)


# TODO: cost in old Alonzo cost model
if configuration.ALONZO_COST_MODEL or VERSIONS.cluster_era == VERSIONS.ALONZO:
    ALWAYS_SUCCEEDS_COST = ExecutionCost(per_time=476_468, per_space=1_700, fixed_cost=133)
    GUESSING_GAME_COST = ExecutionCost(per_time=327_365_461, per_space=870_842, fixed_cost=73_851)
    GUESSING_GAME_UNTYPED_COST = ExecutionCost(per_time=4_034_678, per_space=11_368, fixed_cost=947)
    MINTING_COST = ExecutionCost(per_time=358_849_733, per_space=978_434, fixed_cost=82_329)
    MINTING_TIME_RANGE_COST = ExecutionCost(
        per_time=379_793_656, per_space=1_044_064, fixed_cost=87_626
    )
    MINTING_WITNESS_REDEEMER_COST = ExecutionCost(
        per_time=369_725_712, per_space=1_013_630, fixed_cost=85_144
    )


class PlutusScriptData(NamedTuple):
    script_file: Path
    execution_cost: ExecutionCost


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


def check_plutus_cost(plutus_cost: List[dict], expected_cost: List[ExecutionCost]):
    """Check plutus transaction cost.

    units: the time is in picoseconds and the space is in bytes.
    """
    # sort records by total cost
    sorted_plutus = sorted(
        plutus_cost,
        key=lambda x: x["executionUnits"]["memory"]  # type: ignore
        + x["executionUnits"]["steps"]
        + x["lovelaceCost"],
    )
    sorted_expected = sorted(expected_cost, key=lambda x: x.per_space + x.per_time + x.fixed_cost)

    errors = []
    for costs, expected_values in zip(sorted_plutus, sorted_expected):
        tx_time = costs["executionUnits"]["steps"]
        tx_space = costs["executionUnits"]["memory"]
        lovelace_cost = costs["lovelaceCost"]

        if not helpers.is_in_interval(tx_time, expected_values.per_time, frac=0.15):
            errors.append(f"time: {tx_time} vs {expected_values.per_time}")
        if not helpers.is_in_interval(tx_space, expected_values.per_space, frac=0.15):
            errors.append(f"space: {tx_space} vs {expected_values.per_space}")
        if not helpers.is_in_interval(lovelace_cost, expected_values.fixed_cost, frac=0.15):
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
