#!/usr/bin/env bash

# shellcheck disable=SC2030,SC2031,SC2059

set -euo pipefail

get_version() {
    local version
    version="$("$1" --version)"
    echo "${version#* }"
}

true="$(printf '\033[0;32m\u2714\033[0m' | iconv -f UTF-8)"
false="$(printf '\u274c' | iconv -f UTF-8)"

HAS_NODE="$([ -n "$(command -v cardano-node)" ] && echo "$true" || echo "$false")"
HAS_CLI="$([ -n "$(command -v cardano-cli)" ] && echo "$true" || echo "$false")"
HAS_PYTHON="$([ -n "$(command -v python)" ] && echo "$true" || echo "$false")"
HAS_PYTEST="$([ -n "$(command -v pytest)" ] && echo "$true" || echo "$false")"
HAS_NIX="$([ -n "$(command -v nix-shell)" ] && echo "$true" || echo "$false")"
HAS_JQ="$([ -n "$(command -v jq)" ] && echo "$true" || echo "$false")"
HAS_SUPERVISORD="$([ -n "$(command -v supervisord)" ] && echo "$true" || echo "$false")"
HAS_SUPERVISORCTL="$([ -n "$(command -v supervisorctl)" ] && echo "$true" || echo "$false")"
HAS_BECH32="$([ -n "$(command -v bech32)" ] && echo "$true" || echo "$false")"

IN_ROOT_DIR="$([ -d "cardano_node_tests" ] && echo "$true" || echo "$false")"
DEV_CLUSTER="$([ -n "${DEV_CLUSTER_RUNNING:-}" ] && echo "$true" || echo "$false")"
SOCKET_PATH_SET="$([ -n "${CARDANO_NODE_SOCKET_PATH:-}" ] && echo "$true" || echo "$false")"
USE_DBSYNC="$([ -n "${DBSYNC_REPO:-}" ] && echo "$true" || echo "-")"
P2P_NET="$([ -n "${ENABLE_P2P:-}" ] && echo "$true" || echo "-")"


printf "'cardano-node' available: $HAS_NODE\n"
printf "'cardano-cli' available: $HAS_CLI\n"
printf "'python' available: $HAS_PYTHON\n"
printf "'pytest' available: $HAS_PYTEST\n"
printf "'nix-shell' available: $HAS_NIX\n"
printf "'jq' available: $HAS_JQ\n"
printf "'supervisord' available: $HAS_SUPERVISORD\n"
printf "'supervisorctl' available: $HAS_SUPERVISORCTL\n"
printf "'bech32' available: $HAS_BECH32\n"

if [ "$HAS_NIX" = "$true" ]; then
  USING_NIX_SHELL="$([ -n "${IN_NIX_SHELL:-}" ] && echo "$true" || echo "$false")"
  printf "inside nix shell: $USING_NIX_SHELL\n"
fi

printf "in repo root: $IN_ROOT_DIR\n"
printf "DEV cluster: $DEV_CLUSTER\n"

if [ "$HAS_PYTHON" = "$true" ]; then
  PYTHON_WORKS="$(get_version python >/dev/null && echo "$true" || echo "$false")"
  printf "python works: $PYTHON_WORKS\n"

  if [ "$PYTHON_WORKS" = "$true" ]; then
    IN_VENV="$([ -n "${VIRTUAL_ENV:-}" ] && echo "$true" || echo "$false")"
    printf "in python venv: $IN_VENV\n"

    if [ "$IN_VENV" = "$true" ]; then
      VENV_IN_PYTHONPATH="$([[ "${PYTHONPATH:-}" == *"${VIRTUAL_ENV:-"missing__"}"* ]] && echo "$true" || echo "$false")"
      printf "venv in PYTHONPATH: $VENV_IN_PYTHONPATH\n"
    fi

    pushd "$HOME" > /dev/null
    TESTS_INSTALLED="$(python -c 'import cardano_node_tests' 2>/dev/null && echo "$true" || echo "$false")"
    popd > /dev/null
    printf "cardano-node-tests installed: $TESTS_INSTALLED\n"

    if [ "$HAS_PYTEST" = "$true" ]; then
      PYTEST_WORKS="$(get_version pytest >/dev/null && echo "$true" || echo "$false")"
      printf "pytest works: $PYTEST_WORKS\n"
    fi
  fi
fi

if [ "$HAS_NODE" = "$true" ] && [ "$HAS_CLI" = "$true" ]; then
  NODE_VERSION="$(get_version cardano-node)"
  CLI_VERSION="$(get_version cardano-cli)"
  SAME_VERSION="$([ "$NODE_VERSION" = "$CLI_VERSION" ] && echo "$true" || echo "$false")"
  printf "same version of node and cli: $SAME_VERSION\n"
fi

printf "socket path set: $SOCKET_PATH_SET\n"
if [ "$SOCKET_PATH_SET" = "$true" ]; then
  CORRECT_SOCKET_PATH="$([[ "${CARDANO_NODE_SOCKET_PATH:-}" == *state-cluster0* ]] && echo "$true" || echo "$false")"
  printf "socket path correct: $CORRECT_SOCKET_PATH\n"

  IS_SOCKET="$([ -S "${CARDANO_NODE_SOCKET_PATH:-}" ] && echo "$true" || echo "$false")"
  printf "socket path exists: $IS_SOCKET\n"
fi

printf "cluster era: %s\n" "${CLUSTER_ERA:-"default"}"
printf "transaction era: %s\n" "${TX_ERA:-"default"}"

printf "using dbsync (optional): $USE_DBSYNC\n"
if [ "$USE_DBSYNC" = "$true" ]; then
  HAS_DBSYNC="$([ -e "${DBSYNC_REPO:-}/db-sync-node/bin/cardano-db-sync" ] && echo "$true" || echo "$false")"
  printf "dbsync available: $HAS_DBSYNC\n"
fi

printf "P2P network (optional): $P2P_NET\n"
