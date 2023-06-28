#!/bin/bash

echo "::group::cardano-cli setup"

pushd "$WORKDIR" || exit 1

# Clone cardano-cli if needed
if [ ! -e cardano-cli ]; then
  git clone --depth 1 --recurse-submodules --shallow-submodules https://github.com/input-output-hk/cardano-cli.git
fi

pushd cardano-cli || exit 1
git pull origin main
git rev-parse HEAD

# Build `cardano-cli`
nix build --accept-flake-config .#cardano-cli -o cardano-cli-build

pushd "$REPODIR" || exit 1

echo "::endgroup::"
