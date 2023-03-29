#!/usr/bin/env bash
#
# Run the rollback test.
#
# Can be configured using environment variables:
#  NODE_REV - the node revision to use. If not set, the latest master will be used.
#  NUM_POOLS - the number of pools to setup. If not set, 10 will be used.
#  ROLLBACK_NODES_OFFSET - difference in number of nodes in cluster 1 vs cluster 2. If not set, 1 will be used.
#
#  With default settings, there will be 6 block producing nodes in cluster 1 and 4 block producing nodes in cluster 2.

set -euo pipefail

TOP_DIR="$(readlink -m "${0%/*}/..")"

export NUM_POOLS="${NUM_POOLS:-"10"}"

export CLUSTERS_COUNT=1 TEST_THREADS=0 ROLLBACK_PAUSE=1 SCRIPTS_DIRNAME="mainnet_fast" PYTEST_ARGS="-s -k test_consensus_reached"

"$TOP_DIR/.github/regression.sh"
