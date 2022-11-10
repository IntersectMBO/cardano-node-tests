#! /usr/bin/env -S nix develop --accept-flake-config github:input-output-hk/cardano-node-tests#postgres --ignore-environment --keep CARDANO_NODE_SOCKET_PATH --keep PGHOST --keep PGPORT --keep PGUSER --keep PGPASSFILE --keep DbSyncAbortOnPanic --keep DBSYNC_REPO -c bash
# shellcheck shell=bash

set -uo pipefail

SOCKET_PATH="$(readlink -m "$CARDANO_NODE_SOCKET_PATH")"
STATE_CLUSTER="${SOCKET_PATH%/*}"
STATE_CLUSTER_NAME="${STATE_CLUSTER##*/}"
INSTANCE_NUM="${STATE_CLUSTER#*state-cluster}"
DATABASE_NAME="dbsync${INSTANCE_NUM}"

export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"

export PGPASSFILE="$STATE_CLUSTER/pgpass"
echo "${PGHOST}:${PGPORT}:${DATABASE_NAME}:${PGUSER}:secret" > "$PGPASSFILE"
chmod 600 "$PGPASSFILE"

exec "$DBSYNC_REPO/db-sync-node/bin/cardano-db-sync" --config "./$STATE_CLUSTER_NAME/dbsync-config.yaml" --socket-path "$CARDANO_NODE_SOCKET_PATH" --state-dir "./$STATE_CLUSTER_NAME/db-sync" --schema-dir "$DBSYNC_REPO/schema"
