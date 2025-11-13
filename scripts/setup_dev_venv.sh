#!/bin/sh
#
# Install cardano_node_tests and its dependencies into a virtual environment.

PYTHON_VERSION="3.11"

abort_install=0

set -eu

filter_out_nix(){
  # Filter out nix python packages from PYTHONPATH.
  # This avoids conflicts between nix-installed packages and poetry virtual environment packages.
  PYTHONPATH="$(echo "${PYTHONPATH:-}" | tr ":" "\n" | grep -v "/nix/store/.*/site-packages" | tr "\n" ":" | sed 's/:*$//' || :)"
  if [ -n "${PYTHONPATH:-}" ]; then
    export PYTHONPATH
  else
    unset PYTHONPATH
  fi
}

if ! command -v poetry >/dev/null 2>&1; then
  echo "Poetry is not installed. Please install it first." >&2
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
  POETRY_VIRTUALENVS_PATH="$PWD" poetry install --with docs --with dev
  exit 0
fi

# use the same python version as in nix shell
if ! command -v "python$PYTHON_VERSION" >/dev/null 2>&1; then
  echo "Python $PYTHON_VERSION is not installed. Please install it first." >&2
  abort_install=1
fi

if [ "$abort_install" -eq 1 ]; then
  exit 1
fi

filter_out_nix
poetry env use "python$PYTHON_VERSION"
poetry install --with docs --with dev

# shellcheck disable=SC2016
echo 'Run \`source "$(poetry env info --path)"/bin/activate\` to activate the virtual env.'
