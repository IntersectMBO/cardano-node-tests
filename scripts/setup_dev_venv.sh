#!/bin/sh
#
# Install cardano_node_tests and its dependencies into a virtual environment.

abort_install=0

set -eu

filter_out_nix(){
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
}

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Please install it first." >&2
  abort_install=1
fi
if [ ! -d "cardano_node_tests" ]; then
  echo "This script is supposed to run from the root of cardano-node-tests directory." >&2
  abort_install=1
fi

if [ -n "${VIRTUAL_ENV:-""}" ]; then
  if [ "$abort_install" -eq 1 ]; then
    exit 1
  fi

  echo "A python virtual env is already activated in this shell."
  echo "Install into the current virtual env? [y/N]"
  read -r answer
  if [ "$answer" != "y" ]; then
    exit 1
  fi

  filter_out_nix
  uv sync --active --group docs --group dev
  exit 0
fi

if [ "$abort_install" -eq 1 ]; then
  exit 1
fi

filter_out_nix
uv sync --group docs --group dev

# shellcheck disable=SC2016
echo 'Run `source .venv/bin/activate` to activate the virtual env.'
