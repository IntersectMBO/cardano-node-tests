#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <coverage_dir> <output_file>" >&2
  exit 1
fi

coverage_dir="$1"
output_file="$2"

tests_repo="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine test repo dir, exiting." >&2; exit 1; }

if [ ! -d "$coverage_dir" ]; then
  echo "Coverage dir '$coverage_dir' does not exist, exiting." >&2
  exit 1
fi

get_coverage() {
  if [ "$(echo "$coverage_dir"/cli_coverage_*)" = "$coverage_dir/cli_coverage_*" ] || ! command -v cardano-cli >/dev/null 2>&1; then
    return 1
  fi
  oldpwd="$PWD"
  cd "$tests_repo"
  retval=0
  PYTHONPATH="$PWD:${PYTHONPATH:-}" cardano_node_tests/cardano_cli_coverage.py \
    -i "$coverage_dir"/cli_coverage_* -o "$1" || retval=1
  cd "$oldpwd"
  return "$retval"
}

echo "Generating CLI coverage report to $output_file"
get_coverage "$output_file" || : > "$output_file"
