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
    redeemer_file: Optional[Path] = None
    redeemer_cbor_file: Optional[Path] = None
    redeemer_value: Optional[str] = None
    execution_units: Optional[Tuple[int, int]] = None


class Token(NamedTuple):
    coin: str
    amount: int


class ExpectedCost(NamedTuple):
    expected_time: int
    expected_space: int
    expected_lovelace: int


def check_plutus_cost(plutus_cost: list, expected_cost: List[ExpectedCost]):
    """Check plutus transaction cost.

    units: the time is in picoseconds and the space is in bytes.
    """
    for costs, expected_values in zip(plutus_cost, expected_cost):
        tx_time = costs["executionUnits"]["steps"]
        tx_space = costs["executionUnits"]["memory"]
        lovelace_cost = costs["lovelaceCost"]

        assert helpers.is_in_interval(tx_time, expected_values.expected_time, frac=0.15)
        assert helpers.is_in_interval(tx_space, expected_values.expected_space, frac=0.15)
        assert helpers.is_in_interval(lovelace_cost, expected_values.expected_lovelace, frac=0.15)
