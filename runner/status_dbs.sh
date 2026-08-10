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
# step). The run order is derived from the database modification times (preserved by
# the copy to the artifacts dir) - the `pytest-N` component of the artifacts subdir
# names cannot be used, as pytest's numbered basetemp counter is per temp root and is
# shared by all pytest invocations that use the root, including invocations whose
# artifacts are not collected. The mtime ordering holds no matter how the launcher
# distributes the pytest invocations over temp roots.
# Copy also the WAL sidecar. As artifacts are copied only after pytest exits, the WAL
# is normally checkpointed into the database and the sidecar removed. It can still be
# left behind when the run was killed - copy it in that case, otherwise the database
# copy would miss the not-yet-checkpointed writes. The `-shm` file is skipped on
# purpose, it is transient and not needed for an offline copy.
found=0
num=0
while read -r db; do
  # The file could disappear before it is copied
  [ -f "$db" ] || continue
  num=$((num + 1))
  cp "$db" "${output_dir}/cm-status-${num}.db" \
    || { echo "Failed to copy $db" >&2; continue; }
  found=1
  if [ -e "${db}-wal" ]; then
    cp "${db}-wal" "${output_dir}/cm-status-${num}.db-wal" \
      || echo "Failed to copy ${db}-wal" >&2
  fi
done < <(
  # Hidden dirs are excluded explicitly - a hidden dir can be a leftover staging dir
  # of an interrupted `copy_artifacts.sh` run
  find "$artifacts_dir" -mindepth 2 -maxdepth 2 ! -path '*/.*' \
    -path '*/pytest-*/cm-status.db' -printf '%T@ %p\n' | sort -n | cut -d' ' -f2-
)

if [ "$found" -eq 0 ]; then
  echo "No status database copied from $artifacts_dir" >&2
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::warning::No cluster status database was copied from the testing artifacts, none uploaded."
  fi
fi
