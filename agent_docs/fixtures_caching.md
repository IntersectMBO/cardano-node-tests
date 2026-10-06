# Fixtures Caching

The framework provides caching to avoid recreating expensive fixtures repeatedly, critical for performance when running tests in parallel across multiple workers and cluster instances.

## How Caching Works

### Cache Scope: Per Worker + Per Cluster Instance

The cache is **local to each pytest worker** and **partitioned by cluster instance number**:

```python
# From cache.py
class CacheManager:
    """Set of cache management methods."""

    # Every pytest worker has its own cache
    cache: tp.ClassVar[dict[int, ClusterManagerCache]] = {}
```

Each worker maintains a dictionary:

- **Key**: Cluster instance number (0, 1, 2, etc.)
- **Value**: `ClusterManagerCache` with cached fixture values

This means:

- Worker A on Cluster Instance 0 has its own cache
- Worker B on Cluster Instance 1 has a separate cache
- Worker A switching to Cluster Instance 1 uses a different cache
- Same worker on same cluster instance reuses cached values

## The `cache_fixture` Context Manager

Use `cluster_manager.cache_fixture()` to cache fixture values:

1. **First test** on worker + cluster instance: Creates resources, stores in `fixture_cache.value`
2. **Subsequent tests** on same worker + cluster instance: Retrieves cached resources
3. **Different cluster instance**: Cache miss, creates new resources for that instance

## Why Cached Fixtures Must Be Function-Scoped

**Critical Rule**: Cached fixtures must use `@pytest.fixture` with default function scope.

**Reason**: Tests sharing fixtures can be scheduled on different cluster instances. Function scope ensures:

- Fixture is called for each test
- Each test on different instance gets correct cached value for that instance
- Tests on same worker + same instance still benefit from caching

## Caching Keys

Without a `key`, `cache_fixture()` uses the file name and line number of the fixture that calls it, so every fixture gets its own cache entry. Pass an explicit `key` only when the default doesn't fit:

- Inside a helper called from several fixtures, the line number of the helper would be shared by all callers. The helper must take the key from the caller - this is why the address helpers in `cardano_node_tests/tests/addrs_common.py` take `caching_key`, and fixtures pass `caching_key=helpers.get_current_line_str()`.
- When one fixture caches different values for different parameters, include the parameter in the key (e.g. `caching_key=f"plutusv3_builtins_batch_testing_{variant}"`), otherwise all variants get the value cached for the first one.

Prefer the `addrs_common` helpers (`get_payment_addrs`, `get_payment_addr`, etc.) over calling `cache_fixture` directly for funded addresses. With `caching_key`, they create the addresses once and re-fund them on later calls when the balance drops below `min_amount`. When only `amount` is passed, the addresses are funded once and never re-funded, so a cached address can run out of funds over a long run.

## Sharing a Caching Key

Tests that use the same `caching_key` share the cached value only when they run one after another on the same worker and cluster instance. A worker runs one test at a time, so a cached value is never used by two tests at once, and sharing a key is safe for parallel runs.

Cached addresses still carry state from the earlier tests: leftover UTxOs, a different balance, transactions that are still pending. Don't assert the exact balance of a cached address; check the outputs of the test's own transaction instead.

## Real-World Example

See `cardano_node_tests/tests/test_tx_basic.py:34-78`.
