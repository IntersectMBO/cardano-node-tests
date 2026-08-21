---
name: analyze-failures
description: Triage a failed cardano-node-tests run, and answer questions about where a test run's logs and artifacts live. Use when the user asks to analyze, triage, or explain failures in a regression run, a node-upgrade run, or a run_workdir / saved run directory, and also when they ask where to find node logs, cluster logs, test artifacts, allure results or the cluster-management status database after running tests. Accepts an optional run directory argument (default `run_workdir`).
---

# Analyze test run failures

Triage a finished cardano-node-tests run and report grouped failures with a likely root cause for each group.

## 1. Resolve the run directory

`$1` (the skill argument), or `run_workdir` if no argument was given. The path may be
relative to the current working directory or absolute; it can be a fresh run produced by
`runner/regression.sh` or any saved historical run directory.

If the directory does not exist, stop and say so - do not guess another path.

## 2. Detect the testsuite

```sh
ls -d <run_dir>/allure-results-step* 2>/dev/null
```

- Any `allure-results-step*` entries -> **node-upgrade run**: follow `agent_docs/upgrade_failure_analysis_prompt.md`.
- Otherwise -> **regression run**: follow `agent_docs/failure_analysis_prompt.md`.

Say which one you detected before you start.

## 3. Run the analysis

Read the detected prompt file and follow it. Substitute the resolved run directory for every
`{RUN_DIR}` placeholder in it.

Those prompt files are the single source of truth for the analysis - the same ones the CI
workflows use - so do not restate or reinterpret their instructions here.

## 4. Report

Report the findings in the conversation and stay available for follow-up questions.

Do **not** write `failure_analysis.md`, cap the output length, or apply any other constraint
from `agent_docs/ci_analysis_prompt.md`. Those are CI-only and the workflows append them
themselves.

## Answering "where are the logs?" without analyzing

When the question is only *where something lives*, answer it and stop - do not run the
analysis. The `Inputs available under {RUN_DIR}/` list of the matching prompt file is the
authoritative map for a saved run; the two cases below say which map applies.

**A saved run directory** (produced by `runner/regression.sh`, downloaded from a CI run, or
archived by hand) - use the prompt file's inputs list. The parts people ask for most:

- test results, one JSON per test -> `allure-results/` (or `allure-results-step<N>/`)
- node logs of a cluster instance -> `testing_artifacts/pytest-*/cluster_artifacts/state-cluster<N>_<instance-id>/{bft1,pool1,pool2,pool3}.{stdout,stderr}`
- why a cluster failed to start -> `start-cluster.log`, `supervisord.log` in the same dir
- all errors at once -> `errors_all.log`
- which test held which cluster resource -> `testing_artifacts/pytest-*/cm-status.db`
- files a test itself produced (tx bodies, keys, ...) -> `testing_artifacts/pytest-*/<test_file_py>/`

**A local `./ai_run.sh pytest` run against a dev cluster** - there is no run directory:

- test artifacts are under the pytest temp dir; `$TMPDIR/pytest-of-$USER/pytest-current` is a
  symlink to the newest run (repointed by the next pytest invocation)
- cluster logs are in the *live* state dir of the running cluster, which is the parent dir of
  `$CARDANO_NODE_SOCKET_PATH` - same file names as in `cluster_artifacts/` above
- cluster artifacts are **not** copied into the pytest temp dir on a dev cluster unless
  `FORCE_SAVE_CLUSTER_ARTIFACTS` is set, so read the live state dir instead
