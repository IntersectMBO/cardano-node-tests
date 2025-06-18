#!/bin/bash

_origpwd="$PWD"
cd "$WORKDIR" || exit 1

if [ -z "${CARDANO_CLI_REV:-""}" ]; then
  echo "The value for CARDANO_CLI_REV cannot be empty" >&2
  exit 1
fi

# Build `cardano-cli`
nix build \
  --accept-flake-config \
  --no-write-lock-file \
  "github://github.com/IntersectMBO/cardano-cli?ref=${CARDANO_CLI_REV}#cardano-cli" \
  -o cardano-cli-build || exit 1
[ -e cardano-cli-build/bin/cardano-cli ] || exit 1

# Add `cardano-cli` to PATH_PREPEND
PATH_PREPEND="${PATH_PREPEND:+"${PATH_PREPEND}:"}$(readlink -m cardano-cli-build/bin)"
export PATH_PREPEND

cd "$_origpwd" || exit 1
unset _origpwd
