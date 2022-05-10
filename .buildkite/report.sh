#!/usr/bin/env bash

set -xeuo pipefail

tests_repo="$1"
reports_dir="$tests_repo/.reports"
reports_tar="$tests_repo/allure-report.tar.xz"

allure_report_dir="$tests_repo/allure-report"
rm -rf "$allure_report_dir"
mkdir -p "$allure_report_dir"

cp -a "$reports_dir/environment.properties" "$allure_report_dir"
allure generate "$reports_dir" -o "$allure_report_dir" --clean

echo "Creating report archive $reports_tar"
rm -f "$reports_tar"
tar -C "$tests_repo" -cJf "$reports_tar" allure-report
