#!/usr/bin/env bash

# Launcher for a regression run on the experimental Dijkstra era with the
# experimental Leios feature.
#
# The test setup lives in `runner/env_leios`, which also deselects the tests
# that are known to fail in this setup (see `scripts/deselected_leios_tests.txt`).
#
# Any variable from the env file can be overridden on the command line:
#   ./scripts/test_leios.sh TX_TPS=30
#
# Use `DESELECT_FROM_FILE=` to run without deselecting the known failures, or
# point it to a different file to use your own list.

set -Eeuo pipefail

top_dir="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine top dir, exiting." >&2; exit 1; }
cd "$top_dir"

usage() {
  cat <<EOF
Usage: $0 [VAR=VALUE ...]

Any variable from 'runner/env_leios' can be overridden, e.g. TX_TPS=30.
Use 'DESELECT_FROM_FILE=' to run also the tests that are known to fail here.
EOF
}

# Only `VAR=VALUE` overrides are accepted; anything else would be treated as the
# command to run by `load-gh-env.sh`.
for arg in "$@"; do
  if [[ "$arg" == "-h" || "$arg" == "--help" ]]; then
    usage
    exit 0
  fi
  if [[ ! "$arg" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
    echo "Error: '$arg' is not a VAR=VALUE override." >&2
    usage >&2
    exit 2
  fi
done

# Not part of `runner/env_leios`, because in CI these are workflow inputs. Unlike
# the values in the env file, an already exported value wins here, so the node
# branch can be switched without touching the setup.
export NODE_REV="${NODE_REV:-leios-prototype}"
export MARKEXPR="${MARKEXPR:-testnets}"
export ALLOW_UNSTABLE_ERROR_MESSAGES="${ALLOW_UNSTABLE_ERROR_MESSAGES:-true}"

exec runner/load-gh-env.sh runner/env_leios "$@" -- runner/regression.sh
