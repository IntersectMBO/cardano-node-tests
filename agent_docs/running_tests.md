# Running Tests

The tests are using pytest. Always use the `ai_run.sh` wrapper script to run the `pytest` command.
For example, to run the `test_minting_one_token` test:

```sh
./ai_run.sh pytest -k "test_minting_one_token" cardano_node_tests/
```

In order to see the full CLI command logging, you can add the `--log-level=debug` flag:

```sh
./ai_run.sh pytest -s --log-level=debug -k "test_minting_one_token" cardano_node_tests/
```
