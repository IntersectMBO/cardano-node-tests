#!/bin/bash

echo "::group::cardano-cli setup"

pushd "$WORKDIR" || exit 1

case "${CARDANO_CLI_REV:-""}" in
  "" )
    echo "The value for CARDANO_CLI_REV cannot be empty" >&2
    exit 1
    ;;

  "main" | "HEAD" )
    export CARDANO_CLI_REV="main"

    if [ ! -e cardano-cli ]; then
      git clone --depth 1 https://github.com/input-output-hk/cardano-cli.git
    fi

    pushd cardano-cli || exit 1
    git fetch origin main
    ;;

  * )
    if [ ! -e cardano-cli ]; then
      git clone https://github.com/input-output-hk/cardano-cli.git
    fi

    pushd cardano-cli || exit 1
    git fetch
    ;;
esac

git checkout "$CARDANO_CLI_REV"
git rev-parse HEAD

# Build `cardano-cli`
nix build --accept-flake-config .#cardano-cli -o cardano-cli-build || exit 1
[ -e cardano-cli-build/bin/cardano-cli ] || exit 1

pushd "$REPODIR" || exit 1

echo "::endgroup::"
