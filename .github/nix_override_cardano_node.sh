#!/usr/bin/env bash

node_override() {
  local ref
  # if argument is provided:
  if [ -n "${1:-""}" ]; then
    ref="/$1"
  # else use specified branch and/or revision:
  elif [ -n "${NODE_REV:-""}" ]; then
    ref="/$NODE_REV"
  else
    # otherwise update to latest from default branch
    ref=""
  fi
  echo --override-input cardano-node "github:input-output-hk/cardano-node$ref" --recreate-lock-file
}
