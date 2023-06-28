#!/bin/bash

echo "::group::plutus-apps setup"

pushd "$WORKDIR" || exit 1

# Clone plutus-apps if needed
if [ ! -e plutus-apps ]; then
  git clone --depth 1 --recurse-submodules --shallow-submodules https://github.com/input-output-hk/plutus-apps.git
fi

pushd plutus-apps || exit 1
git pull origin main
git rev-parse HEAD

# Build `create-script-context`
nix build --accept-flake-config .#create-script-context -o create-script-context-build || exit 1
[ -e create-script-context-build/bin/create-script-context ] || exit 1

# Add `create-script-context` to PATH
PATH="$(readlink -m create-script-context-build/bin)":"$PATH"
export PATH

pushd "$REPODIR" || exit 1

echo "::endgroup::"
