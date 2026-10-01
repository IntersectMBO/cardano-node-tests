# Writing and Changing E2E Tests

This document applies only to E2E functional tests under `cardano_node_tests/tests/`. Unit tests for the framework itself live under `framework_tests/` and are plain pytest tests.

The guidelines apply to writing new tests and also to larger changes of existing tests (refactoring, extending, moving tests or fixtures). When changing an existing test, bring the parts you touch in line with these guidelines, but don't rewrite unrelated code just to comply.

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

## Epoch Waits

Epoch length differs between testnets - some local testnet variants (e.g. `leios_fast`) have epochs several times longer than the default `local_fast` ones. A test that waits for epochs must not take too long on any of them:

- Call `common.skip_on_long_epochs(cluster_obj=cluster, epochs=N)` at the start of the test body, before any real work. `N` is the worst-case number of epochs the test waits for. The test is skipped when `N` times the epoch length exceeds `common.MAX_EPOCHS_WAIT_SEC` (the value differs between local and real testnets).
- When only part of the test needs the epoch waits (e.g. a final check after the next epoch), guard that part with `if common.is_epochs_wait_ok(cluster_obj=cluster, epochs=N):` instead of skipping the whole test.
- Pass `max_wait_sec` to either helper only for selected tests where a longer runtime is tolerated. It overrides the default limit on both local and real testnets.
- If the epoch waits happen in a test-specific fixture, call the helper in that fixture, so the skip happens before the waiting. Waits proportional to blocks can be converted to epochs using `activeSlotsCoeff * epoch_length` blocks per epoch.
- Cluster fixtures that start a cluster instance from custom startup scripts (custom `scriptsdir`) must call `common.skip_unless_local_fast()` before `cluster_manager.get()`. Tests using such fixtures run only with the known `local_fast` epoch length, so they don't need `skip_on_long_epochs`. The exception are tests that need a different testnet variant (e.g. `leios_fast` for observing Leios EBs). Their fixtures check the generated genesis for what the test needs instead, and the tests keep `skip_on_long_epochs`.

Counting the worst-case `N`:

- `cluster.wait_for_new_epoch(new_epochs=k)` and `cluster.wait_for_epoch(epoch_no=current + k)` count as `k`. Loops that wait for an epoch in each iteration count once per iteration.
- `clusterlib_utils.wait_for_epoch_interval()` waits only when the current time is already past `stop`. A wide window (e.g. `start=5, stop=common.EPOCH_STOP_SEC_BUFFER`) costs well under a minute and counts as 0. A narrow window near the end of an epoch (e.g. `common.EPOCH_START_SEC_LEDGER_STATE` to `common.EPOCH_STOP_SEC_LEDGER_STATE`) counts as 1.
- Include epoch waits in helper functions the test calls (governance ratification and enactment, waiting for rewards, etc.), in test-specific fixtures and in finalizers. Values derived from genesis (e.g. `cluster.conway_genesis["govActionLifetime"]`) or from the current cluster state (e.g. `clusterlib_utils.get_epochs_to_rewards()`) can be used directly.

## Summary Checklist

When writing a new E2E test, or making larger changes to an existing one, ensure:

- [ ] Test is in a class grouping related functionality
- [ ] `@allure.link(helpers.get_vcs_link())` decorator is present
- [ ] Test has comprehensive docstring with steps and expectations
- [ ] Type hints are included for all parameters
- [ ] `common.get_test_id(cluster)` is used for unique naming
- [ ] Appropriate pytest markers are set (see Pytest Markers above)
- [ ] Tests that wait for epochs call `common.skip_on_long_epochs` (see Epoch Waits above)
- [ ] db-sync checks are added where results are verifiable in db-sync (see `agent_docs/dbsync.md`)
- [ ] Code follows Google Python Style Guide
- [ ] Linters pass
