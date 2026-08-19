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

# Check that the given path is a usable executable: a regular non-empty file
# (or a symlink to one) with the executable bit set. Rules out directories,
# dangling symlinks, empty files and symlinks to directories (e.g. a
# `nix build -o` result pointing to a store output dir).
is_usable_binary() {
  local bin="${1:?Missing binary path}"
  [ -f "$bin" ] && [ -s "$bin" ] && [ -x "$bin" ]
}

# Report that an existing binary (given as an absolute path, typically in the
# `.bin` dir) will be used instead of a built one, and log its version
# (best effort) for traceability.
# Fails when the binary cannot run at all (e.g. wrong architecture, missing
# dynamic libraries, or crashing on startup), because such a binary would
# still shadow working ones on PATH.
report_existing_binary() {
  local bin="${1:?Missing binary path}"
  local rc=0 ver
  echo "Using existing binary '$bin'"
  # Probe from `/` so a binary that writes state files to its cwd on startup
  # cannot pollute the caller's working directory.
  ver="$(cd / && timeout -k 5 10 "$bin" --version </dev/null 2>&1)" || rc="$?"
  ver="${ver%%$'\n'*}"
  # 124 is a `--version` timeout and 137 its SIGKILL escalation (both
  # tolerated, same as a binary that doesn't support `--version`); other
  # 125+ statuses mean the binary could not run (126/127) or died from
  # a signal (128+N), or `timeout` itself failed (125).
  if [ "$rc" -ge 125 ] && [ "$rc" -ne 137 ]; then
    echo "Error: '$bin' is not runnable (exit status ${rc}): ${ver:-no output}" >&2
    return 1
  fi
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    ver="unknown ('--version' timed out)"
  elif [ "$rc" -ne 0 ]; then
    ver="unknown ('--version' exited ${rc}: ${ver:-no output})"
  fi
  echo "  version: ${ver:-unknown}"
}

# Assert that all given commands are available on PATH and resolve to usable
# binaries.
# Usage: assert_cmds_available <command> [<command> ...]
assert_cmds_available() {
  local cmd bin found_all=1
  for cmd in "$@"; do
    if ! bin="$(command -v "$cmd")"; then
      echo "Error: required command '$cmd' not found on PATH." >&2
      found_all=0
    elif ! is_usable_binary "$bin"; then
      echo "Error: required command '$cmd' resolves to unusable '$bin'." >&2
      found_all=0
    fi
  done
  [ "$found_all" -eq 1 ]
}

# Assert that `DESELECT_FROM_FILE`, if set to a non-empty value, points to an
# existing file. An unset or empty value means "deselect nothing" and is not an
# error, so only a typo can make this fail.
assert_deselect_file() {
  [ -n "${DESELECT_FROM_FILE:-}" ] || return 0
  if [ ! -f "$DESELECT_FROM_FILE" ]; then
    echo "Error: deselect file '$DESELECT_FROM_FILE' not found." >&2
    echo "Use 'DESELECT_FROM_FILE=' to run without deselecting any tests." >&2
    return 1
  fi
}

# Acquire an exclusive, non-blocking lock tied to the given workdir to prevent
# concurrent testruns from clobbering each other's workdir. The lock is held
# for the lifetime of the calling shell; it is released automatically on exit.
# The lock file lives next to the workdir so that wiping the workdir does not
# drop the lock.
# If <fd_var_name> is given, the lock FD number is written into a variable of
# that name in the caller's scope. Long-lived background processes spawned by
# the caller can then close the inherited FD (e.g. `exec {LOCK_FD}>&-`) so the
# lock is released even if they outlive the parent shell.
# Usage: acquire_workdir_lock <workdir> [<fd_var_name>]
acquire_workdir_lock() {
  local workdir="${1:?acquire_workdir_lock requires a workdir argument}"
  # Internal local is prefixed with `_aw_` to avoid shadowing the caller's
  # output variable, which is passed by name.
  local _aw_out_var="${2:-}"
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

  if [ -n "$_aw_out_var" ]; then
    printf -v "$_aw_out_var" '%s' "$lockfd"
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

get_node_version() {
  local version _
  read -r _ version _ < <(cardano-node --version 2>/dev/null)
  [ -n "$version" ] || return 1
  printf '%s\n' "$version"
}

version_parse() {
  # Limitation: minor and patch must be < 1000, else they overflow into the next field.
  local v="${1:?"Missing version"}"
  local major minor patch
  IFS=. read -r major minor patch <<< "$v"
  major="${major%%[!0-9]*}"
  minor="${minor%%[!0-9]*}"
  patch="${patch%%[!0-9]*}"
  printf '%d\n' "$((10#${major:-0} * 1000000 + 10#${minor:-0} * 1000 + 10#${patch:-0}))"
}

# Send SIGTERM, wait up to 5s for exit, escalate to SIGKILL if still alive.
# Returns 0 if the process is gone, 1 if it survived even SIGKILL.
# Note: `wait` only reaps direct children of the calling shell; for non-children
# the function still returns correctly but cannot harvest the zombie.
kill_and_wait() {
  local pid="${1:?Missing PID}"
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  if ! kill "$pid"; then
    kill -0 "$pid" 2>/dev/null || return 0
    return 1
  fi
  for _ in {1..5}; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 1
  done
  echo "Warning: process $pid did not exit after SIGTERM, sending SIGKILL." >&2
  kill -9 "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true  # reap if it's a child, ignore error if it's not
  kill -0 "$pid" 2>/dev/null && return 1
  return 0
}
