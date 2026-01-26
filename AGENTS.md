# AGENTS.md - AI Agent Development Guide

This guide provides instructions for AI agents working with the cardano-node-tests repository, assuming the development environment is already activated.

---

## 🧪 Running Individual Tests

When the development environment is activated, you can run individual tests using pytest.

### Basic Test Execution

Run a specific test by name:

```sh
pytest -k "test_minting_one_token" cardano_node_tests/tests/tests_plutus
```

Run multiple tests matching a pattern:

```sh
pytest -k "test_missing_tx_out or test_multiple_same_txins" cardano_node_tests
```

Run tests with a specific marker:

```sh
pytest -m smoke cardano_node_tests/tests/test_cli.py
```

### Debugging and Verbose Output

Enable full CLI command logging:

```sh
pytest -s --log-level=debug -k test_minting_one_token cardano_node_tests/tests/tests_plutus
```

### Test Organization

Tests are organized under:

- `cardano_node_tests/tests/` - Main test directory
- `cardano_node_tests/tests/tests_plutus/` - Plutus-specific tests for all Plutus versions
- `cardano_node_tests/tests/tests_plutus_v2/` - Plutus-specific tests for PlutusV2
- `cardano_node_tests/tests/tests_plutus_v3/` - Plutus-specific tests for PlutusV3
- `cardano_node_tests/tests/tests_conway/` - Conway era specific tests
- `cardano_node_tests/tests/data/` - Test data files

### Framework files

- `cardano_node_tests/cluster_management/` - Cluster management utilities for managing local testnet clusters
- `cardano_node_tests/utils/` - Utility functions for tests and the framework
- `cardano_node_tests/pytest_plugins/` - Pytest plugins, namely a custom pytest-xdist scheduler

---

## 🔧 Making Code Changes

### Coding Guidelines

1. **Follow the Google Python Style Guide**
   - See: <https://google.github.io/styleguide/pyguide.html>

2. **Use Type Hints**
   - All functions and methods should have type hints for parameters and return types
   - Test functions and methods should also include type hints, with the exception that return type `None` is omitted

3. **Use Ruff for Formatting**
   - Formatting is enforced via pre-commit hooks
   - Ruff handles both linting and formatting

4. **Run Linters**

   ```sh
   make lint
   ```

### Making Changes to Dependencies

If you modify dependencies in `pyproject.toml`, update the lockfile:

```sh
make update-lockfile
```

---

## 📝 Testing Workflow

1. **Make your code changes** following the Google Python Style Guide

2. **Run relevant tests** to verify your changes:

   ```sh
   pytest -k "your_test_pattern" cardano_node_tests
   ```

3. **Run linters** to ensure code quality:

   ```sh
   make lint
   ```

## Changing Markdown and ReStructuredText Documentation

1. **Make your changes**

2. **Run Linters to check formatting**:

   ```sh
   make lint
   ```

---

## 🎯 Common Test Patterns

### Running Tests by Category

By marker:

```sh
pytest -m smoke cardano_node_tests
```

By test path:

```sh
pytest cardano_node_tests/tests/test_cli.py
```

Framework tests:

```sh
pytest framework_tests/
```

### Running Tests with Custom Configuration

Filter by multiple conditions:

```sh
pytest -k "test_stake_pool_low_cost or test_reward_amount" cardano_node_tests
```

With marker exclusion:

```sh
pytest -m "not long" cardano_node_tests
```

---

## 🔍 Verifying Your Environment

Before running tests, verify:

1. **Local testnet is running** - Check with `[ -S "$CARDANO_NODE_SOCKET_PATH" ] && echo RUNNING || echo NOT RUNNING`
2. **Python Virtual environment is activated** - Check with `[ -n "$VIRTUAL_ENV" ] && echo ACTIVE || echo NOT ACTIVE`
3. **Needed binaries are available** - Check that `cardano-node` and `cardano-cli` are in your PATH

---

## 🔐 Cluster Management and Resource Locking

The test framework uses a sophisticated cluster management system that allows multiple tests to run in parallel on shared cluster instances while coordinating access to shared resources.

### How Cluster Management Works

**Pool of Cluster Instances**: The framework can run multiple cluster instances concurrently (configured via `CLUSTERS_COUNT`). Each test worker requests a cluster instance to run a test on.

**Coordination via File System**: Workers communicate through status files created in a shared temporary directory. These files act as locks and signals indicating:

- Which test is running on which cluster instance
- Which resources are locked or in use
- When a cluster needs to be respun (restarted to clean state)

**Resource Management**: Tests declare what resources they need. The `ClusterManager` ensures only one test uses exclusive resources at a time.

**Cluster Respin**: Some tests modify cluster state irreversibly. These tests can request a "respin" to re-initialize the cluster to a clean state.

### Available Resources

Resources that are defined in `cluster_management.Resources`:

- **`CLUSTER`** - Whole cluster instance (used by every test, can be locked for exclusive access)
- **`POOL1`, `POOL2`, `POOL3`, `ALL_POOLS`** - Individual stake pools or tuple of all pools
- **`POOL_FOR_OFFLINE`** - Reserved pool for tests where pool stops producing blocks
- **`PLUTUS`** - Plutus script resources
- **`RESERVES`** - Reserve pot
- **`TREASURY`** - Treasury pot
- **`REWARDS`** - Rewards pot
- **`POTS`** - Tuple of all pots (RESERVES, TREASURY, REWARDS)
- **`PERF`** - Performance testing resources
- **`DREPS`** - DRep resources

Other shared resources can exist, like Plutus scripts, when the same script is used in multiple tests.

### Resource Locking vs. Using

**`lock_resources`**: Exclusive access. No other test can use these resources until the current test finishes. Use when a test will modify the resource state.

**`use_resources`**: Shared access. Multiple tests can use the same resource, but the framework tracks usage. Use when a test only reads or makes non-conflicting changes. Other tests cannot lock the resource while it's in use.

### Writing Tests with Resource Management

#### Basic Pattern - Using the `cluster` Fixture

```python
def test_something(cluster: clusterlib.ClusterLib):
    # Basic usage - cluster automatically manages common resources
    # No explicit resource declaration needed for simple tests
    ...
```

#### Custom Fixtures with Resource Requirements

**Lock specific resources** (exclusive access):

```python
@pytest.fixture
def cluster_lock_pool_and_pots(
    cluster_manager: cluster_management.ClusterManager,
) -> tuple[clusterlib.ClusterLib, str]:
    cluster_obj = cluster_manager.get(
        lock_resources=[
            *cluster_management.Resources.POTS,
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
        ]
    )
    pool_name = cluster_manager.get_locked_resources(
        from_set=cluster_management.Resources.ALL_POOLS
    )[0]
    return cluster_obj, pool_name
```

**Use resources** (shared access):

```python
@pytest.fixture
def cluster_use_pool_and_rewards(
    cluster_manager: cluster_management.ClusterManager,
) -> tuple[clusterlib.ClusterLib, str]:
    cluster_obj = cluster_manager.get(
        use_resources=[
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
            cluster_management.Resources.REWARDS,
        ]
    )
    pool_name = cluster_manager.get_used_resources(
        from_set=cluster_management.Resources.ALL_POOLS
    )[0]
    return cluster_obj, pool_name
```

**Lock multiple pools** (e.g., for pool interaction tests):

```python
@pytest.fixture
def cluster_lock_two_pools(
    cluster_manager: cluster_management.ClusterManager,
) -> tuple[clusterlib.ClusterLib, str, str]:
    cluster_obj = cluster_manager.get(
        lock_resources=[
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
        ]
    )
    pool_names = cluster_manager.get_locked_resources(
        from_set=cluster_management.Resources.ALL_POOLS
    )
    return cluster_obj, pool_names[0], pool_names[1]
```

**Lock shared Plutus script**:

```python
@pytest.fixture
def cluster_lock_stake_script(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Make sure just one staking Plutus test run at a time.

    Plutus script always has the same address. When one script is used in multiple
    tests that are running in parallel, the balances etc. don't add up.
    """
    cluster_obj = cluster_manager.get(
        lock_resources=[helpers.checksum(plutus_common.STAKE_PLUTUS_V2)],
        use_resources=[cluster_management.Resources.PLUTUS],
    )
    return cluster_obj
```

### The `OneOf` Filter

`resources_management.OneOf` selects one available resource from a set:

```python
# Select any available pool
resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS)

# Use OneOf multiple times to select different resources
[
    resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
    resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
]
# This will select two DIFFERENT pools from ALL_POOLS
```

The filter automatically handles:

- Resources already locked by other tests
- Resources already selected by previous filters in the same request
- Returning empty list if no resource is available (test will wait)

### Best Practices

1. **Use `lock_resources` when modifying state**: If your test changes pool rewards, treasury, or other shared state, lock those resources.

2. **Use `use_resources` for read-only or non-conflicting operations**: If you only query data or make isolated changes, use shared access.

3. **Be specific about resource needs**: Only lock/use resources you actually need to minimize test blocking.

4. **Request respin when needed**: If your test leaves cluster in unusable state:

   ```python
   cluster_manager.set_needs_respin()
   ```

5. **Use custom fixtures**: Create reusable fixtures for common resource patterns rather than repeating `cluster_manager.get()` calls.

6. **Check resource usage**: Use `cluster_manager.get_locked_resources()` or `cluster_manager.get_used_resources()` to see what was allocated.

### Example: Real-World Test Pattern

```python
@pytest.fixture
def cluster_and_pool(
    cluster_manager: cluster_management.ClusterManager,
) -> tuple[clusterlib.ClusterLib, str]:
    """Get cluster instance and select one pool."""
    cluster_obj = cluster_manager.get(
        use_resources=[
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
        ]
    )
    pool_name = cluster_manager.get_used_resources(
        from_set=cluster_management.Resources.ALL_POOLS
    )[0]
    return cluster_obj, pool_name


def test_stake_pool_rewards(
    cluster_and_pool: tuple[clusterlib.ClusterLib, str],
):
    """Test staking rewards with proper resource management."""
    cluster, pool_name = cluster_and_pool

    # Test implementation using the selected pool
    # Multiple tests can run concurrently, each using different pools
    ...
```

### Troubleshooting Resource Issues

**Test hangs indefinitely**: Check if all required resources are available. Use `SCHEDULING_LOG` environment variable to see cluster scheduling decisions, if available.

**"Manager is already initialized" error**: Don't use multiple `cluster` fixtures in the same test. Use a single custom fixture that requests all needed resources at once.
