#!/bin/bash

# Build all required binaries into $WORKDIR/*-build<postfix>
cardano_bins_build_all() {
  local node_rev="${1:?}"
  local cli_rev="${2:-}"
  local node_bindir_postfix="${3:-}"
  local origpwd="$PWD"

  if [ -z "$node_rev" ]; then
    echo "The value for NODE_REV cannot be empty" >&2
    return 1
  fi

  cd "$WORKDIR" || return 1

  _cardano_bins_build_one() {
    local flake_attr="$1"   # e.g. cardano-node
    local exe="$2"          # e.g. cardano-node
    local out="${flake_attr}-build${node_bindir_postfix}"

    nix build \
      --accept-flake-config \
      --no-write-lock-file \
      "github://github.com/IntersectMBO/cardano-node?ref=${node_rev}#${flake_attr}" \
      -o "$out" || return 1

    [ -e "${out}/bin/${exe}" ] || return 1
  }

  _cardano_bins_build_one "cardano-node" "cardano-node" || { cd "$origpwd" || true; return 1; }
  _cardano_bins_build_one "cardano-submit-api" "cardano-submit-api" || { cd "$origpwd" || true; return 1; }
  _cardano_bins_build_one "bech32" "bech32" || { cd "$origpwd" || true; return 1; }

  if [ -z "$cli_rev" ]; then
    _cardano_bins_build_one "cardano-cli" "cardano-cli" || { cd "$origpwd" || true; return 1; }
  fi

  cd "$origpwd" || return 1
}

# Print PATH to prepend based on previously built outputs
cardano_bins_print_path_prepend() {
  local cli_rev="${1:-}"
  local node_bindir_postfix="${2:-}"
  local origpwd="$PWD"

  cd "$WORKDIR" || return 1

  local node_path_prepend=""

  _cardano_bins_add_bin_dir() {
    local out_prefix="$1"
    local out="${out_prefix}-build${node_bindir_postfix}"
    local bin_dir

    bin_dir="$(readlink -m "${out}/bin")" || return 1
    node_path_prepend="${node_path_prepend:+"${node_path_prepend}:"}${bin_dir}"
  }

  _cardano_bins_add_bin_dir "cardano-node" || { cd "$origpwd" || true; return 1; }
  _cardano_bins_add_bin_dir "cardano-submit-api" || { cd "$origpwd" || true; return 1; }
  _cardano_bins_add_bin_dir "bech32" || { cd "$origpwd" || true; return 1; }

  if [ -z "$cli_rev" ]; then
    _cardano_bins_add_bin_dir "cardano-cli" || { cd "$origpwd" || true; return 1; }
  fi

  cd "$origpwd" || return 1
  echo "${node_path_prepend:+"${node_path_prepend}:"}"
}
