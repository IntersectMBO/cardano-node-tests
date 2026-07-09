#!/usr/bin/env bash

set -euo pipefail

top_dir="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine top dir, exiting." >&2; exit 1; }
# shellcheck disable=SC1091
. "$top_dir/scripts/common.sh"

if ! is_truthy "${DEV_CLUSTER_RUNNING:-}"; then
  echo "The 'DEV_CLUSTER_RUNNING' is not set. It is needed when running dev cluster, exiting." >&2
  exit 1
fi

socket_path="$(readlink -m "${CARDANO_NODE_SOCKET_PATH:-}")"
state_cluster="${socket_path%/*}"

if [ ! -e "$state_cluster" ]; then
  echo "Cannot find state cluster dir at '$state_cluster', exiting." >&2
  exit 1
fi

if [ ! -f "${state_cluster}/stop-cluster" ]; then
  echo "Cannot find '${state_cluster}/stop-cluster', exiting." >&2
  exit 1
fi

"$state_cluster/stop-cluster"
sleep 2

"$state_cluster/supervisord_start"
sleep 2

"$state_cluster/supervisorctl_local" start all
sleep 1

echo
echo "Dev cluster restarted; current status:"
"$state_cluster/supervisorctl_local" status all
