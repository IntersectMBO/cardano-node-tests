# Running Selected Tests on a Managed Cluster

Use this procedure when selected E2E tests need their own disposable cluster, especially
tests that respin the cluster or use custom startup scripts. The framework starts the
cluster when a test requests it, saves artifacts, and stops it at session teardown.
No full regression launcher or rebuild of the development environment is needed.

Read [Running Tests](running_tests.md) first. This is different from running against an
already started dev cluster: `.source.dev` normally points at that cluster and disables
framework management. Override those settings **after** sourcing it.

## Isolation Requirements

- Use a new, short absolute run directory, such as one under `$HOME/tmp/opencode`.
  Unix socket paths have length limits, so avoid deeply nested directories.
- Point `CARDANO_NODE_SOCKET_PATH` at `$RUN/cluster/state-cluster0/bft1.socket`, not the
  dev cluster socket. The framework derives its cluster work/state directories from
  this path. The dev cluster and managed run can both use instance number 0 because
  their directory roots and ports are different.
- Give pytest a separate `$RUN/pytest` directory with `--basetemp`. Cluster-manager
  locks and its status database are scoped to that pytest run. Keep it separate from
  `$RUN/cluster`: pytest clears `--basetemp` on startup. Never reuse a live run's paths.
- Choose an unused `PORTS_BASE`, such as `26000` when the dev cluster uses `23000`.
  With the default three pools, one instance reserves a 100-port block, here
  `26000` through `26099`. More pools or cluster instances need more blocks. Use a
  different block, such as `27000`, for another simultaneous managed run.
- Check ports **before** starting. Cluster startup cleanup can terminate processes on
  assigned ports, not merely fail to bind. Current local scripts use a Unix supervisor
  socket, but cleanup also checks the legacy supervisor TCP port `12001` for instance
  0, independently of `PORTS_BASE`. Do not run if that port belongs to another service.
- Disable inherited db-sync/SMASH settings unless you separately isolate their databases
  and ports. `PORTS_BASE` alone does not isolate those external services.

For the single-instance example below, this should show no listeners:

```sh
ss -ltnp '( sport = :12001 or ( sport >= :26000 and sport <= :26099 ) )'
```

Do not treat an empty listing as a reservation: coordinate port blocks with other runs.
This isolates cluster state and services, not CPU, memory, or disk bandwidth. Heavy
concurrent runs can still affect the dev testnet's timing.

## Run Selected Tests

Run from the repository root. This example selects an existing Plutus test on
`local_fast`, using Conway protocol version 11. Adjust the variant, era, and selection
for the tests you need. Required node/CLI binaries and services must already be on
`PATH`; inspect their resolved paths and versions rather than assuming a node revision.

The subshell keeps environment overrides out of the user's interactive shell:

```sh
(
  set -e
  source .source.dev

  RUN="$(mktemp -d "$HOME/tmp/opencode/cnt.XXXXXX")"
  printf 'Run directory: %s\n' "$RUN"
  mkdir -p "$RUN/cluster" "$RUN/tmp"

  unset DEV_CLUSTER_RUNNING FORBID_RESPIN BOOTSTRAP_DIR KEEP_CLUSTERS_RUNNING
  unset DBSYNC_SCHEMA_DIR SMASH PYTEST_ADDOPTS
  unset BLOCK_PRODUCTION_DB SCHEDULING_LOG
  unset ENABLE_TX_GENERATOR ENABLE_TX_FIREHOSE ENABLE_TX_CENTRIFUGE

  export CARDANO_NODE_SOCKET_PATH="$RUN/cluster/state-cluster0/bft1.socket"
  export TMPDIR="$RUN/tmp"
  export PORTS_BASE=26000 CLUSTERS_COUNT=1
  export TESTNET_VARIANT=local_fast PROTOCOL_VERSION=11 COMMAND_ERA=conway

  command -v cardano-node cardano-cli
  cardano-node --version
  cardano-cli --version

  pytest cardano_node_tests/tests/tests_plutus/test_mint_build.py \
    -k test_minting_one_token \
    --basetemp="$RUN/pytest" --junitxml="$RUN/results.xml" \
    -s --log-level=DEBUG > "$RUN/pytest.log" 2>&1
)
```

- Create `$HOME/tmp/opencode` first if it does not exist. Keep the printed run path for
  inspecting results or cleaning up an interrupted run; `RUN` is local to the subshell.
- Clearing `DEV_CLUSTER_RUNNING` enables managed startup; clearing `FORBID_RESPIN`
  permits respins and custom scripts. Neither is safe without the isolated paths/ports.
- Replace the file and `-k` expression with your selection, or use a full pytest node ID.
  This invokes pytest directly, so launcher options such as `PYTEST_ARGS`, `CI_ARGS`,
  and `MARKEXPR` are not used. Check the collected and skipped tests in `pytest.log`.
- For Leios activity tests, replace the variant/era exports with
  `export TESTNET_VARIANT=leios_fast PROTOCOL_VERSION=12 COMMAND_ERA=dijkstra`, then add
  `export ENABLE_TX_FIREHOSE=true TX_TPS=15` before pytest. Ensure `tx-firehose` is on `PATH`;
  add the directory containing that binary explicitly if necessary. Not every Leios/BLS
  test supports this variant; follow its fixture and epoch-wait requirements.
- Use the installed dependencies deliberately. A local cardonnay/clusterlib checkout
  is not automatically imported. Follow `dep-sync` when validating dependency changes;
  do not rebuild or replace shared editable installations just to run these tests.
- Allow enough execution time for cluster startup and the selected tests. A short tool
  timeout can kill pytest before teardown, even when individual tests have bounded waits.

## Results and Cleanup

- Console/CLI logs: `$RUN/pytest.log`; JUnit results: `$RUN/results.xml` after completion.
- Test artifacts and cluster-manager state: `$RUN/pytest/`, including `cm-status.db`.
- Saved cluster logs/configs: `$RUN/pytest/cluster_artifacts/state-cluster0_*`.
- While running, live node logs are under `$RUN/cluster/state-cluster0/`. Normal teardown
  saves artifacts and removes the managed state directory unless clusters are kept running.

After a hard interruption, inspect the printed run's state directory and stop only that
cluster. The generated stop script reads `CARDANO_NODE_SOCKET_PATH`, so set it explicitly
even if the current shell is configured for the dev cluster:

```sh
(
  source .source.dev
  RUN="$HOME/tmp/opencode/cnt.EXAMPLE"  # Replace with the printed run directory.
  CARDANO_NODE_SOCKET_PATH="$RUN/cluster/state-cluster0/bft1.socket" \
    "$RUN/cluster/state-cluster0/stop-cluster"
)
```

If normal teardown already removed the state directory, there is no stop script to run.
Preserve needed logs before deleting run artifacts. Never use a broad `pkill cardano-node`,
an unqualified `supervisorctl`, or the dev cluster's `make stop-cluster` for this cleanup.
