"""Cluster and test environment configuration."""
import os
from pathlib import Path
from typing import Union


LAUNCH_PATH = Path(os.getcwd())

NETWORK_MAGIC_LOCAL = 42
DBSYNC_DB = "dbsync"
IS_XDIST = bool(os.environ.get("PYTEST_XDIST_TESTRUNUID"))

USE_CDDL = bool(os.environ.get("USE_CDDL"))  # TODO: remove once `--cddl-format` is deprecated

# TODO: for PlutusV2 cost model, used also in startup scripts as `if [ -n "$VAR" ]...`
SKIP_PLUTUSV2 = (os.environ.get("SKIP_PLUTUSV2") or "") != ""
UPDATE_COST_MODEL = (os.environ.get("UPDATE_COST_MODEL") or "") != ""
# TODO: uses old Alonzo PlutusV1 cost model - remove once it is not needed
ALONZO_COST_MODEL = (os.environ.get("ALONZO_COST_MODEL") or "") != ""

TESTNET_POOL_IDS = (
    "pool18yslg3q320jex6gsmetukxvzm7a20qd90wsll9anlkrfua38flr",
    "pool15sfcpy4tps5073gmra0e6tm2dgtrn004yr437qmeh44sgjlg2ex",
    "pool1csh8x6227uphxz67nr8qhmd8c7nsyct2ptn7t0yjkhqu7neauwu",
)

# resolve CARDANO_NODE_SOCKET_PATH
STARTUP_CARDANO_NODE_SOCKET_PATH = (
    Path(os.environ["CARDANO_NODE_SOCKET_PATH"]).expanduser().resolve()
)
os.environ["CARDANO_NODE_SOCKET_PATH"] = str(STARTUP_CARDANO_NODE_SOCKET_PATH)

# resolve SCHEDULING_LOG
SCHEDULING_LOG: Union[str, Path] = os.environ.get("SCHEDULING_LOG") or ""
if SCHEDULING_LOG:
    SCHEDULING_LOG = Path(SCHEDULING_LOG).expanduser().resolve()


CLUSTER_ERA = os.environ.get("CLUSTER_ERA") or ""
if CLUSTER_ERA not in ("", "alonzo", "babbage"):
    raise RuntimeError(f"Invalid or unsupported CLUSTER_ERA: {CLUSTER_ERA}")

TX_ERA = os.environ.get("TX_ERA") or ""
if TX_ERA not in ("", "shelley", "allegra", "mary", "alonzo", "babbage"):
    raise RuntimeError(f"Invalid TX_ERA: {TX_ERA}")

CLUSTERS_COUNT = int(os.environ.get("CLUSTERS_COUNT") or 0)
WORKERS_COUNT = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT") or 1)
CLUSTERS_COUNT = int(CLUSTERS_COUNT or (WORKERS_COUNT if WORKERS_COUNT <= 9 else 9))

DEV_CLUSTER_RUNNING = bool(os.environ.get("DEV_CLUSTER_RUNNING"))
FORBID_RESTART = bool(os.environ.get("FORBID_RESTART"))

BOOTSTRAP_DIR = os.environ.get("BOOTSTRAP_DIR") or ""

NOPOOLS = bool(os.environ.get("NOPOOLS"))

HAS_DBSYNC = bool(os.environ.get("DBSYNC_REPO"))
if HAS_DBSYNC:
    DBSYNC_BIN = (
        Path(os.environ["DBSYNC_REPO"]).expanduser() / "db-sync-node" / "bin" / "cardano-db-sync"
    ).resolve()
else:
    DBSYNC_BIN = Path("/nonexistent")

DONT_OVERWRITE_OUTFILES = bool(os.environ.get("DONT_OVERWRITE_OUTFILES"))

# determine what scripts to use to start the cluster
SCRIPTS_DIRNAME = os.environ.get("SCRIPTS_DIRNAME") or ""
if SCRIPTS_DIRNAME:
    pass
elif BOOTSTRAP_DIR and NOPOOLS:
    SCRIPTS_DIRNAME = "testnets_nopools"
elif BOOTSTRAP_DIR:
    SCRIPTS_DIRNAME = "testnets"
else:
    SCRIPTS_DIRNAME = CLUSTER_ERA or "alonzo"

SCRIPTS_DIR = Path(__file__).parent.parent / "cluster_scripts" / SCRIPTS_DIRNAME
