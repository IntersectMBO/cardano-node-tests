#! /usr/bin/env -S nix develop --accept-flake-config github:input-output-hk/cardano-node-tests#postgres -i -k CARDANO_NODE_SOCKET_PATH -k PGHOST -k PGPORT -k PGUSER -c bash
# shellcheck shell=bash

set -euo pipefail

SOCKET_PATH="$(readlink -m "$CARDANO_NODE_SOCKET_PATH")"
STATE_CLUSTER="${SOCKET_PATH%/*}"
INSTANCE_NUM="${STATE_CLUSTER#*state-cluster}"
DATABASE_NAME="dbsync${INSTANCE_NUM}"

export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"

psql -A -t -d "$DATABASE_NAME" -c "SELECT block_no FROM block ORDER BY ID DESC LIMIT 1;"
