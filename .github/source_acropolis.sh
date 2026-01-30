#!/bin/bash
set -Eeuo pipefail

# Build Acropolis (omnibus) into $WORKDIR/acropolis-build<postfix>
acropolis_build() {
  local acropolis_rev="${1:?}"
  local origpwd="$PWD"

  cd "$WORKDIR" || return 1

  nix build \
    --accept-flake-config \
    --no-write-lock-file \
    "github:input-output-hk/acropolis?ref=${acropolis_rev}#acropolis-process-omnibus" \
    -o acropolis-build || { cd "$origpwd" || true; return 1; }

  [ -x acropolis-build/bin/acropolis-process-omnibus ] || { cd "$origpwd" || true; return 1; }

  cd "$origpwd" || return 1
}

# Print PATH to prepend for the acropolis build output.
acropolis_print_path_prepend() {
  local origpwd="$PWD"

  cd "$WORKDIR" || return 1

  local bin_dir
  bin_dir="$(readlink -m "acropolis-build/bin")" || { cd "$origpwd" || true; return 1; }

  cd "$origpwd" || return 1
  echo "${bin_dir}:"
}
