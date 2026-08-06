#!/bin/bash

# Build all required binaries into $WORKDIR/*-build<postfix>.
# When `skip_bindir` (absolute path) is given, usable executables already
# present there are not built. The `skip_bindir` support requires
# `is_usable_binary` and `report_existing_binary` from `scripts/common.sh`.
cardano_bins_build_all() {
  : "${WORKDIR:?"WORKDIR must be set to a writable directory"}"
  local node_rev="${1:?}"
  local cli_rev="${2:-}"
  local node_bindir_postfix="${3:-}"
  local skip_bindir="${4:-}"
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

    if [ -n "$skip_bindir" ] && is_usable_binary "${skip_bindir}/${exe}"; then
      echo "Skipping build of '${exe}'"
      report_existing_binary "${skip_bindir}/${exe}" || return 1
      return 0
    fi

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
  _cardano_bins_build_one "tx-generator" "tx-generator" || { cd "$origpwd" || true; return 1; }

  if [ -z "$cli_rev" ]; then
    _cardano_bins_build_one "cardano-cli" "cardano-cli" || { cd "$origpwd" || true; return 1; }
  fi

  cd "$origpwd" || return 1
}

# Print PATH to prepend based on previously built outputs.
# When `skip_bindir` (absolute path) is given, entries for usable executables
# already present there are omitted (their build was skipped in
# `cardano_bins_build_all`). The `skip_bindir` support requires
# `is_usable_binary` from `scripts/common.sh`.
# The output may be empty when all binaries are present in `skip_bindir`.
cardano_bins_print_path_prepend() {
  : "${WORKDIR:?"WORKDIR must be set to a writable directory"}"
  local cli_rev="${1:-}"
  local node_bindir_postfix="${2:-}"
  local skip_bindir="${3:-}"
  local origpwd="$PWD"

  cd "$WORKDIR" || return 1

  local node_path_prepend=""

  _cardano_bins_add_bin_dir() {
    local out_prefix="$1"
    local exe="$2"
    local out="${out_prefix}-build${node_bindir_postfix}"
    local bin_dir

    if [ -n "$skip_bindir" ] && is_usable_binary "${skip_bindir}/${exe}"; then
      return 0
    fi

    bin_dir="$(readlink -f "${out}/bin")" || {
      echo "Missing build output '${out}/bin' for '${exe}'" >&2
      return 1
    }
    node_path_prepend="${node_path_prepend:+"${node_path_prepend}:"}${bin_dir}"
  }

  _cardano_bins_add_bin_dir "cardano-node" "cardano-node" || { cd "$origpwd" || true; return 1; }
  _cardano_bins_add_bin_dir "cardano-submit-api" "cardano-submit-api" || { cd "$origpwd" || true; return 1; }
  _cardano_bins_add_bin_dir "bech32" "bech32" || { cd "$origpwd" || true; return 1; }
  _cardano_bins_add_bin_dir "tx-generator" "tx-generator" || { cd "$origpwd" || true; return 1; }

  if [ -z "$cli_rev" ]; then
    _cardano_bins_add_bin_dir "cardano-cli" "cardano-cli" || { cd "$origpwd" || true; return 1; }
  fi

  cd "$origpwd" || return 1
  echo "${node_path_prepend:+"${node_path_prepend}:"}"
}
