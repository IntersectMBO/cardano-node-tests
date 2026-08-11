#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <artifacts_dir> [newer_than_file]" >&2
  exit 1
fi

artifacts_dir="$1"
newer_than="${2:-}"

# Report a problem with collecting the artifacts. The testrun artifacts are essential
# for debugging failures, so on GitHub Actions the message is also emitted as a warning
# annotation that is visible in the run summary.
warn() {
  echo "$1" >&2
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::warning::$1"
  fi
}

# The `pytest-current` symlink points to the numbered pytest temp dir of the most
# recent pytest run. It must be resolved right after the run finishes, before another
# pytest invocation repoints it. Assumes pytest ran with its default basetemp under
# the current temp dir (pytest resolves the temp dir the same way as Python's
# `tempfile.gettempdir()`).
tmp_root="${TMPDIR:-${TEMP:-${TMP:-/tmp}}}"
pytest_current="${tmp_root}/pytest-of-${LOGNAME:-${USER:-$(id -un)}}/pytest-current"
if [ ! -L "$pytest_current" ]; then
  warn "Pytest temp dir symlink '$pytest_current' not found"
  exit 1
fi
pytest_tmp="$(readlink -f "$pytest_current")"
if [ ! -d "$pytest_tmp" ]; then
  warn "Pytest temp dir '$pytest_tmp' not found"
  exit 1
fi

# The guard must fail closed - `test -nt` would be true (and the copy allowed) if the
# reference file didn't exist.
if [ -n "$newer_than" ] && [ ! -e "$newer_than" ]; then
  warn "Reference file '$newer_than' not found, not copying"
  exit 1
fi

# When a pytest run dies before it creates its temp dir, the symlink still points
# to the temp dir of a previous invocation. Refuse to copy a temp dir that predates
# the given reference file, so a stale dir of an earlier run is not copied.
if [ -n "$newer_than" ] && [ ! "$pytest_tmp" -nt "$newer_than" ]; then
  warn "Pytest temp dir '$pytest_tmp' predates '$newer_than', not copying"
  exit 1
fi

mkdir -p "$artifacts_dir" || { warn "Cannot create $artifacts_dir"; exit 1; }

# Copy to a hidden staging dir and rename it when the copy is finished, so consumers
# of the artifacts dir cannot see the tree while it is still being copied. The `mktemp`
# suffix also keeps repeated copies of a same-numbered pytest temp dir from clashing.
staging_dir="$(mktemp -d "${artifacts_dir}/.$(basename "$pytest_tmp")-XXXXXXXX")"
copy_rc=0
cp -a "$pytest_tmp/." "$staging_dir" || copy_rc="$?"

if [ "$copy_rc" -ne 0 ] && [ -z "$(ls -A "$staging_dir")" ]; then
  # Nothing was copied. Don't publish the empty dir - it would defeat the
  # "no artifacts found" detection of the artifacts dir consumers.
  rm -rf "$staging_dir"
  warn "Failed to copy '$pytest_tmp'"
  exit 1
fi

staging_name="$(basename "$staging_dir")"
destdir="${artifacts_dir}/${staging_name#.}"
mv "$staging_dir" "$destdir"

if [ "$copy_rc" -ne 0 ]; then
  # A partial copy is better than no artifacts at all
  warn "Some content of '$pytest_tmp' was not copied to '$destdir'"
  exit 1
fi
echo "Collected artifacts copied to '$destdir'."
