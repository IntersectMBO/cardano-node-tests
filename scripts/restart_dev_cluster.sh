#!/usr/bin/env bash

set -euo pipefail

SOCKET_PATH="$(readlink -m "$CARDANO_NODE_SOCKET_PATH")"
STATE_CLUSTER="${SOCKET_PATH%/*}"

# check if DEV_CLUSTER_RUNNING env variable is set
if [ -z "${DEV_CLUSTER_RUNNING:-}" ]; then
  echo "The 'DEV_CLUSTER_RUNNING' is not set. It is needed when running dev cluster, exiting."
  exit 1
fi

# check if "supervisord_stop" file exists in STATE_CLUSTER dir
if [ ! -f "${STATE_CLUSTER}/supervisord_stop" ]; then
  echo "Cannot find '${STATE_CLUSTER}/supervisord_stop', exiting."
  exit 1
fi

"$STATE_CLUSTER/supervisord_stop"
sleep 2

"$STATE_CLUSTER/supervisord_start"
sleep 2

"$STATE_CLUSTER/supervisorctl" start all
sleep 1

echo
echo "Dev cluster restarted; current status:"
"$STATE_CLUSTER/supervisorctl" status all
