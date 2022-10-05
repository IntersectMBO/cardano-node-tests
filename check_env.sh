#!/usr/bin/env bash

# shellcheck disable=SC2030,SC2031,SC2059

set -euo pipefail

true="$(printf '\u2714' | iconv -f UTF-8)"
false="$(printf '\u274c' | iconv -f UTF-8)"

HAS_NIX="$([ -n "$(command -v nix-shell)" ] && echo "$true" || echo "$false")"
USING_NIX_SHELL="$([ -n "${IN_NIX_SHELL:-}" ] && echo "$true" || echo "$false")"
IN_ROOT_DIR="$([ -d "cardano_node_tests" ] && echo "$true" || echo "$false")"
IN_VENV="$([ -n "${VIRTUAL_ENV:-}" ] && echo "$true" || echo "$false")"
VENV_IN_PYTHONPATH="$([[ "${PYTHONPATH:-}" == *"${VIRTUAL_ENV:-"missing__"}"* ]] && echo "$true" || echo "$false")"
DEV_CLUSTER="$([ -n "${DEV_CLUSTER_RUNNING:-}" ] && echo "$true" || echo "$false")"
HAS_NODE="$([ -n "$(command -v cardano-node)" ] && echo "$true" || echo "$false")"
HAS_CLI="$([ -n "$(command -v cardano-cli)" ] && echo "$true" || echo "$false")"
SOCKET_PATH_SET="$([ -n "${CARDANO_NODE_SOCKET_PATH:-}" ] && echo "$true" || echo "$false")"
CORRECT_SOCKET_PATH="$([[ "${CARDANO_NODE_SOCKET_PATH:-}" == *state-cluster0* ]] && echo "$true" || echo "$false")"
USE_DBSYNC="$([ -n "${DBSYNC_REPO:-}" ] && echo "$true" || echo "$false")"
HAS_DBSYNC="$([ -e "${DBSYNC_REPO:-}/db-sync-node/bin/cardano-db-sync" ] && echo "$true" || echo "$false")"
P2P_NET="$([ -n "${ENABLE_P2P:-}" ] && echo "$true" || echo "$false")"

printf "'nix-shell' available: $HAS_NIX\n"
if [ "$HAS_NIX" = "$true" ]; then
  printf "inside nix shell: $USING_NIX_SHELL\n"
fi
printf "in repo root: $IN_ROOT_DIR\n"
printf "in python venv: $IN_VENV\n"
if [ "$IN_VENV" = "$true" ]; then
  printf "venv in python path: $VENV_IN_PYTHONPATH\n"
fi
printf "dev cluster running: $DEV_CLUSTER\n"
printf "cardano-node available: $HAS_NODE\n"
printf "cardano-cli available: $HAS_CLI\n"
printf "socket path set: $SOCKET_PATH_SET\n"
if [ "$SOCKET_PATH_SET" = "$true" ]; then
  printf "socket path correct: $CORRECT_SOCKET_PATH\n"
fi
printf "cluster_era: %s\n" "${CLUSTER_ERA:-"default"}"
printf "transaction era: %s\n" "${TX_ERA:-"default"}"
printf "using dbsync (optional): $USE_DBSYNC\n"
if [ "$USE_DBSYNC" = "$true" ]; then
    printf "dbsync available: $HAS_DBSYNC\n"
fi
printf "p2p network (optional): $P2P_NET\n"
