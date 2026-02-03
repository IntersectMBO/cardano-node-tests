#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || {
  echo "This script must be sourced from bash." >&2
  return 1
}

_VENV_DIR="${_VENV_DIR:-"${WORKDIR:?}/.venv"}"
_TOP_DIR="$(readlink -m "${BASH_SOURCE[0]}/../..")"

if [ "${1:-""}" = "clean" ]; then
  rm -rf "$_VENV_DIR"
fi

_REQS_INSTALLED="true"
if [ ! -e "$_VENV_DIR" ]; then
  _REQS_INSTALLED=""
  python3 -m venv "$_VENV_DIR" --prompt tests-venv
fi

# shellcheck disable=SC1091
. "$_TOP_DIR/scripts/common.sh"

filter_out_nix

# shellcheck disable=SC1091
. "$_VENV_DIR/bin/activate"

if [ -z "$_REQS_INSTALLED" ]; then
  uv sync --active --no-dev
fi

unset _VENV_DIR _REQS_INSTALLED _TOP_DIR
