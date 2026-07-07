#!/bin/bash

# Build tx-centrifuge from the cardano-node repo into $WORKDIR/tx-centrifuge-build<postfix>.
# tx-centrifuge lives on a different cardano-node ref than the one used for NODE_REV,
# so it is built from its own revision (default: the bench/leios-11.0.1 branch).
tx_centrifuge_build() {
  : "${WORKDIR:?"WORKDIR must be set to a writable directory"}"
  local centrifuge_rev="${1:?}"
  local node_bindir_postfix="${2:-}"
  local origpwd="$PWD"

  if [ -z "$centrifuge_rev" ]; then
    echo "The value for TX_CENTRIFUGE_REV cannot be empty" >&2
    return 1
  fi

  cd "$WORKDIR" || return 1

  local out="tx-centrifuge-build${node_bindir_postfix}"
  nix build \
    --accept-flake-config \
    --no-write-lock-file \
    "github://github.com/IntersectMBO/cardano-node?ref=${centrifuge_rev}#tx-centrifuge" \
    -o "$out" || { cd "$origpwd" || true; return 1; }

  [ -e "${out}/bin/tx-centrifuge" ] || { cd "$origpwd" || true; return 1; }

  cd "$origpwd" || return 1
}

# Print PATH to prepend for the standalone tx-centrifuge build output.
tx_centrifuge_print_path_prepend() {
  : "${WORKDIR:?"WORKDIR must be set to a writable directory"}"
  local node_bindir_postfix="${1:-}"
  local origpwd="$PWD"

  cd "$WORKDIR" || return 1

  local out="tx-centrifuge-build${node_bindir_postfix}"
  local bin_dir
  bin_dir="$(readlink -f "${out}/bin")" || { cd "$origpwd" || true; return 1; }

  cd "$origpwd" || return 1
  echo "${bin_dir:+"${bin_dir}:"}"
}
