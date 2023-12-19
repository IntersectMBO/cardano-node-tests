#! /usr/bin/env -S nix develop --accept-flake-config github:IntersectMBO/cardano-node-tests#postgres -i -k CARDANO_NODE_SOCKET_PATH -k PGHOST -k PGPORT -k PGUSER --no-write-lock-file -c bash
# shellcheck shell=bash

set -euo pipefail

SOCKET_PATH="$(readlink -m "$CARDANO_NODE_SOCKET_PATH")"
STATE_CLUSTER="${SOCKET_PATH%/*}"
DATABASE_NAME="dbsync0"

PGPASSFILE="$STATE_CLUSTER/pgpass"
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"

echo "Deleting db $DATABASE_NAME"
dropdb --if-exists "$DATABASE_NAME" > /dev/null
echo "Setting up db $DATABASE_NAME"
createdb -T template0 --owner="$PGUSER" --encoding=UTF8 "$DATABASE_NAME"

echo "${PGHOST}:${PGPORT}:${DATABASE_NAME}:${PGUSER}:secret" > "$PGPASSFILE"
chmod 600 "$PGPASSFILE"
