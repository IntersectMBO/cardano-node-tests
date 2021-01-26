"""Cluster and test environment configuration."""
import os
from pathlib import Path


CLUSTER_ERA = os.environ.get("CLUSTER_ERA") or "shelley"
if CLUSTER_ERA not in ("shelley", "allegra", "mary"):
    raise RuntimeError(f"Invalid CLUSTER_ERA`: `{CLUSTER_ERA}")

TX_ERA = os.environ.get("TX_ERA") or ""
if TX_ERA not in ("", "shelley", "allegra", "mary"):
    raise RuntimeError(f"Invalid TX_ERA`: `{TX_ERA}")

SCRIPTS_DIR = Path(__file__).parent.parent / "cluster_scripts" / CLUSTER_ERA
