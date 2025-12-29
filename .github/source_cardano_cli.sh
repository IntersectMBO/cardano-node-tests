#!/bin/bash

# Build cardano-cli from the standalone repo into $WORKDIR/cardano-cli-build<postfix>
cardano_cli_build() {
  local cli_rev="${1:?}"
  local node_bindir_postfix="${2:-}"
  local origpwd="$PWD"

  if [ -z "$cli_rev" ]; then
    echo "The value for CARDANO_CLI_REV cannot be empty" >&2
    return 1
  fi

  cd "$WORKDIR" || return 1

  local out="cardano-cli-build${node_bindir_postfix}"
  nix build \
    --accept-flake-config \
    --no-write-lock-file \
    "github://github.com/IntersectMBO/cardano-cli?ref=${cli_rev}#cardano-cli" \
    -o "$out" || { cd "$origpwd" || true; return 1; }

  [ -e "${out}/bin/cardano-cli" ] || { cd "$origpwd" || true; return 1; }

  cd "$origpwd" || return 1
}

# Print PATH to prepend for the standalone cardano-cli build output.
cardano_cli_print_path_prepend() {
  local node_bindir_postfix="${1:-}"
  local origpwd="$PWD"

  cd "$WORKDIR" || return 1

  local out="cardano-cli-build${node_bindir_postfix}"
  local bin_dir
  bin_dir="$(readlink -m "${out}/bin")" || { cd "$origpwd" || true; return 1; }

  cd "$origpwd" || return 1
  echo "${bin_dir:+"${bin_dir}:"}"
}
