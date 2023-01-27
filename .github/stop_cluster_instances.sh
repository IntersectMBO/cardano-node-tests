#!/usr/bin/env bash
# stop all running cluster instances

stop_instances() {
  local workdir="${1:?}"
  for sc in "$workdir"/state-cluster*; do
    [ -d "$sc" ] || continue
    "$sc/supervisord_stop" || true
  done
}
