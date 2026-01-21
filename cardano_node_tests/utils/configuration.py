"""Cluster and test environment configuration."""

import os
import pathlib as pl

from cardano_node_tests.utils import helpers

LAUNCH_PATH = pl.Path.cwd()

NETWORK_MAGIC_LOCAL = 42
TX_SUBMISSION_DELAY = 60
DBSYNC_DB = "dbsync"
IS_XDIST = bool(os.environ.get("PYTEST_XDIST_TESTRUNUID"))

# Make sure the ports don't overlap with ephemeral port range. It's usually 32768 to 60999.
# See `cat /proc/sys/net/ipv4/ip_local_port_range`.
PORTS_BASE = int(os.environ.get("PORTS_BASE") or 23000)

# Used also in startup scripts
ENABLE_LEGACY = helpers.is_truthy_env_var("ENABLE_LEGACY")
# Used also in startup scripts
MIXED_P2P = helpers.is_truthy_env_var("MIXED_P2P")

HAS_CC = not helpers.is_truthy_env_var("NO_CC")

# Number of new blocks before the Tx is considered confirmed. Use default value if set to 0.
CONFIRM_BLOCKS_NUM = int(os.environ.get("CONFIRM_BLOCKS_NUM") or 0)

# Used also in startup scripts
UTXO_BACKEND = os.environ.get("UTXO_BACKEND") or ""
if UTXO_BACKEND not in ("", "mem", "disk", "disklmdb", "empty"):
    __msg = f"Invalid UTXO_BACKEND: {UTXO_BACKEND}"
    raise RuntimeError(__msg)
# Used also in startup scripts
MIXED_UTXO_BACKENDS = os.environ.get("MIXED_UTXO_BACKENDS") or ""

# Resolve CARDANO_NODE_SOCKET_PATH
STARTUP_CARDANO_NODE_SOCKET_PATH = (
    pl.Path(os.environ["CARDANO_NODE_SOCKET_PATH"]).expanduser().resolve()
)
os.environ["CARDANO_NODE_SOCKET_PATH"] = str(STARTUP_CARDANO_NODE_SOCKET_PATH)

# Resolve SCHEDULING_LOG
SCHEDULING_LOG: str | pl.Path = os.environ.get("SCHEDULING_LOG") or ""
if SCHEDULING_LOG:
    SCHEDULING_LOG = pl.Path(SCHEDULING_LOG).expanduser().resolve()

# Resolve BLOCK_PRODUCTION_DB
BLOCK_PRODUCTION_DB: str | pl.Path = os.environ.get("BLOCK_PRODUCTION_DB") or ""
if BLOCK_PRODUCTION_DB:
    BLOCK_PRODUCTION_DB = pl.Path(BLOCK_PRODUCTION_DB).expanduser().resolve()

CLUSTER_ERA = os.environ.get("CLUSTER_ERA") or ""
if CLUSTER_ERA not in ("", "conway"):
    __msg = f"Invalid or unsupported CLUSTER_ERA: {CLUSTER_ERA}"
    raise RuntimeError(__msg)

COMMAND_ERA = os.environ.get("COMMAND_ERA") or ""
if COMMAND_ERA not in ("", "latest", "conway"):
    __msg = f"Invalid COMMAND_ERA: {COMMAND_ERA}"
    raise RuntimeError(__msg)

XDIST_WORKERS_COUNT = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT") or 0)
MAX_TESTS_PER_CLUSTER = int(os.environ.get("MAX_TESTS_PER_CLUSTER") or 8)
# If CLUSTERS_COUNT is not set, use the number of xdist workers or 1
CLUSTERS_COUNT = int(os.environ.get("CLUSTERS_COUNT") or 0)
CLUSTERS_COUNT = int(CLUSTERS_COUNT or (min(XDIST_WORKERS_COUNT, 9)) or 1)

DEV_CLUSTER_RUNNING = helpers.is_truthy_env_var("DEV_CLUSTER_RUNNING")
FORBID_RESTART = helpers.is_truthy_env_var("FORBID_RESTART")

BOOTSTRAP_DIR = os.environ.get("BOOTSTRAP_DIR") or ""

NUM_POOLS = int(os.environ.get("NUM_POOLS") or 3)
if not BOOTSTRAP_DIR and NUM_POOLS < 3:
    __msg = f"Invalid NUM_POOLS '{NUM_POOLS}': must be >= 3"
    raise RuntimeError(__msg)

HAS_DBSYNC = bool(os.environ.get("DBSYNC_SCHEMA_DIR"))
HAS_SMASH = HAS_DBSYNC and helpers.is_truthy_env_var("SMASH")

DONT_OVERWRITE_OUTFILES = helpers.is_truthy_env_var("DONT_OVERWRITE_OUTFILES")

# Allow unstable error messages in tests
ALLOW_UNSTABLE_ERROR_MESSAGES = helpers.is_truthy_env_var("ALLOW_UNSTABLE_ERROR_MESSAGES")

# Cluster instances are kept running after tests finish
KEEP_CLUSTERS_RUNNING = helpers.is_truthy_env_var("KEEP_CLUSTERS_RUNNING")

# Determine what scripts to use to start the cluster
TESTNET_VARIANT = os.environ.get("TESTNET_VARIANT") or ""
if TESTNET_VARIANT:
    pass
elif BOOTSTRAP_DIR:
    TESTNET_VARIANT = "testnets"
else:
    TESTNET_VARIANT = f"{CLUSTER_ERA or 'conway'}_fast"
