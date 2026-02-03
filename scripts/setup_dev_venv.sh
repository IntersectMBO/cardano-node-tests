#!/usr/bin/env bash
#
# Install cardano_node_tests and its dependencies into a virtual environment.

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

if [ -n "${VIRTUAL_ENV:-""}" ]; then
  echo "A python virtual env is already activated in this shell."
  read -r -p "Install into the current virtual env? [y/N] " answer
  if ! is_truthy "$answer"; then
    echo "Aborting." >&2
    exit 1
  fi

  uv sync --active --group docs --group dev
  exit 0
fi

uv sync --group docs --group dev

# shellcheck disable=SC2016
echo 'Run `source .venv/bin/activate` to activate the virtual env.'
