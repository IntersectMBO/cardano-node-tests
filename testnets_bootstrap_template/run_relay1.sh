#!/bin/sh

exec cardano-node run --topology topology-relay1.json --database-path relay1-db/ --socket-path relay1.socket --config config-relay1.json "$@"
