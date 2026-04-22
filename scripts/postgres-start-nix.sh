#! /usr/bin/env -S nix develop --accept-flake-config .#postgres -i -k PGHOST -k PGPORT -k PGUSER -c bash
# shellcheck shell=bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/postgres-start.sh" "$@"
