#!/usr/bin/env bash

set -euo pipefail

tests_repo="$(readlink -m "${0%/*}/..")"

get_coverage() {
  if [ "$(echo "$tests_repo"/.cli_coverage/*)" = "$tests_repo/.cli_coverage/*" ] || ! command -v cardano-cli >/dev/null 2>&1; then
    return 1
  fi
  oldpwd="$PWD"
  cd "$tests_repo"
  retval=0
  PYTHONPATH="$PWD:${PYTHONPATH:-}" cardano_node_tests/cardano_cli_coverage.py \
    -i .cli_coverage/cli_coverage_* -o "$1" || retval=1
  cd "$oldpwd"
  return "$retval"
}

echo "Generating CLI coverage report to $tests_repo/cli_coverage.json"
get_coverage "$tests_repo/cli_coverage.json" || : > "$tests_repo/cli_coverage.json"
