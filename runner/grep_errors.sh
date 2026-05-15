#!/usr/bin/env bash

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <artifacts_dir> <output_file>" >&2
  exit 1
fi

artifacts_dir="$1"
output_file="$2"

cd "$artifacts_dir" || { echo "Cannot switch to $artifacts_dir" >&2; exit 1; }
grep -r --include "*.stdout" --include "*.stderr" -Ei ":error:|failed|failure" . > "$output_file" || :
