---
name: analyze-failures
description: Triage a failed cardano-node-tests run, and answer questions about where a test run's logs and artifacts live. Use when the user asks to analyze, triage, or explain failures in a regression run, a node-upgrade run, a run_workdir, a saved run directory or artifacts downloaded from a CI run, and also when they ask where to find node logs, cluster logs, test artifacts, allure results or the cluster-management status database after running tests. Accepts an optional run directory argument (default `run_workdir`).
---

# Analyze test run failures

Triage a finished cardano-node-tests run and report grouped failures with a likely root cause
for each group.

## 1. Resolve the run directory

The directory passed as the skill argument, or `run_workdir` if none was given. The path may be
relative to the current working directory or absolute; it can be a fresh run produced by
`runner/regression.sh`, artifacts downloaded from a CI run, or any saved historical run
directory.

If the directory does not exist, stop and say so - do not guess another path.

## 2. Unpack tarballs if needed

A run directory produced locally holds both the unpacked result dirs and their `*.tar.xz`
archives (`runner/create_results.sh` keeps both). Artifacts **downloaded from a CI run** hold
only the archives. Each archive contains a single top-level directory of the same name, so
unpack any archive whose directory is missing before reading anything:

```sh
tar -xf <run_dir>/testing_artifacts.tar.xz -C <run_dir>
```

## 3. Detect the testsuite

Match on results present as a directory **or** as a tarball:

- `allure-results-step*` -> **node-upgrade run**: follow `agent_docs/upgrade_failure_analysis_prompt.md`.
- otherwise `allure-results` -> **regression run**: follow `agent_docs/failure_analysis_prompt.md`.
- neither -> do **not** default to the regression prompt. An upgrade run that died before
  step1's pytest produces no results at all and would be misread as a regression run. Report
  what the directory does contain (`testrun-report-step*.html` and three `testing_artifacts/pytest-*`
  dirs point at an upgrade run) and ask which testsuite it was.

Say which one you detected before you start.

## 4. Run the analysis

Read the detected prompt file and follow it. Substitute the resolved run directory for every
`{RUN_DIR}` placeholder in it.

Those prompt files are the single source of truth for the analysis - the same ones the CI
workflows use - so do not restate or reinterpret their instructions here.

## 5. Report

Report the findings in the conversation and stay available for follow-up questions.

Do **not** write `failure_analysis.md`, cap the output length, or apply any other constraint
from `agent_docs/ci_analysis_prompt.md`. Those are CI-only and the workflows append them
themselves.

## Answering "where are the logs?" without analyzing

When the question is only *where something lives*, answer it and stop - do not run the
analysis.

**A saved run directory** (produced by `runner/regression.sh`, downloaded from a CI run, or
archived by hand) - answer from the `Inputs available under {RUN_DIR}/` list of the matching
prompt file, which maps every log, results dir and status database. Unpack archives first as in
step 2.

**A local `./ai_run.sh pytest` run against a dev cluster** - there is no run directory:

- test artifacts are under the pytest temp dir;
  `${TMPDIR:-${TEMP:-${TMP:-/tmp}}}/pytest-of-${LOGNAME:-$USER}/pytest-current` is a symlink to
  the newest run, repointed by the next pytest invocation
- cluster logs are in the *live* state dir of the running cluster, which is the parent dir of
  `$CARDANO_NODE_SOCKET_PATH` - same file names as in `cluster_artifacts/` above
- cluster artifacts are **not** copied into the pytest temp dir on a dev cluster unless
  `FORCE_SAVE_CLUSTER_ARTIFACTS` is set, so read the live state dir instead
