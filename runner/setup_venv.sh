#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || {
  echo "This script must be sourced from bash." >&2
  return 1
}

_VENV_DIR="${_VENV_DIR:-"${WORKDIR:?}/.venv"}"
_top_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || { echo "Cannot determine top dir, returning." >&2; return 1; }

if [ "${1:-""}" = "clean" ]; then
  rm -rf "$_VENV_DIR"
fi

_reqs_installed="true"
if [ ! -e "$_VENV_DIR" ]; then
  _reqs_installed=""
  python3 -m venv "$_VENV_DIR" --prompt tests-venv
fi

# shellcheck disable=SC1091
. "$_top_dir/scripts/common.sh"

filter_out_nix

# shellcheck disable=SC1091
. "$_VENV_DIR/bin/activate"

if [ -z "$_reqs_installed" ]; then
  uv sync --active --no-dev
fi

unset _VENV_DIR _reqs_installed _top_dir
