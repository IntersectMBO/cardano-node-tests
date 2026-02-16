#!/usr/bin/env bash

# This script builds the cardano-node, cardano-cli, cardano-submit-api, and bech32 binaries
# from a given cardano-node repository using Nix, and then creates symlinks to these binaries
# in a specified directory.
#
# Example usage:
#  ./scripts/build_and_link_node_bins.sh ../cardano-node ../cardano-node/.nix_tests_bins_10.6.2 bin_10.6.2

set -euo pipefail

err() { printf "Error: %s\n" "$*" >&2; }
usage() {
  printf "Usage: %s /path/to/cardano-node-repo /path/to/build-output-dir /path/to/bin-dir\n" "${0}"
  printf "  repo-path:         Path to the cardano-node repository\n"
  printf "  build-output-dir:  Directory where Nix build outputs will be stored\n"
  printf "  bin-dir:           Directory where symlinks to binaries will be created\n"
}

if [ $# -ne 3 ]; then
  usage
  exit 2
fi

REPO_PATH="$(readlink -m "$1")"
BUILD_OUTPUT_DIR="$(readlink -m "$2")"
BIN_DIR="$(readlink -m "$3")"

# Validate repo path
if [ ! -d "$REPO_PATH" ]; then
  err "Repo path not found: $REPO_PATH"
  exit 1
fi
if [[ ! -d "$REPO_PATH/cardano-node" && ! -d "$REPO_PATH/cardano-cli" ]]; then
  err "Given path doesn't look like the cardano-node repo."
  exit 1
fi

# Build binaries
cd "$REPO_PATH" >/dev/null
mkdir -p "$BUILD_OUTPUT_DIR"

echo "👉  Building node binaries in repo: $REPO_PATH"
nix build .#cardano-node -o "$BUILD_OUTPUT_DIR/node"
echo "👉  Building cli binaries in repo: $REPO_PATH"
nix build .#cardano-cli -o "$BUILD_OUTPUT_DIR/cli"
echo "👉  Building submit-api binaries in repo: $REPO_PATH"
nix build .#cardano-submit-api -o "$BUILD_OUTPUT_DIR/submit-api"
echo "👉  Building bech32 binaries in repo: $REPO_PATH"
nix build .#bech32 -o "$BUILD_OUTPUT_DIR/bech32"

# Create symlinks
echo "👉  Updating symlinks to built binaries in: $BIN_DIR"
mkdir -p "$BIN_DIR"

rm -f "$BIN_DIR"/cardano-node
rm -f "$BIN_DIR"/cardano-cli
rm -f "$BIN_DIR"/cardano-submit-api
rm -f "$BIN_DIR"/bech32
ln -s "$BUILD_OUTPUT_DIR/node/bin/cardano-node" "$BIN_DIR/cardano-node"
ln -s "$BUILD_OUTPUT_DIR/cli/bin/cardano-cli" "$BIN_DIR/cardano-cli"
ln -s "$BUILD_OUTPUT_DIR/submit-api/bin/cardano-submit-api" "$BIN_DIR/cardano-submit-api"
ln -s "$BUILD_OUTPUT_DIR/bech32/bin/bech32" "$BIN_DIR/bech32"
