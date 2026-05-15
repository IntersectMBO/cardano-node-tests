#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <artifacts_dir> <output_dir>" >&2
  exit 1
fi

artifacts_dir="$1"
output_dir="$2"

if [ "$(echo "$artifacts_dir"/*)" = "$artifacts_dir/*" ]; then
  echo "No artifacts found in $artifacts_dir" >&2
  exit 1
fi

mkdir -p "$output_dir" || { echo "Cannot create $output_dir" >&2; exit 1; }

new_dir="${output_dir}/testing_artifacts"
rm -rf "${new_dir:?}"
mv "$artifacts_dir" "$new_dir" || { echo "Cannot move $artifacts_dir to $new_dir" >&2; exit 1; }

artifacts_tar="${output_dir}/testing_artifacts.tar.xz"
echo "Creating artifacts archive $artifacts_tar"
rm -f "$artifacts_tar"
tar -C "$output_dir" -cJf "$artifacts_tar" testing_artifacts
