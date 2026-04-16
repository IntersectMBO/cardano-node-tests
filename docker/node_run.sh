#!/usr/bin/env bash
# Antithesis node container entrypoint.
#
# 1. Starts the cardano-node cluster on the shared 'cluster-state' volume so
#    the driver container can reach the node sockets without going over the
#    network (Unix socket on a shared Docker volume).
# 2. Serves a lightweight HTTP health check on port 8090 over the Antithesis
#    network bridge.  Returns "ready" once the cluster socket exists.
#    This cross-container HTTP traffic satisfies the Antithesis
#    "Containers joined the Antithesis network" property.
#
# Environment variables:
#   CLUSTER_STATE_DIR  Mount point of the shared cluster-state volume
#                      (default: /cluster-state).
#   TESTNET_VARIANT    Cluster variant passed to prepare_cluster_scripts
#                      (default: conway_fast).

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# 1. Force nix offline — all store paths are pre-built into the image.
# ---------------------------------------------------------------------------
echo "offline = true" >> /etc/nix/nix.conf

# ---------------------------------------------------------------------------
# 2. Point at pre-built binaries and Python venv.
#    All variables are exported so the inner nix shell inherits them.
# ---------------------------------------------------------------------------
export CARDANO_PREBUILT_DIR=/opt/cardano
export _VENV_DIR=/opt/tests-venv
export _PATH_PREPEND="/opt/cardano/cardano-node/bin:/opt/cardano/cardano-submit-api/bin:/opt/cardano/cardano-cli/bin:/opt/cardano/bech32/bin"

# ---------------------------------------------------------------------------
# 3. Cluster state lives on the shared volume so the driver can read sockets.
# ---------------------------------------------------------------------------
CLUSTER_STATE_DIR="${CLUSTER_STATE_DIR:-/cluster-state}"
export _INSTANCE_NUM=0
export _STATE_CLUSTER="${CLUSTER_STATE_DIR}/state-cluster${_INSTANCE_NUM}"
export _SCRIPTS_DEST="${CLUSTER_STATE_DIR}/startup_scripts"
export CLUSTER_STATE_DIR

# Local clusters (conway_fast, etc.) use bft1.socket.
export CARDANO_NODE_SOCKET_PATH="${_STATE_CLUSTER}/bft1.socket"

export _output_dir="${ANTITHESIS_OUTPUT_DIR:-/tmp/antithesis}"
mkdir -p "$_output_dir" "${CLUSTER_STATE_DIR}"

# ---------------------------------------------------------------------------
# 4. Health check server on port 8090 (Antithesis network bridge traffic).
#    Returns HTTP 200 "ready" once the cluster socket file exists,
#    503 "starting" while the cluster is still coming up.
#    Uses the venv Python directly — python3 is not in PATH outside a nix shell.
# ---------------------------------------------------------------------------
"${_VENV_DIR}/bin/python3" -c "
import os, socket as _s
_sock_path = os.environ.get('CARDANO_NODE_SOCKET_PATH', '')
server = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
server.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
server.bind(('0.0.0.0', 8090))
server.listen(64)
while True:
    conn, _ = server.accept()
    ready = os.path.exists(_sock_path)
    body = b'ready' if ready else b'starting'
    status = b'200 OK' if ready else b'503 Service Unavailable'
    conn.sendall(b'HTTP/1.1 ' + status + b'\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
    conn.close()
" &
_health_pid=$!
trap 'kill "$_health_pid" 2>/dev/null || true' EXIT

# ---------------------------------------------------------------------------
# 5. Prepare cluster startup scripts and run the cluster.
#    The inner script uses single quotes so the outer shell does NOT expand
#    variables — the nix shell inherits all exported vars above and the inner
#    bash expands them from its environment.  This avoids PATH corruption from
#    nested quoting (single quotes inside a double-quoted string are literal
#    and prevent $PATH from being expanded in the inner shell).
# ---------------------------------------------------------------------------
export _testnet_variant="${TESTNET_VARIANT:-conway_fast}"

set +e
# shellcheck disable=SC2016
nix develop --accept-flake-config .#testenv --command bash -c '
    set -euo pipefail
    . "$_VENV_DIR/bin/activate"
    export PATH="$_PATH_PREPEND:$PATH"

    # Instantiate cluster scripts for instance $_INSTANCE_NUM into the
    # shared volume.  --clean removes any previous attempt.
    python3 -m cardano_node_tests.prepare_cluster_scripts \
        --dest-dir "$_SCRIPTS_DEST" \
        --testnet-variant "$_testnet_variant" \
        --instance-num "$_INSTANCE_NUM" \
        --clean

    # start-cluster must run from the parent of the state-cluster directory.
    cd "$CLUSTER_STATE_DIR"
    "$_SCRIPTS_DEST/start-cluster"

    # shellcheck disable=SC2016
    printf '"'"'{"antithesis_setup": {"status": "complete", "details": {"info": ["cardano-node cluster ready, socket=%s"]}}}\n'"'"' \
        "$CARDANO_NODE_SOCKET_PATH" >> "$_output_dir/sdk.jsonl"

    # Keep the cluster alive until the container is stopped.
    tail -f /dev/null
'
_rc=$?
set -e

echo "node_run.sh exiting with code ${_rc}"
exit 0
