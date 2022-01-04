#!/usr/bin/env bash

# When running tests on testnet, it is necessary to do some cleanup afterwards:
# * deregister pools
# * deregister stake addresses
# * return funds from addresses created during testing back to faucet
#
# Usage:
# ./testnet_cleanup.sh /path/to/testing_artifacts
#
# The testing artifacts dir can be e.g. /tmp/pytest-of-user/pytest-1

set -euo pipefail

ARTIFACTS_DIR="${1:?"Need path to testing artifacts"}"
TOP_DIR="$(readlink -m "${0%/*}/..")"

# deregister pools
while read -r d; do
  if [ -e "$d/nodes" ] && [ ! -e "$d/nodes/dereg_success" ]; then
    "$TOP_DIR/cardano_node_tests/cluster_scripts/testnets/deregister-pools" "$d/nodes" || :
  fi
done <<< "$(find "$ARTIFACTS_DIR" -type d -name "state-cluster*")"

# return funds to faucet
"$TOP_DIR/cardano_node_tests/testnet_cleanup.py" -a "$ARTIFACTS_DIR"
