#! /usr/bin/env nix-shell
#! nix-shell -i bash --pure --keep CARDANO_NODE_SOCKET_PATH --keep PGHOST --keep PGPORT --keep PGUSER --keep PGPASSFILE --keep DBSYNC_REPO -p postgresql
# shellcheck shell=bash

set -uo pipefail

SOCKET_PATH="$(readlink -m "$CARDANO_NODE_SOCKET_PATH")"
STATE_CLUSTER="${SOCKET_PATH%/*}"
STATE_CLUSTER_NAME="${STATE_CLUSTER##*/}"

exec "$DBSYNC_REPO/smash-server/bin/cardano-smash-server" --config "./$STATE_CLUSTER_NAME/dbsync-config.yaml" --port 3100 --admins "./$STATE_CLUSTER_NAME/admins.txt"
