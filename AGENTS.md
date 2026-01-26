# AI Agent Development Guide

You are a test engineer writing end-to-end tests for the Cardano blockchain using the `cardano-node-tests` framework. The framework uses `pytest` with a cluster management system that enables parallel test execution on shared testnet cluster instances.

---

## Code Organization

Tests are organized under:

- `cardano_node_tests/tests/` - Main test directory
- `cardano_node_tests/tests/tests_plutus/` - Plutus-specific tests for all Plutus versions
- `cardano_node_tests/tests/tests_plutus_v2/` - Plutus-specific tests for PlutusV2+
- `cardano_node_tests/tests/tests_plutus_v3/` - Plutus-specific tests for PlutusV3+
- `cardano_node_tests/tests/tests_conway/` - Conway era specific tests
- `cardano_node_tests/tests/data/` - Test data files

Framework components are organized under:

- `cardano_node_tests/cluster_management/` - Cluster management utilities
- `cardano_node_tests/utils/` - Utility functions
- `cardano_node_tests/pytest_plugins/` - Pytest plugins (custom pytest-xdist scheduler)

---

## Making Code Changes

### Coding Guidelines

1. **Follow the Google Python Style Guide**
2. **Use Type Hints** - All functions and methods need type hints (test return type `None` is omitted)
3. **Use Docstrings** - All tests, public functions, methods, classes, and modules must have Google-style docstrings
4. **Run Linters** (even for documentation-only changes):

   ```sh
   make lint
   ```

---

## Running Tests

See `agent_docs/running_tests.md` for instructions on running tests.

---

## Writing New Tests

When writing new test from scratch, see `agent_docs/new_tests.md` for guidelines.
