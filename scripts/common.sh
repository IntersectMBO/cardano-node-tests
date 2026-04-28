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
