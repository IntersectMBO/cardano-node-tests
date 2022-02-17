from pathlib import Path


DATA_DIR = Path(__file__).parent / "data"
PLUTUS_DIR = DATA_DIR / "plutus"
MINTING_PLUTUS = PLUTUS_DIR / "anyone-can-mint.plutus"
TIME_RANGE_PLUTUS = PLUTUS_DIR / "time_range.plutus"
MINTING_CONTEXT_EQUIVALENCE_PLUTUS = PLUTUS_DIR / "minting-context-equivalence-test.plutus"
MINTING_WITNESS_REDEEMER_PLUTUS = PLUTUS_DIR / "witness-redeemer.plutus"

SIGNING_KEY_GOLDEN = DATA_DIR / "golden_normal.skey"
SIGNING_KEY_GOLDEN_EXTENDED = DATA_DIR / "golden_extended.skey"
