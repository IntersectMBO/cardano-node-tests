#!/usr/bin/env bash

set -euo pipefail

err() { printf "Error: %s\n" "$*" >&2; }
usage() { printf "Usage: %s /path/to/cardano-clusterlib-py\n" "${0}"; }

if [ $# -ne 1 ]; then
  usage
  exit 64
fi
REPO_PATH=$1

TOP_DIR="$(readlink -m "${0%/*}/..")"
cd "$TOP_DIR" >/dev/null

# Sanity checks
if [ -n "${IN_NIX_SHELL:-""}" ]; then
  err "This script is not supposed to run inside nix shell."
  exit 1
fi

# Activate poetry virtual environment
if [ -z "${VIRTUAL_ENV:-}" ]; then
  # shellcheck disable=SC2091
  $(poetry env activate)
  # Override PYTHONPATH to prefer virtual environment packages over nix packages
  PYTHONPATH="$(echo "$VIRTUAL_ENV"/lib/python3*/site-packages):${PYTHONPATH:-}"
  export PYTHONPATH
fi
if [ -z "${VIRTUAL_ENV:-}" ]; then
  err "Failed to activate virtual environment."
  exit 1
fi

# Double-check python is actually running inside a venv
if ! python - <<'PY'
import sys
raise SystemExit(0 if sys.prefix != getattr(sys, "base_prefix", sys.prefix) else 1)
PY
then
  err "Python indicates it's not running inside a virtual environment."
  exit 1
fi

# Check that cardano-clusterlib is installed
if ! python -m pip show cardano-clusterlib >/dev/null 2>&1; then
  err "Package 'cardano-clusterlib' is not installed in this environment."
  exit 1
fi

# Validate repo path
if [ ! -d "$REPO_PATH" ]; then
  err "Repo path not found: $REPO_PATH"
  exit 1
fi
if [[ ! -f "$REPO_PATH/pyproject.toml" && ! -f "$REPO_PATH/cardano_clusterlib" ]]; then
  err "Given path doesn't look like the cardano-clusterlib-py repo."
  exit 1
fi

echo "Uninstalling 'cardano-clusterlib' from current environment..."
python -m pip uninstall -y cardano-clusterlib

echo "Installing editable from: $REPO_PATH"
cd "$REPO_PATH" >/dev/null
python -m pip install -e . --config-settings editable_mode=compat

echo
echo "Verifying editable install (should point into your repo, not site-packages):"
cd "$TOP_DIR" >/dev/null
python -c 'from cardano_clusterlib import clusterlib_klass; print(clusterlib_klass.__file__)'
