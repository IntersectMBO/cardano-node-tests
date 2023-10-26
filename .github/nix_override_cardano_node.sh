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

  #TODO: remove this workaround as soon as https://github.com/nix-community/poetry2nix/pull/1368 is merged
  poetry2nix_ref=("--override-input" "poetry2nix" "github:nix-community/poetry2nix/026032a8b4e6e948c608de0fc559c6d0256e6117")

  echo --override-input cardano-node "github:input-output-hk/cardano-node$ref" "${poetry2nix_ref[@]}" --recreate-lock-file
}
