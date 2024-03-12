#!/bin/bash

if ! (return 0 2>/dev/null); then
  echo "This script is supposed to be sourced, not executed." >&2
  exit 1
fi

if [ -z "${IN_NIX_SHELL:-""}" ]; then
  echo "This script is supposed to be sourced from nix shell." >&2
  return 1
fi

case "${1:-""}" in
  "babbage")
    export CLUSTER_ERA=babbage
    ;;
  "conway")
    export CLUSTER_ERA=conway COMMAND_ERA=conway
    ;;
  *)
    echo "Usage: $0 {babbage|conway}"
    return 1
    ;;
esac

_cur_file="${BASH_SOURCE[${#BASH_SOURCE[@]} - 1]}"
REPODIR="$(readlink -m "${_cur_file%/*}")"
cd "$REPODIR" || return 1
unset _cur_file

export WORKDIR="$REPODIR/dev_workdir"

rm -rf "${WORKDIR:?}"
mkdir -p "$WORKDIR"

# shellcheck disable=SC1091
. "$REPODIR/.github/setup_venv.sh"

export \
  CARDANO_NODE_SOCKET_PATH="$PWD/dev_workdir/state-cluster0/bft1.socket" \
  TMPDIR="$PWD/dev_workdir/tmp" \
  DEV_CLUSTER_RUNNING=1 \
  CLUSTERS_COUNT=1 \
  FORBID_RESTART=1 \
  NO_ARTIFACTS=1

_scripts_dest="$WORKDIR/${CLUSTER_ERA}_fast"
if [ ! -d "$_scripts_dest" ]; then
  PYTHONPATH=$PYTHONPATH:$PWD cardano_node_tests/prepare_cluster_scripts.py \
    -s "cardano_node_tests/cluster_scripts/${CLUSTER_ERA}_fast" \
    -d "$_scripts_dest"
fi
unset _scripts_dest

echo
echo
echo "----------------------------------------"
echo
echo "To start local testnet, run:"
echo "$WORKDIR/${CLUSTER_ERA}_fast/start-cluster"
echo
