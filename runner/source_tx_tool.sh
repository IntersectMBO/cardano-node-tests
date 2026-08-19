#!/bin/bash

# Build standalone transaction tools (tx-centrifuge, tx-firehose, ...) from the
# cardano-node repo into $WORKDIR/<tool>-build.
# These tools live on different cardano-node refs than the one used for NODE_REV,
# so each is built from its own revision, passed in as the second argument.
tx_tool_build() {
  : "${WORKDIR:?"WORKDIR must be set to a writable directory"}"
  local tool="${1:?"Missing tool name"}"
  local tool_rev="${2-}"
  local origpwd="$PWD"

  if [ -z "$tool_rev" ]; then
    echo "The revision for '$tool' cannot be empty" >&2
    return 1
  fi

  cd "$WORKDIR" || return 1

  local out="${tool}-build"
  nix build \
    --accept-flake-config \
    --no-write-lock-file \
    "github:IntersectMBO/cardano-node/${tool_rev}#${tool}" \
    -o "$out" || { cd "$origpwd" || true; return 1; }

  [ -e "${out}/bin/${tool}" ] || { cd "$origpwd" || true; return 1; }

  cd "$origpwd" || return 1
}

# Print the bin dir to prepend to PATH for a standalone tx tool build output.
tx_tool_print_path_prepend() {
  : "${WORKDIR:?"WORKDIR must be set to a writable directory"}"
  local tool="${1:?"Missing tool name"}"
  local origpwd="$PWD"

  cd "$WORKDIR" || return 1

  local out="${tool}-build"
  local bin_dir
  bin_dir="$(readlink -f "${out}/bin")" || { cd "$origpwd" || true; return 1; }

  cd "$origpwd" || return 1
  echo "$bin_dir"
}
