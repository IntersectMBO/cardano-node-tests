# Running Tests

Run individual tests using pytest:

```sh
pytest -k "test_minting_one_token" cardano_node_tests/
```

With full CLI command logging:

```sh
pytest -s --log-level=debug -k "test_minting_one_token" cardano_node_tests/
```

## Verifying Your Environment

Before running tests, verify:

1. **Local testnet is running** - Check with `[ -S "$CARDANO_NODE_SOCKET_PATH" ] && echo RUNNING || echo NOT RUNNING`
2. **Python Virtual environment is activated** - Check with `[ -n "$VIRTUAL_ENV" ] && echo ACTIVE || echo NOT ACTIVE`
3. **Needed binaries are available** - Check that `cardano-node` and `cardano-cli` are in your PATH
