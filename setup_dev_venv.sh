#!/bin/sh
#
# Install cardano_node_tests and its dependencies into a virtual environment.

PYTHON_VERSION="3.10"

# TODO: for pylint and mypy, see https://github.com/PyCQA/pylint/issues/7306
export SETUPTOOLS_ENABLE_FEATURES="legacy-editable"

abort_install=0

set -eu

if [ -n "${IN_NIX_SHELL:-""}" ]; then
  echo "This script is not supposed to run inside nix shell." >&2
  abort_install=1
fi
if ! type poetry >/dev/null 2>&1; then
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

  poetry install --with docs --with dev
  exit 0
fi

# use the same python version as in nix shell
if ! type "python$PYTHON_VERSION" >/dev/null 2>&1; then
  echo "Python $PYTHON_VERSION is not installed. Please install it first." >&2
  abort_install=1
fi

if [ "$abort_install" -eq 1 ]; then
  exit 1
fi

poetry env use "python$PYTHON_VERSION"
poetry install --with docs --with dev

echo "Run \`poetry shell\` to activate the virtual env."
