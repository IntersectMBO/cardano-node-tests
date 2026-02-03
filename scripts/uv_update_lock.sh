#!/usr/bin/env bash

set -euo pipefail

abort_install=0

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Please install it first." >&2
  abort_install=1
fi
if [ ! -d "cardano_node_tests" ]; then
  echo "This script is supposed to run from the root of cardano-node-tests repository." >&2
  abort_install=1
fi
if [ "$abort_install" -eq 1 ]; then
  exit 1
fi

# shellcheck disable=SC1091
. scripts/common.sh

filter_out_nix

uv lock "$@"
