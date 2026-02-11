#!/usr/bin/env bash

set -euo pipefail

TOP_DIR="$(readlink -m "${0%/*}/..")"
# shellcheck disable=SC1091
. "$TOP_DIR/scripts/common.sh"

if ! is_truthy "${DEV_CLUSTER_RUNNING:-}"; then
  echo "The 'DEV_CLUSTER_RUNNING' is not set. It is needed when running dev cluster, exiting." >&2
  exit 1
fi

SOCKET_PATH="$(readlink -m "${CARDANO_NODE_SOCKET_PATH:-}")"
STATE_CLUSTER="${SOCKET_PATH%/*}"

if [ ! -e "$STATE_CLUSTER" ]; then
  echo "Cannot find state cluster dir at '$STATE_CLUSTER', exiting." >&2
  exit 1
fi

if [ ! -f "${STATE_CLUSTER}/stop-cluster" ]; then
  echo "Cannot find '${STATE_CLUSTER}/stop-cluster', exiting." >&2
  exit 1
fi

"$STATE_CLUSTER/stop-cluster"
sleep 2

"$STATE_CLUSTER/supervisord_start"
sleep 2

"$STATE_CLUSTER/supervisorctl" start all
sleep 1

echo
echo "Dev cluster restarted; current status:"
"$STATE_CLUSTER/supervisorctl" status all
