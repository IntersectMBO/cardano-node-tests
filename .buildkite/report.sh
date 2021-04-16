#!/usr/bin/env bash

set -euo pipefail

tests_repo="$1"
reports_dir="$tests_repo/.reports"

get_coverage() {
  if [ ! -e "$tests_repo/.cli_coverage" ] || ! hash cardano-cli; then
    return
  fi
  oldpwd="$PWD"
  cd "$tests_repo"
  PYTHONPATH="$PWD:$PYTHONPATH" cardano_node_tests/cardano_cli_coverage.py \
    -i .cli_coverage/cli_coverage_* \
    -o "$1"
  cd "$oldpwd"
}

allure_report_dir="$tests_repo/allure-report"
rm -rf "$allure_report_dir"
mkdir -p "$allure_report_dir"

cp -a "$reports_dir/environment.properties" "$allure_report_dir"
allure generate "$reports_dir" -o "$allure_report_dir" --clean
get_coverage "$allure_report_dir/cli_coverage.json"
