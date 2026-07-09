#! /usr/bin/env -S nix develop --accept-flake-config github:IntersectMBO/cardano-node-tests#postgres --no-write-lock-file -c bash
# shellcheck shell=bash

# Optional env vars:
#   DBSYNC_ALLOW_PRIVATE_OFFCHAIN_URLS - if truthy, start db-sync with
#     `--allow-private-offchain-urls` so it can fetch off-chain anchor data
#     served from localhost (pool / governance metadata on a local testnet).
#     Off by default.

set -uo pipefail

is_truthy() {
  local val="${1:-}"
  case "${val,,}" in
    1 | true | yes | on | enabled) return 0 ;;
    *) return 1 ;;
  esac
}

retval=0

if [ -z "${CARDANO_NODE_SOCKET_PATH:-}" ]; then
  echo "CARDANO_NODE_SOCKET_PATH is not set" >&2
  retval=1
fi
if [ -z "${DBSYNC_SCHEMA_DIR:-}" ]; then
  echo "DBSYNC_SCHEMA_DIR is not set" >&2
  retval=1
fi
if ! command -v cardano-db-sync &> /dev/null; then
  echo "cardano-db-sync is not available in PATH" >&2
  retval=1
fi
if [ "$retval" -ne 0 ]; then
  exit "$retval"
fi

SOCKET_PATH="$(readlink -m "$CARDANO_NODE_SOCKET_PATH")"
STATE_CLUSTER="${SOCKET_PATH%/*}"

export PGPASSFILE="$STATE_CLUSTER/pgpass"
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"

args=(
  --config "$STATE_CLUSTER/dbsync-config.yaml"
  --socket-path "$CARDANO_NODE_SOCKET_PATH"
  --state-dir "$STATE_CLUSTER/db-sync"
  --schema-dir "$DBSYNC_SCHEMA_DIR"
)

if is_truthy "${DBSYNC_ALLOW_PRIVATE_OFFCHAIN_URLS:-}"; then
  args+=(--allow-private-offchain-urls)
fi

exec cardano-db-sync "${args[@]}"
