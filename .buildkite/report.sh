#!/usr/bin/env bash

set -euo pipefail

tests_repo="$1"
reports_dir="$tests_repo/.reports"
reports_tar="$tests_repo/allure-report.tar.xz"

get_coverage() {
  if [ ! -e "$tests_repo/.cli_coverage" ] || ! hash cardano-cli; then
    return 1
  fi
  oldpwd="$PWD"
  cd "$tests_repo"
  retval=0
  PYTHONPATH="$PWD:$PYTHONPATH" cardano_node_tests/cardano_cli_coverage.py \
    -i .cli_coverage/cli_coverage_* -o "$1" || retval=1
  cd "$oldpwd"
  return "$retval"
}

allure_report_dir="$tests_repo/allure-report"
rm -rf "$allure_report_dir"
mkdir -p "$allure_report_dir"

cp -a "$reports_dir/environment.properties" "$allure_report_dir"
allure generate "$reports_dir" -o "$allure_report_dir" --clean

echo "Creating report archive $reports_tar"
rm -f "$reports_tar"
tar -C "$tests_repo" -cJf "$reports_tar" allure-report

echo "Generating CLI coverage report to $tests_repo/cli_coverage.json"
get_coverage "$tests_repo/cli_coverage.json" || : > "$tests_repo/cli_coverage.json"
