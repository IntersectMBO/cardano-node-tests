from pathlib import Path
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple

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


class PlutusOp(NamedTuple):
    script_file: Path
    datum_file: Optional[Path] = None
    datum_cbor_file: Optional[Path] = None
    datum_value: Optional[str] = None
    redeemer_file: Optional[Path] = None
    redeemer_cbor_file: Optional[Path] = None
    redeemer_value: Optional[str] = None
    execution_units: Optional[Tuple[int, int]] = None


class Token(NamedTuple):
    coin: str
    amount: int


class ExecutionCost(NamedTuple):
    per_time: int
    per_space: int
    fixed_cost: int


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


def get_execution_cost(protocol_params: dict):
    """Get execution cost per unit in Lovelace."""
    return ExecutionCost(
        per_time=protocol_params["executionUnitPrices"]["priceSteps"] or 0,
        per_space=protocol_params["executionUnitPrices"]["priceMemory"] or 0,
        fixed_cost=protocol_params["txFeeFixed"] or 0,
    )
