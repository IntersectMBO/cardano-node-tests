#! /usr/bin/env -S nix develop --accept-flake-config github:input-output-hk/cardano-node-tests#postgres -i -k CARDANO_NODE_SOCKET_PATH -k PGHOST -k PGPORT -k PGUSER -c bash
# shellcheck shell=bash

set -euo pipefail

SOCKET_PATH="$(readlink -m "$CARDANO_NODE_SOCKET_PATH")"
STATE_CLUSTER="${SOCKET_PATH%/*}"
INSTANCE_NUM="${STATE_CLUSTER#*state-cluster}"
DATABASE_NAME="dbsync${INSTANCE_NUM}"

PGPASSFILE="$STATE_CLUSTER/pgpass"
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"

echo "Deleting db $DATABASE_NAME"
psql -d "$DATABASE_NAME" -c "SELECT pg_terminate_backend(pg_stat_activity.pid) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid();" > /dev/null 2>&1 || :
dropdb --if-exists "$DATABASE_NAME" > /dev/null
echo "Setting up db $DATABASE_NAME"
createdb -T template0 --owner="$PGUSER" --encoding=UTF8 "$DATABASE_NAME"

echo "${PGHOST}:${PGPORT}:${DATABASE_NAME}:${PGUSER}:secret" > "$PGPASSFILE"
chmod 600 "$PGPASSFILE"
