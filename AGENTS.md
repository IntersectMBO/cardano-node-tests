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
2. **Use Type Hints** - All functions and methods need type hints (test return type `None` is omitted, use `import typing as tp`)
3. **Use Docstrings** - All tests, public functions, methods, classes, and modules must have Google-style docstrings. You must check that docstrings are still accurate after your code changes and update them if necessary.
4. **Run Linters** when appropriate, even for documentation-only changes:

   ```sh
   ./ai_run.sh make lint
   ```

---

## Running Tests

Before running tests, you must first open `agent_docs/running_tests.md` and follow the instructions.

---

## Writing New Tests

Before writing new test from scratch, you must first open `agent_docs/new_tests.md` and follow the instructions.

---

## Commits

Any time the user asks for a commit, you must first open `agent_docs/commits.md` and confirm compliance before committing.
