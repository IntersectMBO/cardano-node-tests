#!/usr/bin/env bash

set -euo pipefail

ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"

tests_repo="$(readlink -m "${0%/*}/..")"

get_coverage() {
  if [ ! -e "$ARTIFACTS_DIR" ]; then
    return 1
  fi
  oldpwd="$PWD"
  cd "$tests_repo"
  retval=0
  PYTHONPATH="$PWD:${PYTHONPATH:-}" cardano_node_tests/dump_requirements_coverage.py \
    -a "$ARTIFACTS_DIR" -m "src_docs/requirements_mapping.json" -o "$1" || retval=1
  cd "$oldpwd"
  return "$retval"
}

echo "Generating requirements coverage report to $tests_repo/requirements_coverage.json"
get_coverage "$tests_repo/requirements_coverage.json" || : > "$tests_repo/requirements_coverage.json"
