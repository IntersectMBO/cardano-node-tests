#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <artifacts_dir> <output_dir>" >&2
  exit 1
fi

artifacts_dir="$1"
output_dir="$2"

mkdir -p "$output_dir" || { echo "Cannot create $output_dir" >&2; exit 1; }

# The status database was copied from the pytest temp dir to the artifacts dir together
# with the other testing artifacts. Copy the database of each pytest run to the output
# dir. The output databases are numbered in the run order (e.g. one per node upgrade
# step) - the `pytest-N` component of the artifacts subdir names cannot be used directly,
# as the number comes from pytest's numbered basetemp counter that is shared by all
# pytest invocations in a testrun, including those that don't collect artifacts.
# Copy also the WAL sidecar, which is present when database connections were still open
# during artifacts collection - without it the database copy would miss the
# not-yet-checkpointed writes. The `-shm` file is skipped on purpose, it is transient
# and not needed for an offline copy.
found=0
num=0
while read -r db; do
  # Skip the unexpanded pattern when the glob didn't match anything
  [ -f "$db" ] || continue
  num=$((num + 1))
  cp "$db" "${output_dir}/cm-status-${num}.db" \
    || { echo "Failed to copy $db" >&2; continue; }
  found=1
  if [ -e "${db}-wal" ]; then
    cp "${db}-wal" "${output_dir}/cm-status-${num}.db-wal" \
      || echo "Failed to copy ${db}-wal" >&2
  fi
done < <(printf '%s\n' "$artifacts_dir"/pytest-*/cm-status.db | sort -V)

if [ "$found" -eq 0 ]; then
  echo "No status database copied from $artifacts_dir" >&2
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::warning::No cluster status database was copied from the testing artifacts, none uploaded."
  fi
fi
