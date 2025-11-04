#!/usr/bin/env bash

# shellcheck disable=SC2030,SC2031,SC2059

set -uo pipefail

exit_code=0

get_version() {
    local version
    version="$("$1" --version)"
    echo "${version#* }"
}

get_rev() {
    local revision
    revision="$("$1" --version | grep "git rev")"
    echo "${revision#* }"
}

true="$(printf '\033[0;32m\u2714\033[0m' | iconv -f UTF-8)"
false="$(printf '\u274c' | iconv -f UTF-8)"

process_result() {
    local result="$?"
    local optional="${1:-""}"

    if [ "$result" -eq 0 ]; then
        echo "$true"
    elif [ -n "$optional" ]; then
        echo "-"
    else
        echo "$false"
        return 1
    fi
}

HAS_NODE="$([ -n "$(command -v cardano-node)" ]; process_result)" || exit_code=1
HAS_CLI="$([ -n "$(command -v cardano-cli)" ]; process_result)" || exit_code=1
HAS_PYTHON="$([ -n "$(command -v python)" ]; process_result)" || exit_code=1
HAS_PYTEST="$([ -n "$(command -v pytest)" ]; process_result)" || exit_code=1
HAS_NIX="$([ -n "$(command -v nix-shell)" ]; process_result)" || exit_code=1
HAS_JQ="$([ -n "$(command -v jq)" ]; process_result)" || exit_code=1
HAS_SUPERVISORD="$([ -n "$(command -v supervisord)" ]; process_result)" || exit_code=1
HAS_SUPERVISORCTL="$([ -n "$(command -v supervisorctl)" ]; process_result)" || exit_code=1
HAS_BECH32="$([ -n "$(command -v bech32)" ]; process_result)" || exit_code=1

IN_ROOT_DIR="$([ -d "cardano_node_tests" ]; process_result)" || exit_code=1
DEV_CLUSTER="$([ -n "${DEV_CLUSTER_RUNNING:-}" ]; process_result)" || exit_code=1
SOCKET_PATH_SET="$([ -n "${CARDANO_NODE_SOCKET_PATH:-}" ]; process_result)" || exit_code=1
USE_DBSYNC="$([ -n "${DBSYNC_SCHEMA_DIR:-}" ]; process_result "optional")" || exit_code=1
P2P_NET="$([ -z "${ENABLE_LEGACY:-}" ]; process_result "optional")" || exit_code=1


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
  USING_NIX_SHELL="$([ -n "${IN_NIX_SHELL:-}" ]; process_result)" || exit_code=1
  printf "inside nix shell: $USING_NIX_SHELL\n"
fi

printf "in repo root: $IN_ROOT_DIR\n"
printf "DEV cluster: $DEV_CLUSTER\n"

if [ "$HAS_PYTHON" = "$true" ]; then
  PYTHON_WORKS="$(get_version python >/dev/null; process_result)" || exit_code=1
  printf "python works: $PYTHON_WORKS\n"

  if [ "$PYTHON_WORKS" = "$true" ]; then
    IN_VENV="$([ -n "${VIRTUAL_ENV:-}" ]; process_result)" || exit_code=1
    printf "in python venv: $IN_VENV\n"

    if [ "$IN_VENV" = "$true" ]; then
      NIX_NOT_IN_PYTHONPATH="$([[ "${PYTHONPATH:-"empty__"}" != */nix/store/* ]]; process_result)" || exit_code=1
      printf "nix not in PYTHONPATH: $NIX_NOT_IN_PYTHONPATH\n"
    fi

    pushd "$HOME" > /dev/null || exit 1
    TESTS_INSTALLED="$(python -c 'import cardano_node_tests' 2>/dev/null; process_result)" || exit_code=1
    popd > /dev/null || exit 1
    printf "cardano-node-tests installed: $TESTS_INSTALLED\n"

    if [ "$HAS_PYTEST" = "$true" ]; then
      PYTEST_WORKS="$(get_version pytest >/dev/null; process_result)" || exit_code=1
      printf "pytest works: $PYTEST_WORKS\n"
    fi
  fi
fi

if [ "$HAS_NODE" = "$true" ] && [ "$HAS_CLI" = "$true" ]; then
  NODE_VERSION="$(get_rev cardano-node)"
  CLI_VERSION="$(get_rev cardano-cli)"
  SAME_VERSION="$([ "$NODE_VERSION" = "$CLI_VERSION" ]; process_result)" || exit_code=1
  printf "same version of node and cli: $SAME_VERSION\n"
fi

printf "socket path set: $SOCKET_PATH_SET\n"
if [ "$SOCKET_PATH_SET" = "$true" ]; then
  CORRECT_SOCKET_PATH="$([[ "${CARDANO_NODE_SOCKET_PATH:-}" == */state-cluster* ]]; process_result)" || exit_code=1
  printf "socket path correct: $CORRECT_SOCKET_PATH\n"

  IS_SOCKET="$([ -S "${CARDANO_NODE_SOCKET_PATH:-}" ]; process_result)" || exit_code=1
  printf "socket path exists: $IS_SOCKET\n"
fi

printf "cluster era: %s\n" "${CLUSTER_ERA:-"default"}"
printf "command era: %s\n" "${COMMAND_ERA:-"latest"}"

printf "using dbsync (optional): $USE_DBSYNC\n"
if [ "$USE_DBSYNC" = "$true" ]; then
  HAS_DBSYNC="$(command -v cardano-db-sync >/dev/null 2>&1; process_result)" || exit_code=1
  printf "dbsync available: $HAS_DBSYNC\n"
  HAS_PSQL="$([ -n "$(command -v psql)" ]; process_result)" || exit_code=1
  printf "'psql' available: $HAS_PSQL\n"
fi

printf "P2P network (optional): $P2P_NET\n"

exit "$exit_code"
