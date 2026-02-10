# Writing New Tests

Organize tests in classes that group related functionality.

## Resource Management

When tests modify or use shared resources (stake pools, treasury, reserves, DReps, Plutus spending scripts), use custom fixtures with proper resource locking. Open `agent_docs/resource_management.md` and follow the instructions.

## Fixture Caching

Cache expensive fixture resources (addresses, keys, scripts) to avoid recreation on every test. Open `agent_docs/fixtures_caching.md` and follow the instructions.

## Tests with Expensive Setup

Reuse expensive setups (governance actions, etc.) across multiple scenarios using pytest-subtests. Open `agent_docs/subtests.md` and follow the instructions.

## Summary Checklist

When writing a new test, ensure:

- [ ] Test is in a class grouping related functionality
- [ ] `@allure.link(helpers.get_vcs_link())` decorator is present
- [ ] Test has comprehensive docstring with steps and expectations
- [ ] Type hints are included for all parameters
- [ ] `common.get_test_id(cluster)` is used for unique naming
- [ ] Code follows Google Python Style Guide
- [ ] Linters pass
