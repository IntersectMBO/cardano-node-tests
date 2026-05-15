#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <reports_dir> <output_dir>" >&2
  exit 1
fi

reports_dir="$1"
output_dir="$2"

if [ "$(echo "$reports_dir"/*.json)" = "$reports_dir/*.json" ]; then
  echo "No reports found in $reports_dir" >&2
  exit 1
fi

mkdir -p "$output_dir" || { echo "Cannot create $output_dir" >&2; exit 1; }

results_tar="${output_dir}/allure-results.tar.xz"
allure_results_dir="${output_dir}/allure-results"

rm -rf "${allure_results_dir:?}"
mkdir -p "$allure_results_dir"
find "$reports_dir" -maxdepth 1 -type f \
  \( -name '*.json' -o -name '*.txt' -o -name '*.properties' \) \
  -exec mv -t "$allure_results_dir" {} +

echo "Creating results archive $results_tar"
rm -f "$results_tar"
tar -C "$output_dir" -cJf "$results_tar" allure-results
# Keep $allure_results_dir around so post-testrun consumers (e.g. failure
# analysis) can read it without having to untar the archive.
