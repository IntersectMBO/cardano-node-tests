#!/usr/bin/env bash

# shellcheck disable=SC2030,SC2031,SC2059

set -uo pipefail

_top_dir="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine top dir, exiting." >&2; exit 1; }
# shellcheck disable=SC1091
. "$_top_dir/scripts/common.sh"

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

TRUE="$(printf '\033[0;32m\u2714\033[0m' | iconv -f UTF-8)"
FALSE="$(printf '\u274c' | iconv -f UTF-8)"
readonly TRUE FALSE

process_result() {
    local result="$?"
    local optional="${1:-""}"

    if [ "$result" -eq 0 ]; then
        echo "$TRUE"
    elif [ -n "$optional" ]; then
        echo "-"
    else
        echo "$FALSE"
        return 1
    fi
}

has_node="$([ -n "$(command -v cardano-node)" ]; process_result)" || exit_code=1
has_cli="$([ -n "$(command -v cardano-cli)" ]; process_result)" || exit_code=1
has_submit_api="$([ -n "$(command -v cardano-submit-api)" ]; process_result "optional")" || exit_code=1
has_bech32="$([ -n "$(command -v bech32)" ]; process_result)" || exit_code=1
has_tx_generator="$([ -n "$(command -v tx-generator)" ]; process_result "optional")" || exit_code=1
has_python="$([ -n "$(command -v python)" ]; process_result)" || exit_code=1
has_pytest="$([ -n "$(command -v pytest)" ]; process_result)" || exit_code=1
has_nix="$([ -n "$(command -v nix)" ]; process_result)" || exit_code=1
has_jq="$([ -n "$(command -v jq)" ]; process_result)" || exit_code=1
has_supervisord="$([ -n "$(command -v supervisord)" ]; process_result)" || exit_code=1
has_supervisorctl="$([ -n "$(command -v supervisorctl)" ]; process_result)" || exit_code=1

in_root_dir="$([ -d "cardano_node_tests" ]; process_result)" || exit_code=1
dev_cluster="$(is_truthy "${DEV_CLUSTER_RUNNING:-}"; process_result)" || exit_code=1
socket_path_set="$([ -n "${CARDANO_NODE_SOCKET_PATH:-}" ]; process_result)" || exit_code=1
use_dbsync="$([ -n "${DBSYNC_SCHEMA_DIR:-}" ]; process_result "optional")" || exit_code=1


printf "'cardano-node' available: $has_node\n"
printf "'cardano-cli' available: $has_cli\n"
printf "'cardano-submit-api' available (optional): $has_submit_api\n"
printf "'bech32' available: $has_bech32\n"
printf "'tx-generator' available (optional): $has_tx_generator\n"
printf "'python' available: $has_python\n"
printf "'pytest' available: $has_pytest\n"
printf "'nix' available: $has_nix\n"
printf "'jq' available: $has_jq\n"
printf "'supervisord' available: $has_supervisord\n"
printf "'supervisorctl' available: $has_supervisorctl\n"

printf "in repo root: $in_root_dir\n"
printf "DEV cluster: $dev_cluster\n"

if [ "$has_python" = "$TRUE" ]; then
  python_works="$(get_version python >/dev/null; process_result)" || exit_code=1
  printf "python works: $python_works\n"

  if [ "$python_works" = "$TRUE" ]; then
    in_venv="$([ -n "${VIRTUAL_ENV:-}" ]; process_result)" || exit_code=1
    printf "in python venv: $in_venv\n"

    if [ "$in_venv" = "$TRUE" ]; then
      nix_not_in_pythonpath="$([[ "${PYTHONPATH:-"empty__"}" != */nix/store/* ]]; process_result)" || exit_code=1
      printf "nix not in PYTHONPATH: $nix_not_in_pythonpath\n"
    fi

    pushd "$HOME" > /dev/null || exit 1
    tests_installed="$(python -c 'import cardano_node_tests' 2>/dev/null; process_result)" || exit_code=1
    popd > /dev/null || exit 1
    printf "cardano-node-tests installed: $tests_installed\n"

    if [ "$has_pytest" = "$TRUE" ]; then
      pytest_works="$(get_version pytest >/dev/null; process_result)" || exit_code=1
      printf "pytest works: $pytest_works\n"
    fi
  fi
fi

if [ "$has_node" = "$TRUE" ] && [ "$has_cli" = "$TRUE" ]; then
  node_version="$(get_rev cardano-node)"
  cli_version="$(get_rev cardano-cli)"
  same_version="$([ "$node_version" = "$cli_version" ]; process_result)" || exit_code=1
  printf "same version of node and cli: $same_version\n"
fi

printf "socket path set: $socket_path_set\n"
if [ "$socket_path_set" = "$TRUE" ]; then
  correct_socket_path="$([[ "${CARDANO_NODE_SOCKET_PATH:-}" == */state-cluster* ]]; process_result)" || exit_code=1
  printf "socket path correct: $correct_socket_path\n"

  is_socket="$([ -S "${CARDANO_NODE_SOCKET_PATH:-}" ]; process_result)" || exit_code=1
  printf "socket path exists: $is_socket\n"
fi

printf "command era: %s\n" "${COMMAND_ERA:-"latest"}"

printf "using dbsync (optional): $use_dbsync\n"
if [ "$use_dbsync" = "$TRUE" ]; then
  has_dbsync="$(command -v cardano-db-sync >/dev/null 2>&1; process_result)" || exit_code=1
  printf "dbsync available: $has_dbsync\n"
  has_psql="$([ -n "$(command -v psql)" ]; process_result)" || exit_code=1
  printf "'psql' available: $has_psql\n"
fi

exit "$exit_code"
