#!/bin/bash

echo "::group::plutus-apps setup"

pushd "$WORKDIR" || exit 1

case "${PLUTUS_APPS_REV:-""}" in
  "" )
    echo "The value for PLUTUS_APPS_REV cannot be empty" >&2
    exit 1
    ;;

  "main" | "HEAD" )
    export PLUTUS_APPS_REV="main"

    if [ ! -e plutus-apps ]; then
      git clone --depth 1 --recurse-submodules --shallow-submodules https://github.com/IntersectMBO/plutus-apps.git
    fi

    pushd plutus-apps || exit 1
    git fetch origin main
    ;;

  * )
    if [ ! -e plutus-apps ]; then
      git clone --recurse-submodules https://github.com/IntersectMBO/plutus-apps.git
    fi

    pushd plutus-apps || exit 1
    git fetch
    ;;
esac

git checkout "$PLUTUS_APPS_REV"
git rev-parse HEAD

# Build `create-script-context`
nix build --accept-flake-config .#create-script-context -o create-script-context-build || exit 1
[ -e create-script-context-build/bin/create-script-context ] || exit 1

# Add `create-script-context` to PATH
PATH="$(readlink -m create-script-context-build/bin)":"$PATH"
export PATH

pushd "$REPODIR" || exit 1

echo "::endgroup::"
