#!/usr/bin/env bash
#
# Run reconnect tests.

set -euo pipefail

TOP_DIR="$(readlink -m "${0%/*}/..")"

export \
  TEST_RECONNECT=1 \
  CLUSTERS_COUNT=1 \
  TEST_THREADS=0 \
  ENABLE_P2P=1 \
  SCRIPTS_DIRNAME="${SCRIPTS_DIRNAME:-mainnet_fast}" \
  PYTEST_ARGS="-s -k TestNodeReconnect"

"$TOP_DIR/.github/regression.sh"
