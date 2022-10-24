#!/usr/bin/env bash

node_override() {
  # If argument is provided:
  if [ -n "${1:-""}" ]; then
    REF="/$1"
  # else use specified branch and/or revision:
  elif [ -n "${NODE_REV:-""}" ]; then
    REF="/$NODE_REV"
  elif [ -n "${NODE_BRANCH:-""}" ]; then
    REF="/$NODE_BRANCH"
  else
    #otherwise update to latest from default branch.
    REF=""
  fi
  echo --override-input cardano-node "github:input-output-hk/cardano-node$REF" --recreate-lock-file
}
