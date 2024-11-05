#!/bin/bash

# Meant to run on long running testnet such as Preview.

if [ -z "$CARDANO_NODE_SOCKET_PATH" ]; then
  echo "CARDANO_NODE_SOCKET_PATH is not set" >&2
  exit 1
fi

BOOTSRAP_DIR="${CARDANO_NODE_SOCKET_PATH%/*}"
cd "$BOOTSRAP_DIR" || exit 1

rm -f nodefifo
mkfifo nodefifo

(
  exec 3<nodefifo  # open fifo for reading
  exec cardano-node run --topology topology-relay1.json --database-path relay1-db/ --socket-path relay1.socket --config config-relay1.json --shutdown-ipc 3 > relay1.log 2>&1
) &
bpid=$!
echo "$bpid"

exec 4>nodefifo  # open fifo for writing
sleep 120  # let node start and run for a while

exec 4>&-  # close the pipe, node should shutdown
echo Node should be shutting down
sleep 20
