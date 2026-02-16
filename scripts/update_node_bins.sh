#!/usr/bin/env bash

# This script is a wrapper around build_and_link_node_bins.sh that provides a simpler interface
# for updating the cardano-node, cardano-cli, cardano-submit-api, and bech32 binaries from
# a given cardano-node repository.
# It assumes default locations for the build output and symlink directories.

set -euo pipefail

usage() { printf "Usage: %s /path/to/cardano-node-repo\n" "${0}"; }

if [ $# -ne 1 ]; then
  usage
  exit 2
fi
REPO_PATH="$(readlink -m "$1")"

TOP_DIR="$(readlink -m "${0%/*}/..")"
BUILD_OUTPUT_DIR="$REPO_PATH/.nix_tests_bins"
BIN_DIR="$TOP_DIR/.bin_node"

# Call the generic build and link script
"${0%/*}/build_and_link_node_bins.sh" "$REPO_PATH" "$BUILD_OUTPUT_DIR" "$BIN_DIR"
