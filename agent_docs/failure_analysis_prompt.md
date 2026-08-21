# Preliminary Failure Analysis Prompt

You are triaging a failed cardano-node-tests regression run. Produce a concise preliminary failure analysis.

The run directory is `{RUN_DIR}` (path is relative to the current working directory unless it is absolute). The default run directory is `run_workdir`. All input paths below are under that directory.

Inputs available under `{RUN_DIR}/` (use only what exists):

- `{RUN_DIR}/allure-results/` — one JSON per test (status, statusDetails.message, statusDetails.trace, stdout/stderr attachments listed by name)
- `{RUN_DIR}/testing_artifacts/` — per-test artifact dirs, cluster instance artifacts and scripts
- `{RUN_DIR}/testing_artifacts/pytest-*/cluster_artifacts/state-cluster<N>_<instance-id>/` — saved state dir of one cluster instance: per-node `*.stdout`/`*.stderr` and their rotated `*.stdout.<n>`/`*.stderr.<n>` (node names come from the testnet template, e.g. `bft1` and `pool<N>` - don't assume a fixed pool count), `start-cluster.log`, `supervisord.log`, `config-*.json`, `topology-*.json`, `pparams.json`, plus the `nodes/` and `shelley/` subdirs (per-node dirs, genesis files and keys). There is one such dir per instance start, so a respun instance number has several; `<instance-id>` matches the `started cluster instance '<id>'` line in `scheduling.log` - a name carrying an extra random suffix, or one with no matching `scheduling.log` line, is a fallback name used when the instance id was unavailable or the dir name already existed
- `{RUN_DIR}/errors_all.log` — output of `runner/grep_errors.sh`: case-insensitive grep for `:error:|failed|failure` in `*.stdout`/`*.stderr` files under `testing_artifacts/`, with paths relative to that dir. Rotated logs (`*.stdout.<n>`, `*.stderr.<n>`) are **not** grepped, so a thin or empty `errors_all.log` doesn't prove there were no errors - grep the rotated logs directly when an early crash is suspected
- `{RUN_DIR}/scheduling.log` — cluster instance manager log
- `{RUN_DIR}/testrun-report.xml` — junit XML
- `{RUN_DIR}/monitor.log` — system resource snapshots every 10 min
- `{RUN_DIR}/testing_artifacts/pytest-*/cm-status.db` — cluster-management SQLite status database ("test running", resource and flag records as they were at the end of the run); query with `sqlite3 -readonly -header <db_file> 'SELECT * FROM overview ORDER BY instance_num, kind'`

Counting tests (IMPORTANT):

Run following command to get the correct counts and the list of failed/broken tests:

```sh
scripts/count_test_results.py {RUN_DIR}/allure-results
```

Run it exactly as shown, as a single plain command - do not wrap it in pipes, command substitution or other compound shell constructs, those are rejected by the CI command allowlist.

Steps:

1. Enumerate failed/broken tests from the `count_test_results.py` output above.
   (`broken` = pytest error in setup/teardown; `failed` = assertion failure.)
2. Group failures by likely root cause (same exception class + message head, same node crash, same infra symptom). Treat one node crash that flunks many tests as a single group.
3. For each group: list affected tests (truncate to ~10 with a "+N more" tail), give the most informative 1–3 lines of error context, and classify as one of `node-bug | test-bug | infra-flake | env-issue | unknown` with a short justification.
4. Skim `{RUN_DIR}/errors_all.log` and `{RUN_DIR}/monitor.log` for anything corroborating (OOM, disk pressure, repeated tracebacks).
5. When failures look cluster-management related (dead cluster instances, tests stuck waiting for resources), query the `overview` view of the status database — leftover records show which tests were running, which resources were locked and which flags (e.g. `cluster_dead`, `respin_needed`) were set when the run ended.

Known patterns:

- `All cluster instances are dead.` — no cluster instance could start; usually caused by a `cardano-cli` argument change or a `cardano-node` configuration change. Inspect `scheduling.log` and the `start-cluster.log` / `supervisord.log` / node `*.stderr` of the newest `cluster_artifacts/state-cluster*` dir for the startup failure details.
- **Testnet under constant load** — if `tx-firehose.stderr`, `tx-centrifuge.stdout` or `tx-generator.stdout` are present in a `cluster_artifacts/state-cluster*` dir, a load generator was submitting transactions for the whole run. Expect timing-sensitive failures (tx submission timeouts, full mempool, slower block/epoch progress), and attribute errors these tools log to the load generator, not to the node under test.

Constraints:

- No full stack traces — only the most informative line(s).
- If a source file is missing, note it once and move on; do not fail.
- Do not modify any other files.
