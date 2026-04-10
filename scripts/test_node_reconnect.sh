#!/usr/bin/env bash
#
# Run reconnect tests.
#
# You must set either TEST_RECONNECT or TEST_METRICS_RECONNECT to 1 to run.

set -euo pipefail

TOP_DIR="$(cd "${0%/*}/.." && pwd)" || { echo "Cannot determine top dir, exiting." >&2; exit 1; }

export \
  CLUSTERS_COUNT=1 \
  TEST_THREADS=0 \
  TESTNET_VARIANT="mainnet_fast" \
  PYTEST_ARGS="-s -k TestNodeReconnect"

"$TOP_DIR/runner/regression.sh"
