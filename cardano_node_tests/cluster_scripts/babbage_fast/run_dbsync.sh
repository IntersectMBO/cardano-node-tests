#! /usr/bin/env -S nix develop --accept-flake-config github:input-output-hk/cardano-node-tests#postgres -i -k CARDANO_NODE_SOCKET_PATH -k PGHOST -k PGPORT -k PGUSER -k PGPASSFILE -k DbSyncAbortOnPanic -k DBSYNC_REPO -c bash
# shellcheck shell=bash

set -uo pipefail

SOCKET_PATH="$(readlink -m "$CARDANO_NODE_SOCKET_PATH")"
STATE_CLUSTER="${SOCKET_PATH%/*}"
STATE_CLUSTER_NAME="${STATE_CLUSTER##*/}"

export PGPASSFILE="$STATE_CLUSTER/pgpass"
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"

exec "$DBSYNC_REPO/db-sync-node/bin/cardano-db-sync" --config "./$STATE_CLUSTER_NAME/dbsync-config.yaml" --socket-path "$CARDANO_NODE_SOCKET_PATH" --state-dir "./$STATE_CLUSTER_NAME/db-sync" --schema-dir "$DBSYNC_REPO/schema"
