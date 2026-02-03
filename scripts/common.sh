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
