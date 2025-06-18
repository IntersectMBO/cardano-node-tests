#!/bin/bash

_origpwd="$PWD"
cd "$WORKDIR" || exit 1

if [ -z "${PLUTUS_APPS_REV:-""}" ]; then
  echo "The value for PLUTUS_APPS_REV cannot be empty" >&2
  exit 1
fi

# Build `create-script-context`
nix build \
  --accept-flake-config \
  --no-write-lock-file \
  "github://github.com/IntersectMBO/plutus-apps?ref=${PLUTUS_APPS_REV}#create-script-context" \
  -o create-script-context-build || exit 1
[ -e create-script-context-build/bin/create-script-context ] || exit 1

# Add `create-script-context` to PATH_PREPEND
PATH_PREPEND="${PATH_PREPEND:+"${PATH_PREPEND}:"}$(readlink -m create-script-context-build/bin)"
export PATH_PREPEND

cd "$_origpwd" || exit 1
unset _origpwd
