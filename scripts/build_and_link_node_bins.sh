#!/usr/bin/env bash

# This script builds the cardano-node, cardano-cli, cardano-submit-api, bech32 and
# tx-generator binaries from a given cardano-node repository using Nix, and then creates
# symlinks to these binaries in a specified directory. When ENABLE_TX_CENTRIFUGE is set,
# tx-centrifuge is built and linked too (only available on cardano-node branches that
# ship it, e.g. bench/leios-11.0.1).
#
# Example usage:
#  ./scripts/build_and_link_node_bins.sh ../cardano-node ../cardano-node/.nix_tests_bins_10.6.2 bin_10.6.2

set -euo pipefail

# shellcheck disable=SC1091
. "$(dirname "$0")/common.sh"

err() { printf "Error: %s\n" "$*" >&2; }
usage() {
  printf "Usage: %s /path/to/cardano-node-repo /path/to/build-output-dir /path/to/bin-dir\n" "${0}"
  printf "  repo-path:         Path to the cardano-node repository\n"
  printf "  build-output-dir:  Directory where Nix build outputs will be stored\n"
  printf "  bin-dir:           Directory where symlinks to binaries will be created\n"
}

if [ $# -ne 3 ]; then
  usage >&2
  exit 2
fi

repo_path="$(readlink -m "$1")"
build_output_dir="$(readlink -m "$2")"
bin_dir="$(readlink -m "$3")"

# Validate repo path
if [ ! -d "$repo_path" ]; then
  err "Repo path not found: $repo_path"
  exit 1
fi
if [[ ! -d "$repo_path/cardano-node" && ! -d "$repo_path/cardano-cli" ]]; then
  err "Given path doesn't look like the cardano-node repo."
  exit 1
fi

# Build binaries
cd "$repo_path" >/dev/null
mkdir -p "$build_output_dir"

echo "👉  Building node binaries in repo: $repo_path"
nix build .#cardano-node -o "$build_output_dir/node"
echo "👉  Building cli binaries in repo: $repo_path"
nix build .#cardano-cli -o "$build_output_dir/cli"
echo "👉  Building submit-api binaries in repo: $repo_path"
nix build .#cardano-submit-api -o "$build_output_dir/submit-api"
echo "👉  Building bech32 binaries in repo: $repo_path"
nix build .#bech32 -o "$build_output_dir/bech32"
echo "👉  Building tx-generator binaries in repo: $repo_path"
nix build .#tx-generator -o "$build_output_dir/tx-generator"
if is_truthy "${ENABLE_TX_CENTRIFUGE:-}"; then
  echo "👉  Building tx-centrifuge binaries in repo: $repo_path"
  nix build .#tx-centrifuge -o "$build_output_dir/tx-centrifuge"
fi

# Create symlinks
echo "👉  Updating symlinks to built binaries in: $bin_dir"
mkdir -p "$bin_dir"

rm -f "$bin_dir"/cardano-node
rm -f "$bin_dir"/cardano-cli
rm -f "$bin_dir"/cardano-submit-api
rm -f "$bin_dir"/bech32
rm -f "$bin_dir"/tx-generator
rm -f "$bin_dir"/tx-centrifuge
ln -s "$build_output_dir/node/bin/cardano-node" "$bin_dir/cardano-node"
ln -s "$build_output_dir/cli/bin/cardano-cli" "$bin_dir/cardano-cli"
ln -s "$build_output_dir/submit-api/bin/cardano-submit-api" "$bin_dir/cardano-submit-api"
ln -s "$build_output_dir/bech32/bin/bech32" "$bin_dir/bech32"
ln -s "$build_output_dir/tx-generator/bin/tx-generator" "$bin_dir/tx-generator"
if is_truthy "${ENABLE_TX_CENTRIFUGE:-}"; then
  ln -s "$build_output_dir/tx-centrifuge/bin/tx-centrifuge" "$bin_dir/tx-centrifuge"
fi
