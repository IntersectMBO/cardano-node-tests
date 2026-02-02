#!/bin/bash

set -euo pipefail

_VENV_DIR="${_VENV_DIR:-"${WORKDIR:?}/.venv"}"

if [ "${1:-""}" = "clean" ]; then
  rm -rf "$_VENV_DIR"
fi

_REQS_INSTALLED="true"
if [ ! -e "$_VENV_DIR" ]; then
  _REQS_INSTALLED=""
  python3 -m venv "$_VENV_DIR" --prompt tests-venv
fi

# shellcheck disable=SC1090,SC1091
. "$_VENV_DIR/bin/activate"

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

if [ -z "$_REQS_INSTALLED" ]; then
  uv sync --active --no-dev
fi

unset _VENV_DIR _REQS_INSTALLED
