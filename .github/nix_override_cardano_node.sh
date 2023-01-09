#!/usr/bin/env bash

node_override() {
  # if argument is provided:
  if [ -n "${1:-""}" ]; then
    REF="/$1"
  # else use specified branch and/or revision:
  elif [ -n "${NODE_REV:-""}" ]; then
    REF="/$NODE_REV"
  else
    # otherwise update to latest from default branch
    REF=""
  fi
  echo --override-input cardano-node "github:input-output-hk/cardano-node$REF" --recreate-lock-file
}
