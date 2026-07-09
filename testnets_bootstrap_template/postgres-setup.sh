#! /usr/bin/env -S nix develop --accept-flake-config github:IntersectMBO/cardano-node-tests#postgres -i -k CARDANO_NODE_SOCKET_PATH -k PGHOST -k PGPORT -k PGUSER --no-write-lock-file -c bash
# shellcheck shell=bash

set -euo pipefail

socket_path="$(readlink -m "$CARDANO_NODE_SOCKET_PATH")"
state_cluster="${socket_path%/*}"
database_name="dbsync0"

pgpassfile="$state_cluster/pgpass"
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"

echo "Deleting db $database_name"
dropdb --if-exists "$database_name" > /dev/null
echo "Setting up db $database_name"
createdb -T template0 --owner="$PGUSER" --encoding=UTF8 "$database_name"

echo "${PGHOST}:${PGPORT}:${database_name}:${PGUSER}:secret" > "$pgpassfile"
chmod 600 "$pgpassfile"
