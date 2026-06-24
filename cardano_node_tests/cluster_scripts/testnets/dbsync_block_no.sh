#! /usr/bin/env -S nix develop --accept-flake-config github:IntersectMBO/cardano-node-tests#postgres -i -k CARDANO_NODE_SOCKET_PATH -k PGHOST -k PGPORT -k PGUSER -c bash
# shellcheck shell=bash

set -euo pipefail

socket_path="$(readlink -m "$CARDANO_NODE_SOCKET_PATH")"
state_cluster="${socket_path%/*}"
instance_num="${state_cluster#*state-cluster}"
database_name="dbsync${instance_num}"

export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"

psql -A -t -d "$database_name" -c "SELECT block_no FROM block ORDER BY ID DESC LIMIT 1;"
