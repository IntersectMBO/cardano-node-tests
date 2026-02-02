#!/bin/sh

abort_install=0

set -eu

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Please install it first." >&2
  abort_install=1
fi
if [ ! -d "cardano_node_tests" ]; then
  echo "This script is supposed to run from the root of cardano-node-tests directory." >&2
  abort_install=1
fi
if [ "$abort_install" -eq 1 ]; then
  exit 10
fi

if [ -n "${IN_NIX_SHELL:-}" ]; then
  # Filter out nix python packages from PYTHONPATH.
  # This avoids conflicts between nix-installed packages and python virtual environment packages.
  PYTHONPATH="$(echo "${PYTHONPATH:-}" | tr ":" "\n" | grep -v "/nix/store/.*/site-packages" | tr "\n" ":" | sed 's/:*$//' || :)"
fi
if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH
else
  unset PYTHONPATH
fi

uv lock "$@"
