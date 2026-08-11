# Writing New E2E Tests

This document applies only to E2E functional tests under `cardano_node_tests/tests/`. Unit tests for the framework itself live under `framework_tests/` and are plain pytest tests.

Organize tests in classes that group related functionality.

## Resource Management

When tests modify or use shared resources (stake pools, treasury, reserves, DReps, Plutus spending scripts), use custom fixtures with proper resource locking. Open `agent_docs/resource_management.md` and follow the instructions.

## Fixture Caching

Cache expensive fixture resources (addresses, keys, scripts) to avoid recreation on every test. Open `agent_docs/fixtures_caching.md` and follow the instructions.

## E2E Tests with Expensive Setup

Reuse expensive setups (governance actions, etc.) across multiple scenarios using pytest-subtests. Open `agent_docs/subtests.md` and follow the instructions.

## db-sync Checks

When test results (transactions, registrations, governance actions) can be verified in db-sync, open `agent_docs/dbsync.md` and follow the instructions.

## Pytest Markers

Mark tests based on where they can run and how long they take:

- `@pytest.mark.testnets` - add when the test can run on public testnets like Preview. The test cannot depend on crossing an epoch boundary - waiting for the next epoch would take too long there.
- `@pytest.mark.long` - add when the test runs for a long time even on local testnets, typically because it crosses several epoch boundaries.
- `@pytest.mark.smoke` - add when the test finishes under 1 minute. Smoke tests are selected for quick regression and upgrade testing runs, so unmarked fast tests silently drop out of those runs.

The full list of markers is in `pyproject.toml`. For db-sync related markers, see `agent_docs/dbsync.md`. The `xdist_group` marker is described in `agent_docs/subtests.md`. The `xdist_split` marker spreads tests that lock the same scarce cluster resource across xdist workers - it is orthogonal to `long` (wallclock).

## Summary Checklist

When writing a new E2E test, ensure:

- [ ] Test is in a class grouping related functionality
- [ ] `@allure.link(helpers.get_vcs_link())` decorator is present
- [ ] Test has comprehensive docstring with steps and expectations
- [ ] Type hints are included for all parameters
- [ ] `common.get_test_id(cluster)` is used for unique naming
- [ ] Appropriate pytest markers are set (see Pytest Markers above)
- [ ] db-sync checks are added where results are verifiable in db-sync (see `agent_docs/dbsync.md`)
- [ ] Code follows Google Python Style Guide
- [ ] Linters pass
