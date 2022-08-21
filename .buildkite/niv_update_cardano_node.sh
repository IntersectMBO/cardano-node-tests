#!/usr/bin/env bash

niv_update

# update cardano-node to specified branch and/or revision
if [ -n "${NODE_REV:-""}" ]; then
  niv_update cardano-node --rev "$NODE_REV"
elif [ -n "${NODE_BRANCH:-""}" ]; then
  niv_update cardano-node --branch "$NODE_BRANCH"
fi

cat nix/sources.json
