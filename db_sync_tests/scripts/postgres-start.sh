#! /usr/bin/env -S nix develop --accept-flake-config github:input-output-hk/cardano-node-tests#postgres -i -k PGHOST -k PGPORT -k PGUSER -c bash
# shellcheck shell=bash

set -euo pipefail

POSTGRES_DIR="${1:?"Need path to postgres dir"}"
POSTGRES_DIR="$(readlink -m "$POSTGRES_DIR")"

# set postgres env variables
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"

# kill running postgres and clear its data
if [ "${2:-""}" = "-k" ]; then
  # try to kill whatever is listening on postgres port
  listening_pid="$(lsof -i:"$PGPORT" -t || echo "")"
  if [ -n "$listening_pid" ]; then
    kill "$listening_pid"
  fi

  rm -rf "$POSTGRES_DIR/data"
fi

# setup db
if [ ! -e "$POSTGRES_DIR/data" ]; then
  mkdir -p "$POSTGRES_DIR/data"
  initdb -D "$POSTGRES_DIR/data" --encoding=UTF8 --locale=en_US.UTF-8 -A trust -U "$PGUSER"
fi

# start postgres
postgres -D "$POSTGRES_DIR/data" -k "$POSTGRES_DIR" > "$POSTGRES_DIR/postgres.log" 2>&1 &
PSQL_PID="$!"
sleep 5
cat "$POSTGRES_DIR/postgres.log"
echo
ps -fp "$PSQL_PID"
