#!/usr/bin/env bash
# Antithesis driver container entrypoint.
#
# Runs the full test suite without any network access by:
#   1. Forcing nix into offline mode (all store paths were pre-built into
#      the image by docker/Dockerfile).
#   2. Pointing regression.sh at the pre-built cardano binaries and Python
#      venv so it skips all download / build steps.
#   3. When NODE_HOST is set (multi-container mode): waiting for the node
#      container's health check on port 8090 before running tests, and
#      setting DEV_CLUSTER_RUNNING=1 so pytest uses the pre-running cluster
#      instead of starting its own.
#   3b. Starting a local cardano-submit-api in the driver container so that
#       tests using submit_api can reach it via localhost.  The test framework
#       hard-codes http://localhost:<port> for submit_api; since submit_api in
#       the node container binds to 127.0.0.1 there (unreachable here), we
#       run our own instance against the shared cluster-state socket.
#   4. Emitting the Antithesis setup_complete lifecycle signal.
#   5. Handing off to regression.sh.
#
# Multi-container environment variables (set in docker-compose):
#   NODE_HOST          Hostname of the node container (default: unset).
#   NODE_PORT          Health check port on the node container (default: 8090).
#   CLUSTER_STATE_DIR  Mount point of the shared cluster-state volume
#                      (default: /cluster-state).
#
# This file is installed at:
#   /opt/antithesis/test/v1/quickstart/singleton_driver_regression.sh
# and is also usable directly as the docker-compose command.

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# 1. Force nix offline — all required store paths were pre-built into the
#    image.  This prevents nix from attempting any network calls at runtime,
#    which would fail inside the Antithesis environment.
# ---------------------------------------------------------------------------
echo "offline = true" >> /etc/nix/nix.conf

# ---------------------------------------------------------------------------
# 2. Tell regression.sh to use the pre-built binaries and Python venv that
#    were baked into the image at docker build time.
# ---------------------------------------------------------------------------
export CARDANO_PREBUILT_DIR=/opt/cardano
export _VENV_DIR=/opt/tests-venv

_output_dir="${ANTITHESIS_OUTPUT_DIR:-/tmp/antithesis}"
mkdir -p "$_output_dir"

# ---------------------------------------------------------------------------
# 3. Multi-container mode: wait for the node container and configure the
#    driver to use the pre-running cluster.
#
#    When NODE_HOST is set the driver polls the node's HTTP health endpoint
#    (port 8090) until it responds "ready".  This HTTP traffic is what makes
#    both containers visible on the Antithesis network bridge.
#
#    DEV_CLUSTER_RUNNING=1 tells pytest to skip cluster startup/shutdown and
#    use the cluster already started by the node container.
#    CARDANO_NODE_SOCKET_PATH_CI is pre-set to the shared volume socket path
#    so regression.sh does not override it with its default workdir path.
# ---------------------------------------------------------------------------
if [ -n "${NODE_HOST:-}" ]; then
    _node_port="${NODE_PORT:-8090}"
    echo "Waiting for ${NODE_HOST}:${_node_port} to report ready..."

    _ready=0
    for _i in $(seq 1 120); do
        # Use the venv's Python directly — python3 is not in PATH outside a nix shell.
    _resp="$("${_VENV_DIR}/bin/python3" -c "
import urllib.request, sys
try:
    r = urllib.request.urlopen('http://${NODE_HOST}:${_node_port}/', timeout=5)
    sys.stdout.write(r.read().decode())
except Exception:
    pass
" 2>/dev/null || true)"
        if [ "$_resp" = "ready" ]; then
            _ready=1
            break
        fi
        echo "  attempt ${_i}/120: node reports '${_resp:-no response}', retrying in 5s..."
        sleep 5
    done

    if [ "$_ready" -ne 1 ]; then
        echo "ERROR: node container did not become ready within 10 minutes" >&2
        printf '{"antithesis_assert": {"type": "always", "condition": false, "display_name": "Node became ready", "message": "Node container did not become ready within 10 minutes", "details": {"node_host": "%s", "node_port": "%s"}, "location": {"function": "antithesis_run.sh", "file": "antithesis_run.sh", "begin_line": 1, "begin_column": 1, "class": ""}}}\n' \
            "${NODE_HOST:-}" "${_node_port:-8090}" >> "${ANTITHESIS_OUTPUT_DIR:-/tmp/antithesis}/sdk.jsonl"
        exit 0
    fi
    echo "Node is ready."

    CLUSTER_STATE_DIR="${CLUSTER_STATE_DIR:-/cluster-state}"
    export DEV_CLUSTER_RUNNING=1
    export CLUSTERS_COUNT="${CLUSTERS_COUNT:-1}"
    # Pre-set so regression.sh does not overwrite with its default workdir path.
    export CARDANO_NODE_SOCKET_PATH_CI="${CLUSTER_STATE_DIR}/state-cluster0/bft1.socket"

    # -------------------------------------------------------------------------
    # 3b. Start a local cardano-submit-api so tests can reach it via localhost.
    #
    #     start-cluster already generated state-cluster0/run-cardano-submit-api
    #     with the correct port, socket path, and testnet magic substituted in.
    #     We run it from CLUSTER_STATE_DIR so its relative paths resolve, and
    #     put the pre-built binary on PATH.
    # -------------------------------------------------------------------------
    _submit_api_script="${CLUSTER_STATE_DIR}/state-cluster0/run-cardano-submit-api"
    if [ -x "$_submit_api_script" ]; then
        export PATH="/opt/cardano/cardano-submit-api/bin:${PATH}"
        # Derive the port the same way cluster_scripts.py does:
        #   base = PORTS_BASE + instance_num*10  (instance 0 → base = PORTS_BASE)
        #   submit_api = base + ports_per_instance - 1 - 2 = base + 7
        _submit_api_port=$(( ${PORTS_BASE:-23000} + 7 ))

        echo "Starting local cardano-submit-api on port ${_submit_api_port}..."
        (cd "${CLUSTER_STATE_DIR}" && exec "${_submit_api_script}") &
        _submit_api_pid=$!
        # Kill it when this script exits so no orphan is left behind.
        trap 'kill "${_submit_api_pid}" 2>/dev/null || true' EXIT

        # Wait up to 30 s for the port to open.
        _sa_ready=0
        for _i in $(seq 1 30); do
            if (echo >/dev/tcp/127.0.0.1/"${_submit_api_port}") 2>/dev/null; then
                _sa_ready=1
                echo "Local submit_api is ready."
                break
            fi
            echo "  waiting for local submit_api (${_i}/30)..."
            sleep 1
        done
        if [ "$_sa_ready" -ne 1 ]; then
            echo "WARNING: local submit_api did not start within 30 s; submit_api tests will fail." >&2
        fi
        unset _submit_api_port _sa_ready _i
    else
        echo "WARNING: ${_submit_api_script} not found; submit_api tests will fail." >&2
    fi
    unset _submit_api_script
fi
# _submit_api_pid is intentionally kept in scope so the EXIT trap above can kill it.

# ---------------------------------------------------------------------------
# 4. Emit the Antithesis setup_complete signal.
# ---------------------------------------------------------------------------
printf '{"antithesis_setup": {"status": "complete", "details": {"info": ["cardano-node-tests driver ready, node_rev=%s"]}}}\n' \
    "${BAKED_NODE_REV:-unknown}" >> "$_output_dir/sdk.jsonl"
unset _output_dir

# ---------------------------------------------------------------------------
# 5. Hand off to regression.sh.  The shebang in that script will invoke
#    `nix develop .#base` which now resolves entirely from the local nix
#    store (offline = true).
#
#    Do not exec directly: Antithesis treats any non-zero container exit
#    code (other than 137/143) as an error property violation.  Test
#    failures are expected and communicated via SDK assertions, not the
#    process exit code.  Always exit 0 so the container is not flagged.
# ---------------------------------------------------------------------------
set +e
/work/.github/regression.sh
_rc=$?
set -e
echo "regression.sh finished with exit code ${_rc}"
exit 0
