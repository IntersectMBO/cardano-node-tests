# Preliminary Failure Analysis Prompt

You are triaging a failed cardano-node-tests regression run. Produce a concise preliminary failure analysis.

The run directory is `{RUN_DIR}` (path is relative to the current working directory unless it is absolute). The default run directory is `run_workdir`. All input paths below are under that directory.

Inputs available under `{RUN_DIR}/` (use only what exists):

- `{RUN_DIR}/allure-results/` — one JSON per test (status, statusDetails.message, statusDetails.trace, stdout/stderr attachments listed by name)
- `{RUN_DIR}/testing_artifacts/` — per-test artifact dirs with cluster logs, node stdouts, etc.
- `{RUN_DIR}/errors_all.log` — output of `runner/grep_errors.sh` over cluster logs
- `{RUN_DIR}/scheduling.log` — cluster instance manager log
- `{RUN_DIR}/testrun-report.xml` — junit XML
- `{RUN_DIR}/monitor.log` — system resource snapshots every 10 min

Counting tests (IMPORTANT):

Run following code snippet to get the correct counts:

```sh
passed=$(grep -l '"status": "passed"' {RUN_DIR}/allure-results/*result.json | wc -l)
failed=$(grep -l '"status": "failed"' {RUN_DIR}/allure-results/*result.json | wc -l)
broken=$(grep -l '"status": "broken"' {RUN_DIR}/allure-results/*result.json | wc -l)
total=$(grep -l '"message": "Skipped: collected, not run"' {RUN_DIR}/allure-results/*result.json | wc -l)
if [ "$total" -gt 0 ]; then
  skipped=$(( total - passed - failed - broken ))
else
  skipped=$(grep -l '"status": "skipped"' {RUN_DIR}/allure-results/*result.json | wc -l)
  total=$(( passed + failed + broken + skipped ))
fi
echo "Total: $total, Passed: $passed, Failed: $failed, Broken: $broken, Skipped: $skipped"
```

Steps:

1. Enumerate failed/broken tests:
   `grep -E '"status": "(failed|broken)"' {RUN_DIR}/allure-results/*result.json | cut -c1-200`.
   (`broken` = pytest error in setup/teardown; `failed` = assertion failure.)
2. Group failures by likely root cause (same exception class + message head, same node crash, same infra symptom). Treat one node crash that flunks many tests as a single group.
3. For each group: list affected tests (truncate to ~10 with a "+N more" tail), give the most informative 1–3 lines of error context, and classify as one of `node-bug | test-bug | infra-flake | env-issue | unknown` with a short justification.
4. Skim `{RUN_DIR}/errors_all.log` and `{RUN_DIR}/monitor.log` for anything corroborating (OOM, disk pressure, repeated tracebacks).

Known patterns:

- `All cluster instances are dead.` — no cluster instance could start; usually caused by a `cardano-cli` argument change or a `cardano-node` configuration change. Inspect `scheduling.log` for the cluster startup failure details.

Constraints:

- No full stack traces — only the most informative line(s).
- If a source file is missing, note it once and move on; do not fail.
- Do not modify any other files.
