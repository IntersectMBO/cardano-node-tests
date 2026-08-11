#!/usr/bin/env bash

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <artifacts_dir> <output_file>" >&2
  exit 1
fi

artifacts_dir="$1"
output_file="$2"

cd "$artifacts_dir" || { echo "Cannot switch to $artifacts_dir" >&2; exit 1; }
# Hidden dirs are skipped - a hidden dir can be a leftover staging dir of an
# interrupted `copy_artifacts.sh` run, holding an incomplete copy of the artifacts.
# The `.?*` glob (unlike `.*`) doesn't match the `.` start dir, that GNU grep would
# otherwise exclude as well.
grep -r --exclude-dir=".?*" --include "*.stdout" --include "*.stderr" -Ei ":error:|failed|failure" . > "$output_file" || :
