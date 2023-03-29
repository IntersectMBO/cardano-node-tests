#!/usr/bin/env bash
#
# Run the rollback test.
#
# Can be configured using environment variables:
#  NODE_REV - the node revision to use. If not set, the latest master will be used.
#  NUM_POOLS - the number of pools to setup. If not set, 10 will be used.

set -euo pipefail

TOP_DIR="$(readlink -m "${0%/*}/..")"

# Node revision to use. If not set, the latest master will be used.
export NODE_REV="${NODE_REV:-""}"

# The number of pools to setup
export NUM_POOLS="${NUM_POOLS:-"10"}"
if [ $((NUM_POOLS % 2)) -ne 0 ]; then
  echo "NUM_POOLS needs to be even number" >&2
  exit 1
fi

export CLUSTERS_COUNT=1 TEST_THREADS=0 ROLLBACK_PAUSE=1 SCRIPTS_DIRNAME="mainnet_fast" PYTEST_ARGS="-s -k test_consensus_reached"

"$TOP_DIR/.github/regression.sh"
