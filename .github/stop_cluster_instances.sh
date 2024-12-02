#!/usr/bin/env bash

stop_instances() {
  echo "Stopping all running cluster instances"

  local workdir="${1:?}"
  for sc in "$workdir"/state-cluster*; do
    [ -d "$sc" ] || continue
    "$sc/supervisord_stop" 2>/dev/null || :
  done
}
