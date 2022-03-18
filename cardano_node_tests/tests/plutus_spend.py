from pathlib import Path
from typing import NamedTuple
from typing import Optional
from typing import Tuple


DATA_DIR = Path(__file__).parent / "data"
PLUTUS_DIR = DATA_DIR / "plutus"

ALWAYS_SUCCEEDS_PLUTUS = PLUTUS_DIR / "always-succeeds-spending.plutus"
ALWAYS_FAILS_PLUTUS = PLUTUS_DIR / "always-fails.plutus"
GUESSING_GAME_PLUTUS = PLUTUS_DIR / "custom-guess-42-datum-42.plutus"
CONTEXT_EQUIVALENCE_PLUTUS = PLUTUS_DIR / "context-equivalence-test.plutus"


class PlutusOp(NamedTuple):
    script_file: Path
    datum_file: Optional[Path] = None
    datum_cbor_file: Optional[Path] = None
    redeemer_file: Optional[Path] = None
    redeemer_cbor_file: Optional[Path] = None
    execution_units: Optional[Tuple[int, int]] = None


class Token(NamedTuple):
    coin: str
    amount: int
