#!/usr/bin/env bash

# update cardano-node to specified branch and/or revision, or to the latest available
if [ -n "${NODE_REV:-""}" ]; then
  niv update cardano-node --rev "$NODE_REV"
elif [ -n "${NODE_BRANCH:-""}" ]; then
  niv update cardano-node --branch "$NODE_BRANCH"
else
  niv update cardano-node
fi

cat nix/sources.json
