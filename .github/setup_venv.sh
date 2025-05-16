#!/bin/bash

set -euo pipefail

_VENV_DIR="${_VENV_DIR:-"$WORKDIR/.venv"}"

if [ "${1:-""}" = "clean" ]; then
  rm -rf "$_VENV_DIR"
fi

_REQS_INSTALLED="true"
if [ ! -e "$_VENV_DIR" ]; then
  _REQS_INSTALLED=""
  python3 -m venv "$_VENV_DIR"
fi

# shellcheck disable=SC1090,SC1091
. "$_VENV_DIR/bin/activate"

PYTHONPATH="$(echo "$VIRTUAL_ENV"/lib/python3*/site-packages):$PYTHONPATH"
export PYTHONPATH

if [ -z "$_REQS_INSTALLED" ]; then
  poetry install -n
fi

unset _VENV_DIR _REQS_INSTALLED
