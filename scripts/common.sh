#!/usr/bin/env bash

is_truthy() {
  local val="${1:-}"
  val=${val,,}

  case "$val" in
    1 | true | yes | y | on | enabled )
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_venv_active() {
  [ -n "${VIRTUAL_ENV:-}" ]
}

# Acquire an exclusive, non-blocking lock tied to the given workdir to prevent
# concurrent testruns from clobbering each other's workdir. The lock is held
# for the lifetime of the calling shell; it is released automatically on exit.
# The lock file lives next to the workdir so that wiping the workdir does not
# drop the lock.
# Usage: acquire_workdir_lock <workdir>
acquire_workdir_lock() {
  local workdir="${1:?acquire_workdir_lock requires a workdir argument}"
  local lockfile="${workdir}.lock"
  local lockfd

  if ! command -v flock >/dev/null 2>&1; then
    echo "Error: 'flock' is required for testrun locking but was not found." >&2
    return 1
  fi

  # Open lock file in the current shell so the lock outlives this function.
  if ! exec {lockfd}>"$lockfile"; then
    echo "Error: failed to open lock file '$lockfile' for writing." >&2
    return 1
  fi

  if ! flock -n "$lockfd"; then
    echo "Error: another testrun appears to be in progress." >&2
    echo "Lock '$lockfile' is held by another process; refusing to start a new testrun." >&2
    echo "If no testrun is running, simply retry (the lock is released automatically when the holding process exits)." >&2
    exec {lockfd}>&-
    return 1
  fi
}

# Verify that VIRTUAL_ENV is activated and points to .venv inside the given top dir.
# Compares canonicalized paths to tolerate symlinks and trailing slashes.
# Usage: assert_correct_venv <top_dir>
assert_correct_venv() {
  local top_dir="$1"
  local expected actual
  if ! is_venv_active; then
    echo "Error: no virtual env is activated; expected '$top_dir/.venv'." >&2
    return 1
  fi
  expected="$(readlink -f -- "$top_dir/.venv" 2>/dev/null || echo "$top_dir/.venv")"
  actual="$(readlink -f -- "$VIRTUAL_ENV" 2>/dev/null || echo "$VIRTUAL_ENV")"
  if [ "$actual" != "$expected" ]; then
    echo "Error: wrong virtual env activated: VIRTUAL_ENV='$VIRTUAL_ENV', expected '$top_dir/.venv'." >&2
    return 1
  fi
}

filter_out_nix(){
  if [[ :"${PYTHONPATH:-}": == */nix/store/* ]]; then
    # Filter out nix python packages from PYTHONPATH.
    # This avoids conflicts between nix-installed packages and python virtual environment packages.
    PYTHONPATH="$(echo "${PYTHONPATH:-}" | tr ":" "\n" | grep -v "/nix/store/.*/site-packages" | tr "\n" ":" | sed 's/:*$//' || :)"
  fi
  if [ -n "${PYTHONPATH:-}" ]; then
    export PYTHONPATH
  else
    unset PYTHONPATH
  fi
}
