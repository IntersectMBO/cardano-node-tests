#!/usr/bin/env bash

# re-run `niv update` in case of failure
niv_update() {
  local retval=3

  for _ in {1..3}; do
    if niv update "$@"; then
      return 0
    else
      retval="$?"
      sleep 2
    fi
  done

  return "$retval"
}
