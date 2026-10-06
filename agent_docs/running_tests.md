# Running Tests

## Unit Tests

Unit tests cover the testing framework itself (`framework_tests/` plus doctests in `cardano_node_tests/utils/`). They need no running cluster and no `ai_run.sh` wrapper. Run them directly:

```sh
./.venv/bin/pytest --doctest-modules framework_tests cardano_node_tests/utils/
```

## E2E Functional Tests

E2E functional tests (everything under `cardano_node_tests/tests/`) run against a local testnet cluster. The tests are using pytest. Always use the `ai_run.sh` wrapper script to run the `pytest` command.
For example, to run the `test_minting_one_token` test:

```sh
./ai_run.sh pytest -k "test_minting_one_token" cardano_node_tests/
```

In order to see the full CLI command logging, you can add the `--log-level=debug` flag:

```sh
./ai_run.sh pytest -s --log-level=debug -k "test_minting_one_token" cardano_node_tests/
```

## Troubleshooting E2E Runs

- **"Connection refused" or tests failing on the first query.** The `ai_run.sh` guard only checks that the node socket file exists. A socket file left behind by a stopped cluster passes the check. Verify that a `cardano-node` process is running (`pgrep -af cardano-node`). If it isn't, ask the user to restart the cluster - restarting it is outside of what `ai_run.sh` allows.
- **Every tx fails with `ValidationTagMismatch ... PassedUnexpectedly` on a Dijkstra cluster.** The CLI builds Conway era txs unless told otherwise. Set the era explicitly, the same as `runner/regression.sh` does: `PROTOCOL_VERSION=12 COMMAND_ERA=dijkstra ./ai_run.sh pytest ...`. Add `TESTNET_VARIANT=leios_fast` when the cluster was started with that variant.
- **Results look wrong after changing cardonnay or cardano-clusterlib.** The tests import the installed release of these packages, not the local checkout. Use the `dep-sync` skill (if available) or install the local checkout into the virtual environment first.
