# Resource Management

The test framework enables parallel test execution on shared cluster instances while coordinating access to shared resources.

## How It Works

**Pool of Cluster Instances**: The framework runs multiple cluster instances concurrently. Each test worker requests a cluster instance.

**Resource Management**: Tests declare required resources. The `ClusterManager` ensures exclusive resources are used by only one test at a time.

**Cluster Respin**: Tests can request a "respin" to re-initialize cluster to a clean state after irreversible modifications.

## Available Resources

Common resources are defined in `cluster_management.Resources`.

Other shared resources include Plutus scripts when the same script is used across multiple tests.

## Resource Locking vs. Using

**`lock_resources`**: Exclusive access. No other test can use these resources until the current test finishes. Use when modifying resource state.

**`use_resources`**: Shared access. Multiple tests can use the same resource simultaneously. Use for read-only or non-conflicting operations. Other tests cannot lock the resource while it's in use.

## Writing Tests with Resource Management

### Basic Pattern - Using the `cluster` Fixture

```python
def test_something(cluster: clusterlib.ClusterLib):
    # Basic usage - cluster automatically manages common resources
    # No explicit resource declaration needed for simple tests
    ...
```

### Custom Fixtures with Resource Requirements

```python
@pytest.fixture
def cluster_lock_pool_and_pots(
    cluster_manager: cluster_management.ClusterManager,
) -> tuple[clusterlib.ClusterLib, list[str], str]:
    cluster_obj = cluster_manager.get(
        # Lock specific resources (exclusive access)
        lock_resources=[
            *cluster_management.Resources.POTS,
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
        ],
        # Use resources (shared access)
        use_resources=[
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
            cluster_management.Resources.REWARDS,
        ]
    )
    locked_pools_names = cluster_manager.get_locked_resources(
        from_set=cluster_management.Resources.ALL_POOLS
    )
    used_pool_name = cluster_manager.get_used_resources(
        from_set=cluster_management.Resources.ALL_POOLS
    )[0]
    return cluster_obj, locked_pools_names, used_pool_name
```

## The `OneOf` Filter

`resources_management.OneOf` selects one available resource from a set:

```python
# Select any available pool
resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS)

# Select multiple different resources
[
    resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
    resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
]
# This selects two DIFFERENT pools from ALL_POOLS
```

The filter handles:

- Resources locked by other tests
- Resources already selected by previous filters in the same request
- Returning empty list if no resource is available (test will wait)

## Best Practices

1. **Lock resources when modifying state** - Lock resources if your test changes pool rewards, treasury, or other shared state

2. **Use resources for read-only operations** - Use shared access when only querying data or making isolated changes

3. **Be specific about needs** - Only lock/use resources you actually need to minimize test blocking

4. **Use custom fixtures** - Create reusable fixtures for common patterns instead of repeating `cluster_manager.get()` calls
