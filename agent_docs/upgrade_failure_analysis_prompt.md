# Preliminary Failure Analysis Prompt — Node Upgrade

You are triaging a failed cardano-node-tests **node upgrade** run. Produce a concise preliminary failure analysis.

The run directory is `{RUN_DIR}` (path is relative to the current working directory unless it is absolute). All input paths below are under that directory.

A node upgrade run executes the same smoke-test suite three times against an evolving cluster:

- **step1** — cluster built from the **base** node revision; pre-upgrade baseline.
- **step2** — cluster partially upgraded (pool3 still on base binary, other nodes on upgrade binary, old config).
- **step3** — all nodes on upgrade binary, new config + hard fork to the new protocol version.

Inputs available under `{RUN_DIR}/` (use only what exists):

- `{RUN_DIR}/allure-results-step1/`, `{RUN_DIR}/allure-results-step2/`, `{RUN_DIR}/allure-results-step3/` — one JSON per test per step (`status`, `statusDetails.message`, `statusDetails.trace`, stdout/stderr attachments listed by name)
- `{RUN_DIR}/testrun-report-step1.html`, `…-step2.html`, `…-step3.html` — self-contained HTML reports (large; prefer the per-step allure JSON above)
- `{RUN_DIR}/testing_artifacts/` — per-test artifact dirs with cluster logs, node stdouts, etc. (shared across all steps)
- `{RUN_DIR}/errors_all.log` — output of `runner/grep_errors.sh` over cluster logs (covers all steps)
- `{RUN_DIR}/scheduling.log` — cluster instance manager log

Steps:

1. Enumerate failed/broken tests per step:
   `for s in step1 step2 step3; do grep -l -E '"status": "(failed|broken)"' {RUN_DIR}/allure-results-$s/*result.json 2>/dev/null | sed "s|^|$s: |"; done`
   (`broken` = pytest error in setup/teardown; `failed` = assertion failure.)
2. For each failing test, extract `statusDetails.message` head with:
   `grep -E '"status": "(failed|broken)"' {RUN_DIR}/allure-results-step*/*result.json | cut -c1-200`.
3. Group failures by likely root cause (same exception class + message head, same node crash, same infra symptom). **Note which step(s) each group hits** — a failure that appears only in step2 or step3 is much more interesting than one that already fails in step1. Treat one node crash that flunks many tests as a single group.
4. For each group: list affected tests (truncate to ~10 with a "+N more" tail), give the most informative 1–3 lines of error context, mark the step(s) affected, and classify as one of `node-bug | test-bug | infra-flake | env-issue | upgrade-regression | unknown` with a short justification. Use `upgrade-regression` when a test passes in step1 but fails in step2 or step3 — that is the signal this workflow exists to catch.
5. Skim `{RUN_DIR}/errors_all.log` and `{RUN_DIR}/scheduling.log` for anything corroborating (node crash on restart, hard-fork failure, supervisord errors, OOM, repeated tracebacks).
6. If a whole step is missing its `allure-results-stepN/` dir, that step likely failed before pytest ran — call this out explicitly and check `errors_all.log` / the workflow log group output for the cause (commonly a `start-cluster` / `supervisord` / hard-fork failure).

Known patterns:

- **Step2 node failed to start** — typically a config-rewrite mismatch (genesis hashes, topology, `ExperimentalHardForksEnabled`) or pool3 still using `cardano-node-step1` against an incompatible config.
- **Step3 hard-fork test failure** — `test_hardfork` fails or never raises the protocol version; later tests in step3 all then fail with stale protocol params.
- **Sync stalls after restart** — `Failed to sync node` in workflow log; check whether pool1/pool3 PIDs are 0 or whether `syncProgress` never reaches `100.00`.
- **`All cluster instances are dead.`** — no cluster instance could start; usually caused by a `cardano-cli` argument change or a `cardano-node` configuration change.

Constraints:

- Output a single markdown file at: `{RUN_DIR}/failure_analysis.md`.
- Keep it under ~300 lines. No full stack traces — only the most informative line(s).
- If a source file is missing, note it once and move on; do not fail.
- Do not modify any other files.

Start now.
