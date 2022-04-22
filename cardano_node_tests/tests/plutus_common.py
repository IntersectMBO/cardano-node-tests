from pathlib import Path
from typing import List
from typing import NamedTuple
from typing import Optional

from cardano_node_tests.utils import helpers

DATA_DIR = Path(__file__).parent / "data"
PLUTUS_DIR = DATA_DIR / "plutus"

ALWAYS_SUCCEEDS_PLUTUS = PLUTUS_DIR / "always-succeeds-spending.plutus"
ALWAYS_FAILS_PLUTUS = PLUTUS_DIR / "always-fails.plutus"
GUESSING_GAME_PLUTUS = PLUTUS_DIR / "custom-guess-42-datum-42.plutus"
GUESSING_GAME_UNTYPED_PLUTUS = PLUTUS_DIR / "guess-42-datum-42-txin.plutus"
CONTEXT_EQUIVALENCE_PLUTUS = PLUTUS_DIR / "context-equivalence-test.plutus"

MINTING_PLUTUS = PLUTUS_DIR / "anyone-can-mint.plutus"
MINTING_TIME_RANGE_PLUTUS = PLUTUS_DIR / "time_range.plutus"
MINTING_CONTEXT_EQUIVALENCE_PLUTUS = PLUTUS_DIR / "minting-context-equivalence-test.plutus"
MINTING_WITNESS_REDEEMER_PLUTUS = PLUTUS_DIR / "witness-redeemer.plutus"

STAKE_GUESS_42_PLUTUS = PLUTUS_DIR / "guess-42-stake.plutus"

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
ALWAYS_SUCCEEDS_COST = ExecutionCost(per_time=476_468, per_space=1_700, fixed_cost=133)
GUESSING_GAME_COST = ExecutionCost(per_time=327_365_461, per_space=870_842, fixed_cost=73_851)
GUESSING_GAME_UNTYPED_COST = ExecutionCost(per_time=4_034_678, per_space=11_368, fixed_cost=947)
# TODO: fix once context equivalence tests can run again
CONTEXT_EQUIVALENCE_COST = ExecutionCost(per_time=100_000_000, per_space=1_000_00, fixed_cost=947)

MINTING_COST = ExecutionCost(per_time=358_849_733, per_space=978_434, fixed_cost=82_329)
MINTING_TIME_RANGE_COST = ExecutionCost(
    per_time=379_793_656, per_space=1_044_064, fixed_cost=87_626
)
# TODO: fix once context equivalence tests can run again
MINTING_CONTEXT_EQUIVALENCE_COST = ExecutionCost(
    per_time=358_849_733, per_space=978_434, fixed_cost=82_329
)
MINTING_WITNESS_REDEEMER_COST = ExecutionCost(
    per_time=369_725_712, per_space=1_013_630, fixed_cost=85_144
)


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
    collateral: int


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

    for costs, expected_values in zip(sorted_plutus, sorted_expected):
        tx_time = costs["executionUnits"]["steps"]
        tx_space = costs["executionUnits"]["memory"]
        lovelace_cost = costs["lovelaceCost"]

        assert helpers.is_in_interval(tx_time, expected_values.per_time, frac=0.15)
        assert helpers.is_in_interval(tx_space, expected_values.per_space, frac=0.15)
        assert helpers.is_in_interval(lovelace_cost, expected_values.fixed_cost, frac=0.15)


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
    _collateral_amount = int(fee_redeem * collateral_fraction * collateral_fraction_offset)
    collateral_amount = _collateral_amount if _collateral_amount >= 2_000_000 else 2_000_000

    return ScriptCost(fee=fee_redeem, collateral=collateral_amount)
