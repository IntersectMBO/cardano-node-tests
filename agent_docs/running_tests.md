# Running Tests

## Unit Tests

Unit tests cover the testing framework itself (`framework_tests/` plus doctests in `cardano_node_tests/utils/`). They need no running cluster. Run them directly:

```sh
./.venv/bin/pytest --doctest-modules framework_tests cardano_node_tests/utils/
```

Never add unit tests for code under `cardano_node_tests/tests/`, including test-specific helpers and fixtures, even if the unit tests would live in `framework_tests/`. Validate that code through E2E functional tests instead.

Never create unit tests for shell scripts.

## E2E Functional Tests

E2E functional tests (everything under `cardano_node_tests/tests/`) run against a local testnet cluster using pytest. Load the development environment with `source .source` (or `source .source.dev`) first. Ensure the cluster is running, `CARDANO_NODE_SOCKET_PATH` points to its socket, and `cardano-node` and `cardano-cli` are available on `PATH`.
For example, to run the `test_minting_one_token` test:

```sh
pytest -k "test_minting_one_token" cardano_node_tests/
```

In order to see the full CLI command logging, you can add the `--log-level=debug` flag:

```sh
pytest -s --log-level=debug -k "test_minting_one_token" cardano_node_tests/
```

## Troubleshooting E2E Runs

- **"Connection refused" or tests failing on the first query.** A socket file can be left behind by a stopped cluster. Verify that a `cardano-node` process is running (`pgrep -af cardano-node`). If it isn't, ask the user to restart the cluster.
- **Every tx fails with `ValidationTagMismatch ... PassedUnexpectedly` on a Dijkstra cluster.** The CLI builds Conway era txs unless told otherwise. Set the era explicitly, the same as `runner/regression.sh` does: `PROTOCOL_VERSION=12 COMMAND_ERA=dijkstra pytest ...`. Add `TESTNET_VARIANT=leios_fast` when the cluster was started with that variant.
- **Results look wrong after changing cardonnay or cardano-clusterlib.** The tests import the installed release of these packages, not the local checkout. Use the `dep-sync` skill (if available) or install the local checkout into the virtual environment first.
