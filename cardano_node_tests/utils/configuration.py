"""Cluster and test environment configuration."""

import os
import pathlib as pl
import typing as tp


def _check_cardano_node_socket_path() -> None:
    """Check that `CARDANO_NODE_SOCKET_PATH` value is valid for use by testing framework."""
    socket_env = os.environ.get("CARDANO_NODE_SOCKET_PATH")
    if not socket_env:
        msg = "The `CARDANO_NODE_SOCKET_PATH` env variable is not set."
        raise RuntimeError(msg)

    socket_path = pl.Path(socket_env).expanduser().resolve()
    parts = socket_path.parts
    if not parts[-2].startswith("state-cluster") or parts[-1] not in (
        "bft1.socket",
        "relay1.socket",
    ):
        msg = "The `CARDANO_NODE_SOCKET_PATH` value is not valid for use by testing framework."
        raise RuntimeError(msg)


_check_cardano_node_socket_path()


LAUNCH_PATH = pl.Path.cwd()

NETWORK_MAGIC_LOCAL = 42
TX_SUBMISSION_DELAY = 60
DBSYNC_DB = "dbsync"
IS_XDIST = bool(os.environ.get("PYTEST_XDIST_TESTRUNUID"))

# used also in startup scripts as `if [ -n "$VAR" ]...`
ENABLE_P2P = (os.environ.get("ENABLE_P2P") or "") != ""
# used also in startup scripts as `if [ -n "$VAR" ]...`
MIXED_P2P = (os.environ.get("MIXED_P2P") or "") != ""

# used also in startup scripts as `if [ -n "$VAR" ]...`
HAS_CC = (os.environ.get("NO_CC") or "") == ""

# used also in startup scripts as `if [ -n "$VAR" ]...`
PV10 = (os.environ.get("PV10") or "") == ""

# used also in startup scripts
UTXO_BACKEND = os.environ.get("UTXO_BACKEND") or ""
if UTXO_BACKEND not in ("", "mem", "disk"):
    msg = f"Invalid UTXO_BACKEND: {UTXO_BACKEND}"
    raise RuntimeError(msg)

TESTNET_POOL_IDS = (
    "pool18yslg3q320jex6gsmetukxvzm7a20qd90wsll9anlkrfua38flr",
    "pool15sfcpy4tps5073gmra0e6tm2dgtrn004yr437qmeh44sgjlg2ex",
    "pool1csh8x6227uphxz67nr8qhmd8c7nsyct2ptn7t0yjkhqu7neauwu",
)

# resolve CARDANO_NODE_SOCKET_PATH
STARTUP_CARDANO_NODE_SOCKET_PATH = (
    pl.Path(os.environ["CARDANO_NODE_SOCKET_PATH"]).expanduser().resolve()
)
os.environ["CARDANO_NODE_SOCKET_PATH"] = str(STARTUP_CARDANO_NODE_SOCKET_PATH)

# resolve SCHEDULING_LOG
SCHEDULING_LOG: tp.Union[str, pl.Path] = os.environ.get("SCHEDULING_LOG") or ""
if SCHEDULING_LOG:
    SCHEDULING_LOG = pl.Path(SCHEDULING_LOG).expanduser().resolve()

# resolve BLOCK_PRODUCTION_DB
BLOCK_PRODUCTION_DB: tp.Union[str, pl.Path] = os.environ.get("BLOCK_PRODUCTION_DB") or ""
if BLOCK_PRODUCTION_DB:
    BLOCK_PRODUCTION_DB = pl.Path(BLOCK_PRODUCTION_DB).expanduser().resolve()

CLUSTER_ERA = os.environ.get("CLUSTER_ERA") or ""
if CLUSTER_ERA not in ("", "babbage", "conway"):
    msg = f"Invalid or unsupported CLUSTER_ERA: {CLUSTER_ERA}"
    raise RuntimeError(msg)

COMMAND_ERA = os.environ.get("COMMAND_ERA") or ""
if COMMAND_ERA not in ("", "shelley", "allegra", "mary", "alonzo", "babbage", "conway", "latest"):
    msg = f"Invalid COMMAND_ERA: {COMMAND_ERA}"
    raise RuntimeError(msg)

CLUSTERS_COUNT = int(os.environ.get("CLUSTERS_COUNT") or 0)
WORKERS_COUNT = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT") or 1)
CLUSTERS_COUNT = int(CLUSTERS_COUNT or (WORKERS_COUNT if WORKERS_COUNT <= 9 else 9))

DEV_CLUSTER_RUNNING = bool(os.environ.get("DEV_CLUSTER_RUNNING"))
FORBID_RESTART = bool(os.environ.get("FORBID_RESTART"))

BOOTSTRAP_DIR = os.environ.get("BOOTSTRAP_DIR") or ""

NUM_POOLS = int(os.environ.get("NUM_POOLS") or 3)
if not BOOTSTRAP_DIR and NUM_POOLS < 3:
    msg = f"Invalid NUM_POOLS '{NUM_POOLS}': must be >= 3"
    raise RuntimeError(msg)

HAS_DBSYNC = bool(os.environ.get("DBSYNC_REPO"))
if HAS_DBSYNC:
    DBSYNC_BIN = (
        pl.Path(os.environ["DBSYNC_REPO"]).expanduser() / "db-sync-node" / "bin" / "cardano-db-sync"
    ).resolve()
else:
    DBSYNC_BIN = pl.Path("/nonexistent")

DONT_OVERWRITE_OUTFILES = bool(os.environ.get("DONT_OVERWRITE_OUTFILES"))

# cluster instances are kept running after tests finish
KEEP_CLUSTERS_RUNNING = bool(os.environ.get("KEEP_CLUSTERS_RUNNING"))

# determine what scripts to use to start the cluster
SCRIPTS_DIRNAME = os.environ.get("SCRIPTS_DIRNAME") or ""
if SCRIPTS_DIRNAME:
    pass
elif BOOTSTRAP_DIR:
    SCRIPTS_DIRNAME = "testnets"
else:
    SCRIPTS_DIRNAME = f"{CLUSTER_ERA or 'conway'}_fast"

SCRIPTS_DIR = pl.Path(__file__).parent.parent / "cluster_scripts" / SCRIPTS_DIRNAME
