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
