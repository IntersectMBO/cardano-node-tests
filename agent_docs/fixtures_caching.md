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

## Real-World Example

See `cardano_node_tests/tests/test_tx_basic.py:34-78`.
