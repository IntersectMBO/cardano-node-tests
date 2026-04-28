#!/usr/bin/env bash

set -euo pipefail

err() { printf "Error: %s\n" "$*" >&2; }
usage() { printf "Usage: %s /path/to/package_root\n" "${0}"; }

if [ $# -ne 1 ]; then
  usage >&2
  exit 2
fi
REPO_PATH="$(readlink -f "$1")"

TOP_DIR="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine top dir, exiting." >&2; exit 1; }
cd "$TOP_DIR"

# shellcheck disable=SC1091
. "$TOP_DIR/scripts/common.sh"

# Activate python virtual environment
if ! is_venv_active; then
  if [ ! -e "$TOP_DIR/.venv/bin/activate" ]; then
    err "Virtual environment not found at: $TOP_DIR/.venv"
    exit 1
  fi
  # shellcheck disable=SC1091
  . "$TOP_DIR/.venv/bin/activate"
fi

# check that correct virtual env is activated
assert_correct_venv "$TOP_DIR"

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
if [ ! -d "$REPO_PATH" ]; then
  err "Repo path not found: $REPO_PATH"
  exit 1
fi
if [[ ! -f "$REPO_PATH/pyproject.toml" && ! -f "$REPO_PATH/setup.cfg" && ! -f "$REPO_PATH/setup.py" ]]; then
  err "Given path doesn't look like python package."
  exit 1
fi

echo "Installing editable from: $REPO_PATH"
cd "$REPO_PATH"
uv pip install -e . --config-setting editable_mode=compat
