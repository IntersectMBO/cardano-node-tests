#!/usr/bin/env bash

set -euo pipefail

err() { printf "Error: %s\n" "$*" >&2; }
usage() { printf "Usage: %s /path/to/package_root\n" "${0}"; }

if [ $# -ne 1 ]; then
  usage >&2
  exit 2
fi
repo_path="$(readlink -f "$1")"

top_dir="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine top dir, exiting." >&2; exit 1; }
cd "$top_dir"

# shellcheck disable=SC1091
. "$top_dir/scripts/common.sh"

# Activate python virtual environment
if ! is_venv_active; then
  if [ ! -e "$top_dir/.venv/bin/activate" ]; then
    err "Virtual environment not found at: $top_dir/.venv"
    exit 1
  fi
  # shellcheck disable=SC1091
  . "$top_dir/.venv/bin/activate"
fi

# check that correct virtual env is activated
assert_correct_venv "$top_dir"

filter_out_nix

# Double-check python is actually running inside a venv
if ! python - <<'PY'
import sys
raise SystemExit(0 if sys.prefix != getattr(sys, "base_prefix", sys.prefix) else 1)
PY
then
  err "Python indicates it's not running inside a virtual environment."
  exit 1
fi

# Validate repo path
if [ ! -d "$repo_path" ]; then
  err "Repo path not found: $repo_path"
  exit 1
fi
if [[ ! -f "$repo_path/pyproject.toml" && ! -f "$repo_path/setup.cfg" && ! -f "$repo_path/setup.py" ]]; then
  err "Given path doesn't look like python package."
  exit 1
fi

echo "Installing editable from: $repo_path"
cd "$repo_path"
uv pip install -e . --config-setting editable_mode=compat
