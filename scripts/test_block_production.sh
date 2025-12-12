#!/usr/bin/env bash
#
# Run the block production test on environment where half of the nodes are using the `mem`
# and the other half are using the `disk` UTxO backend.

set -euo pipefail

TOP_DIR="$(readlink -m "${0%/*}/..")"

# The database file will be created if missing
if [ -z "${BLOCK_PRODUCTION_DB:-}" ]; then
  # shellcheck disable=SC2016
  echo 'Configure path to the database file by setting `BLOCK_PRODUCTION_DB`' >&2
  exit 1
fi
BLOCK_PRODUCTION_DB="$(readlink -m "$BLOCK_PRODUCTION_DB")"
export BLOCK_PRODUCTION_DB

# The number of pools to setup
export NUM_POOLS="${NUM_POOLS:-"10"}"
if [ $((NUM_POOLS % 2)) -ne 0 ]; then
  echo "NUM_POOLS needs to be even number" >&2
  exit 1
fi

# The number of epochs to produce blocks for
export BLOCK_PRODUCTION_EPOCHS="${BLOCK_PRODUCTION_EPOCHS:-"100"}"

export MIXED_UTXO_BACKENDS="mem disk" SESSION_TIMEOUT=10h CLUSTERS_COUNT=1 TEST_THREADS=0 PYTEST_ARGS="-k test_block_production"

"$TOP_DIR/.github/regression.sh"
