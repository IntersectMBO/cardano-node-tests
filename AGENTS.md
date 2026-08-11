# AI Agent Development Guide

You are a test engineer writing end-to-end tests for the Cardano blockchain using the `cardano-node-tests` framework. The framework uses `pytest` with a cluster management system that enables parallel test execution on shared testnet cluster instances.

---

## Test Types

This repository contains two distinct kinds of tests. Always be clear about which kind you are working with, as different rules apply:

- **E2E functional tests** - the product of this repository. Located under `cardano_node_tests/tests/`. They test the Cardano node by running against a local testnet cluster. All the E2E-specific rules (cluster management, resource locking, fixture caching, Allure links, `ai_run.sh` wrapper) apply only to these tests.
- **Unit tests** - tests of the testing framework itself. Located under `framework_tests/`, plus doctests in `cardano_node_tests/utils/`. They are plain pytest tests that need no cluster and no `ai_run.sh` wrapper, and the E2E-specific rules do not apply to them.

---

## Code Organization

E2E tests are organized under:

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

Unit tests for the framework components are organized under:

- `framework_tests/` - Unit tests for cluster management, log file checking, etc.

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

### Documentation

When adding to `README.md`, keep it short, with no lengthy explanations. Instead of documenting every detail, the user-facing functionality (`make ...` targets, scripts in the runner, etc.) should handle incorrect usage itself and guide the user. Detailed guidance for AI agents belongs in `agent_docs/`.

---

## Running Tests

Before running tests, you must first open `agent_docs/running_tests.md` and follow the instructions.

---

## Writing New E2E Tests

Before writing a new E2E test from scratch, you must first open `agent_docs/new_e2e_tests.md` and follow the instructions.

---

## Commits

Any time the user asks for a commit, you must first open `agent_docs/commits.md` and confirm compliance before committing.
