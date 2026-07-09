#! /usr/bin/env -S nix develop --accept-flake-config .#postgres -i -k PGHOST -k PGPORT -k PGUSER -c bash
# shellcheck shell=bash

script_dir="$(cd "$(dirname "$0")" && pwd)"
exec "$script_dir/postgres-start.sh" "$@"
