#!/bin/sh

abort_install=0

set -eu

if [ -n "${IN_NIX_SHELL:-""}" ]; then
  echo "This script is not supposed to run inside nix shell." >&2
  abort_install=1
fi
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

poetry lock "$@"
