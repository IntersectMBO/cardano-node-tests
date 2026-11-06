# Investigating mempool latency

`MempoolTxTooSlow` measures wall-clock validation latency, including GC and scheduling
delays. Local submissions use this rejection for both soft and hard timeouts. An empty
mempool does not rule out CPU starvation.

## Resource diagnostics

`runner/regression.sh` samples `monitor.log` every 60 seconds by default. Override with
`MONITOR_INTERVAL=10` for short incidents. Snapshots include:

- Host CPU, memory, disk and CPU pressure.
- Node PIDs, lifetime-average CPU usage, thread counts and main-thread context switches.
- `/proc/<pid>/task/*/schedstat`: CPU runtime, runnable queue wait, and timeslices,
  summed over live threads, including RTS workers. Compare snapshots for the same PID;
  thread exits/restarts can reset counters, so sums are not strictly monotonic.
- Cgroup v2 CPU quotas, throttling counters and pressure at visible node/ancestor groups.
  Rising `nr_throttled`/`throttled_usec` points at quota contention. Namespace-hidden
  groups and cgroup v1 cannot be fully diagnosed with these snapshots.
- GC, allocation, heap, pause and CPU metrics exposed by each configured local
  Prometheus endpoint. Names and availability depend on the node/tracing configuration;
  absent metrics are not evidence that there were no GC pauses.
  Both legacy `hasPrometheus` and newer `PrometheusSimple` backends are discovered.

Scrapes are best-effort, capped at two seconds per endpoint. Sampling interval is the
sleep between snapshots; collecting a snapshot adds some time. These are diagnostic
counters, not an automatic overload detector or a reason to relax node protection.

## Controlled reruns

Reduce concurrency explicitly, without changing repository defaults:

```sh
TEST_THREADS=2 MAX_TESTS_PER_CLUSTER=2 MONITOR_INTERVAL=10 ./runner/regression.sh
```

To rerun selected tests on an existing dev cluster, source its environment and use
`MAX_TESTS_PER_CLUSTER=2 pytest -n 2 ...`. Keep the original workload and lower-load
results separate; a passing lower-load run does not prove the contention was fixed.

Read-only query timeouts are separate from submission rejections. Do not retry an
entire transaction-building operation or extend CLI timeouts indiscriminately. A
transport timeout can leave submission status unknown; existing confirmation logic
must resolve it before any resubmission.
