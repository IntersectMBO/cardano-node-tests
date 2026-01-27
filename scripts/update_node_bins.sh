#!/usr/bin/env bash

set -euo pipefail

err() { printf "Error: %s\n" "$*" >&2; }
usage() { printf "Usage: %s /path/to/cardano-node-repo\n" "${0}"; }

if [ $# -ne 1 ]; then
  usage
  exit 64
fi
REPO_PATH="$(readlink -m "$1")"

# Validate repo path
if [ ! -d "$REPO_PATH" ]; then
  err "Repo path not found: $REPO_PATH"
  exit 1
fi
if [[ ! -d "$REPO_PATH/cardano-node" && ! -d "$REPO_PATH/cardano-cli" ]]; then
  err "Given path doesn't look like the cardano-node repo."
  exit 1
fi

TOP_DIR="$(readlink -m "${0%/*}/..")"

cd "$REPO_PATH" >/dev/null
echo "👉  Building node binaries in repo: $REPO_PATH"
nix build .#cardano-node -o .nix_tests_bins/node
echo "👉  Building cli binaries in repo: $REPO_PATH"
nix build .#cardano-cli -o .nix_tests_bins/cli
echo "👉  Building submit-api binaries in repo: $REPO_PATH"
nix build .#cardano-submit-api -o .nix_tests_bins/submit-api
echo "👉  Building bech32 binaries in repo: $REPO_PATH"
nix build .#bech32 -o .nix_tests_bins/bech32

cd "$TOP_DIR" >/dev/null
BIN_DIR="$TOP_DIR/.bin_node"
echo "👉  Updating symlinks to built binaries in test repo: $BIN_DIR"
mkdir -p "$BIN_DIR"

rm -f "$BIN_DIR"/cardano-node
rm -f "$BIN_DIR"/cardano-cli
rm -f "$BIN_DIR"/cardano-submit-api
rm -f "$BIN_DIR"/bech32
ln -s "$REPO_PATH/.nix_tests_bins/node/bin/cardano-node" "$BIN_DIR/cardano-node"
ln -s "$REPO_PATH/.nix_tests_bins/cli/bin/cardano-cli" "$BIN_DIR/cardano-cli"
ln -s "$REPO_PATH/.nix_tests_bins/submit-api/bin/cardano-submit-api" "$BIN_DIR/cardano-submit-api"
ln -s "$REPO_PATH/.nix_tests_bins/bech32/bin/bech32" "$BIN_DIR/bech32"
