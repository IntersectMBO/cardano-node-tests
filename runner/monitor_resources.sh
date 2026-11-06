#!/usr/bin/env bash
# Best-effort node and cgroup diagnostics, sourced by regression.sh's resource monitor.

monitor_node_resources() {
  local workdir="$1" pid cg cgdir config port metric hierarchy cgpath
  local task runtime wait_ns timeslices total_runtime total_wait total_timeslices threads
  local -A cgroups=([/sys/fs/cgroup]=1)

  echo "--- NODE PROCESSES (CPU is lifetime average) ---"
  ps -C cardano-node -o pid,ppid,pcpu,pmem,nlwp,stat,etimes,time,args || true
  while read -r pid; do
    echo "--- NODE PID $pid ---"
    # Include the RTS worker threads, not just the process leader's schedstat.
    total_runtime=0 total_wait=0 total_timeslices=0 threads=0
    for task in "/proc/$pid/task/"[0-9]*; do
      [ -r "$task/schedstat" ] || continue
      if read -r runtime wait_ns timeslices < "$task/schedstat"; then
        total_runtime=$((total_runtime + runtime))
        total_wait=$((total_wait + wait_ns))
        total_timeslices=$((total_timeslices + timeslices))
        threads=$((threads + 1))
      fi
    done
    echo "schedstat (live-thread sums: runtime_ns wait_ns timeslices; threads=$threads):"
    echo "$total_runtime $total_wait $total_timeslices"
    echo "RSS, thread count, and main-thread context switches:"
    grep -E '^(VmRSS|Threads|voluntary_ctxt_switches|nonvoluntary_ctxt_switches):' "/proc/$pid/status" || true

    cg=""
    if [ -r "/proc/$pid/cgroup" ]; then
      while IFS=: read -r hierarchy _ cgpath; do
        if [ "$hierarchy" = 0 ]; then
          cg="$cgpath"
          break
        fi
      done < "/proc/$pid/cgroup"
    fi
    # Cgroup namespaces can hide the host path. The visible root is still sampled below.
    if [[ "$cg" = /* && "$cg" != *"/.."* ]] && [ -d "/sys/fs/cgroup$cg" ]; then
      cgdir="/sys/fs/cgroup${cg%/}"
      while [[ "$cgdir" = /sys/fs/cgroup* ]]; do
        cgroups["$cgdir"]=1
        [ "$cgdir" != /sys/fs/cgroup ] || break
        cgdir="${cgdir%/*}"
      done
    else
      echo "Process cgroup not visible in this namespace: $cg"
    fi
  done < <(pgrep -x cardano-node || true)

  echo "--- HOST CPU PRESSURE ---"
  if [ -r /proc/pressure/cpu ]; then cat /proc/pressure/cpu; fi
  for cgdir in "${!cgroups[@]}"; do
    for metric in cpu.max cpu.stat cpu.pressure memory.events; do
      if [ -r "$cgdir/$metric" ]; then
        echo "--- CGROUP $cgdir/$metric (counters are cumulative) ---"
        cat "$cgdir/$metric" || true
      fi
    done
  done

  # Poll configured local Prometheus ports, not guessed port offsets. Missing/older metrics
  # endpoints are harmless; never let a slow endpoint stall the monitoring loop indefinitely.
  for config in "$workdir"/state-cluster*/config-*.json; do
    [ -f "$config" ] || continue
    while read -r port; do
      echo "--- NODE GC/CPU METRICS $config (port $port) ---"
      curl --fail --silent --show-error --max-time 2 "http://127.0.0.1:$port/metrics" |
        grep -Ei '^[a-zA-Z_:][a-zA-Z0-9_:]*(gc|heap|cpu|mutator|alloc|pause)[a-zA-Z0-9_:]*({|[[:space:]])' || true
    done < <(jq -r '[
      (.hasPrometheus?[1]? // empty),
      (.TraceOptions[]?.backends[]? | strings |
        capture("^PrometheusSimple [^ ]+ (?<port>[0-9]+)$")? | .port | tonumber)
    ] | unique[] | select(type == "number") |
      select(. > 0 and . < 65536 and floor == .)' "$config" 2>/dev/null)
  done
}
