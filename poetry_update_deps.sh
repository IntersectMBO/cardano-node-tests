#!/bin/sh

abort_install=0

set -eu

if ! command -v poetry >/dev/null 2>&1; then
  echo "Poetry is not installed. Please install it first." >&2
  abort_install=1
fi
if [ ! -d "cardano_node_tests" ]; then
  echo "This script is supposed to run from the root of cardano-node-tests directory." >&2
  abort_install=1
fi
if [ "$abort_install" -eq 1 ]; then
  exit 1
fi

# Filter out nix python packages from PYTHONPATH.
# This avoids conflicts between nix-installed packages and poetry virtual environment packages.
PYTHONPATH="$(echo "${PYTHONPATH:-}" | tr ":" "\n" | grep -v "/nix/store/.*/site-packages" | tr "\n" ":" | sed 's/:*$//')"
if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH
else
  unset PYTHONPATH
fi

poetry lock "$@"
