#!/bin/bash

VENV_DIR="${VENV_DIR:-"$WORKDIR/.env"}"

if [ "${1:-""}" = "clean" ]; then
  rm -rf "$VENV_DIR"
fi

python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1090,SC1091
. "$VENV_DIR/bin/activate"

PYTHONPATH="$(echo "$VIRTUAL_ENV"/lib/python3*/site-packages):$PYTHONPATH"
export PYTHONPATH

pip install -r requirements_freeze.txt
