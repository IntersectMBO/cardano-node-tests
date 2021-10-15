#!/bin/sh

set -eu

if [ -n "${IN_NIX_SHELL:-""}" ]; then
  echo "This script is not supposed to run inside nix shell." >&2
  exit 1
fi
if [ ! -d "cardano_node_tests" ]; then
  echo "This script is supposed to run from the root of cardano-node-tests directory." >&2
  exit 1
fi
if [ -n "${VIRTUAL_ENV:-""}" ]; then
  echo "A python virtual env is already activated in this shell. To install cardano-node-tests, run \`make install\`." >&2
  exit 1
fi

if [ -e ".env" ]; then
  echo "The \`.env\` dir with python virtual env already exists, not creating new one."
else
  # create a python virtual env
  python3 -m venv .env
fi

# activate the virtual env
# shellcheck disable=SC1091
. .env/bin/activate

# install cardano-node-tests and its dependencies into the virtual env
make install
