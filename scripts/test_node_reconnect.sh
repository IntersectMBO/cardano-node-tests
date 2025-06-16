#!/usr/bin/env bash
#
# Run reconnect tests.
#
# You must set either TEST_RECONNECT or TEST_METRICS_RECONNECT to 1 to run.

set -euo pipefail

TOP_DIR="$(readlink -m "${0%/*}/..")"

export \
  CLUSTERS_COUNT=1 \
  TEST_THREADS=0 \
  TESTNET_VARIANT="mainnet_fast" \
  PYTEST_ARGS="-s -k TestNodeReconnect"

"$TOP_DIR/.github/regression.sh"
