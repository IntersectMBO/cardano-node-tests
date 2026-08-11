# db-sync Checks in Tests

Many E2E tests can verify their results against cardano-db-sync in addition to node queries. Availability of db-sync is optional, and tests need to handle both cases.

## Markers

- `@pytest.mark.dbsync` - add to any test that performs optional db-sync checks (i.e. calls `dbsync_utils.check_...` functions). The test runs even when db-sync is not available; only the db-sync checks are skipped.
- `@pytest.mark.needs_dbsync` - add to a test that depends on db-sync. The test is automatically skipped when db-sync is not available. The `dbsync` marker is added automatically to such tests, don't add it manually.

## Writing the Checks

Use the `check_...` functions from `cardano_node_tests/utils/dbsync_utils.py` (e.g. `dbsync_utils.check_tx`, `dbsync_utils.check_drep_registration`). They are the high-level interface to db-sync meant to be called directly from tests. Every `check_...` function checks db-sync availability and returns early (with `None`) when db-sync is not available, so a plain call is enough - don't wrap it in `if configuration.HAS_DBSYNC:`.

```python
tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
if tx_db_record:
    # additional assertions on the transaction record
    ...
```

Keep db-sync related code in the test body to a minimum. If a test needs non-trivial db-sync logic, add a reusable `check_...` function to `dbsync_utils` instead. Every new `check_...` function must return early when db-sync is not available - either directly (start with `if not configuration.HAS_DBSYNC:`) or via a getter it calls first (as `check_tx` does via `get_tx`). Avoid calling lower-level building blocks (`dbsync_queries`, `get_...` helpers without the guard) from tests directly - prefer adding a `check_...` function. Some older tests call `dbsync_queries` directly, but only under the `needs_dbsync` marker or an explicit `if configuration.HAS_DBSYNC:` guard.

See `_build_spend_locked_txin` in `cardano_node_tests/tests/tests_plutus/spend_build.py:370-375` for a real example (`check_tx` result tested with `if`, with `check_plutus_costs` nested inside).
