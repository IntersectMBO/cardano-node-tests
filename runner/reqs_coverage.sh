#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <artifacts_dir> <output_file>" >&2
  exit 1
fi

artifacts_dir="$1"
output_file="$2"

tests_repo="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine tests repo dir, exiting." >&2; exit 1; }

get_coverage() {
  if [ ! -e "$artifacts_dir" ]; then
    return 1
  fi
  oldpwd="$PWD"
  cd "$tests_repo"
  retval=0
  PYTHONPATH="$PWD:${PYTHONPATH:-}" cardano_node_tests/dump_requirements_coverage.py \
    -a "$artifacts_dir" -m "src_docs/requirements_mapping.json" -o "$1" || retval=1
  cd "$oldpwd"
  return "$retval"
}

echo "Generating requirements coverage report to $output_file"
get_coverage "$output_file" || : > "$output_file"
