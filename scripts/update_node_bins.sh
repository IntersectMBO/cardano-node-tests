#!/usr/bin/env bash

# This script is a wrapper around build_and_link_node_bins.sh that provides a simpler interface
# for updating the cardano-node, cardano-cli, cardano-submit-api, and bech32 binaries from
# a given cardano-node repository.
# It assumes default locations for the build output and symlink directories.

set -euo pipefail

usage() { printf "Usage: %s /path/to/cardano-node-repo\n" "${0}"; }

if [ $# -ne 1 ]; then
  usage >&2
  exit 2
fi
repo_path="$(readlink -f "$1")"

top_dir="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine top dir, exiting." >&2; exit 1; }
build_output_dir="$repo_path/.nix_tests_bins"
bin_dir="$top_dir/.bin_node"

# Call the generic build and link script
"${0%/*}/build_and_link_node_bins.sh" "$repo_path" "$build_output_dir" "$bin_dir"
