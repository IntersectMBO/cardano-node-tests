#!/bin/bash

abort_install=0

if [ -z "${IN_NIX_SHELL:-""}" ]; then
  echo "This script is supposed to run inside nix shell." >&2
  abort_install=1
fi
if [ ! -d "cardano_node_tests" ]; then
  echo "This script is supposed to run from the root of cardano-node-tests directory." >&2
  abort_install=1
fi
if [ "$abort_install" -eq 1 ]; then
  exit 1
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
    exit 1
    ;;
esac

REPODIR="$PWD"
cd "$REPODIR" || exit 1

export WORKDIR="$REPODIR/dev_workdir"

rm -rf "${WORKDIR:?}"
mkdir -p "$WORKDIR"

# shellcheck disable=SC1091
. "$REPODIR/.github/setup_venv.sh"

export CARDANO_NODE_SOCKET_PATH="$PWD/dev_workdir/state-cluster0/bft1.socket" TMPDIR="$PWD/dev_workdir/tmp" DEV_CLUSTER_RUNNING=1 CLUSTERS_COUNT=1 FORBID_RESTART=1 NO_ARTIFACTS=1

PYTHONPATH=$PYTHONPATH:$PWD cardano_node_tests/prepare_cluster_scripts.py -s "cardano_node_tests/cluster_scripts/${CLUSTER_ERA}_fast" -d "$WORKDIR/${CLUSTER_ERA}_fast"

echo
echo
echo "----------------------------------------"
echo
echo "To start local testnet, run:"
echo "$WORKDIR/${CLUSTER_ERA}_fast/start-cluster"
echo
