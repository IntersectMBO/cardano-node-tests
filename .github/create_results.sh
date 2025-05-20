#!/usr/bin/env bash

set -euo pipefail

tests_repo="$(readlink -m "${0%/*}/..")"
reports_dir="$tests_repo/.reports"
if [ "$(echo "$reports_dir"/*)" = "$reports_dir/*" ]; then
  echo "No reports found in $reports_dir" >&2
  exit 1
fi

results_tar="$tests_repo/allure-results.tar.xz"
allure_results_dir="$tests_repo/allure-results"

rm -rf "${allure_results_dir:?}"
mkdir -p "$allure_results_dir"
mv "$reports_dir"/{*.json,*.txt,*.properties} "$allure_results_dir"

echo "Creating results archive $results_tar"
rm -f "$results_tar"
tar -C "$tests_repo" -cJf "$results_tar" allure-results
rm -rf "${allure_results_dir:?}"
