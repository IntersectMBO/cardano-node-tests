# Using pytest-subtests for Expensive Setup Reuse

Use pytest-subtests to share expensive setup across multiple test scenarios. When
subtests are not applicable, use marked tests instead (see the last section).

## Pattern 1: Method-Based Subtests

```python
@allure.link(helpers.get_vcs_link())
def test_stake_pool_costs(
    self,
    cluster_manager: cluster_management.ClusterManager,
    cluster_expensive: clusterlib.ClusterLib,
    subtests: pytest_subtests.SubTests,
):
    """Test various pool cost configurations.

    Run multiple scenarios with different pool costs while reusing
    the expensive cluster setup.
    """
    cluster = cluster_expensive
    temp_template = common.get_test_id(cluster)

    def _test_pool_cost(pool_cost: int) -> None:
        """Test a specific pool cost value."""
        rand_str = clusterlib.get_rand_str(4)
        name_template = f"{temp_template}_{rand_str}"

        # Register and test pool with specific cost
        pool_id = register_stake_pool(
            cluster_obj=cluster,
            name_template=name_template,
            pool_cost=pool_cost,
        )
        # Assertions and verifications
        ...

    # Run multiple cost scenarios
    for cost in (500, 340_000_000, 9_999_999_999):
        with subtests.test(pool_cost=cost):
            _test_pool_cost(cost)
```

## Pattern 2: Generator-Based Subtests

For complex test suites with many scenarios:

```python
def get_test_scenarios() -> tp.Generator[tp.Callable, None, None]:
    """Generate test scenarios for governance guardrails."""

    def test_tx_fee_per_byte(cluster_with_constitution: ClusterRecord):
        """Test txFeePerByte guardrail."""
        # Scenario implementation
        ...

    yield test_tx_fee_per_byte

    def test_max_tx_size(cluster_with_constitution: ClusterRecord):
        """Test maxTxSize guardrail."""
        # Scenario implementation
        ...

    yield test_max_tx_size

    # Add more scenarios...


class TestGovernanceGuardrails:
    """Test governance guardrails with constitution."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_guardrails(
        self,
        cluster_with_constitution: ClusterRecord,
        subtests: pytest_subtests.SubTests,
    ):
        """Test governance guardrails using plutus script constitution.

        Run multiple guardrail scenarios on a single expensive cluster setup
        with a registered constitution.
        """
        common.get_test_id(cluster_with_constitution.cluster)

        for scenario in get_test_scenarios():
            with subtests.test(scenario=getattr(scenario, "__name__", "")):
                scenario(cluster_with_constitution)
```

## When Subtests Are Not Applicable: Marked Tests

Subtests don't work with `hypothesis` property based tests. A group of such tests that
needs the same expensive setup (e.g. a cluster started with custom scripts) can instead
pass `mark="<name>"` to `cluster_manager.get()`. All tests with the same mark run on the
same cluster instance and the setup is done only once.

The mark is considered abandoned and cleaned up when no marked test is running for a while.
Therefore always combine the cluster manager mark with `@pytest.mark.xdist_group` of the
same name. The custom xdist scheduler (`pytest_plugins.xdist_scheduler`) schedules tests
with the same `xdist_group` as one work unit on a single pytest worker, so the marked tests
run back-to-back and the assigned "marked" cluster instance is reused instead of being
cleaned up and prepared again.

```python
@pytest.fixture
def cluster_mincost(
    cluster_manager: cluster_management.ClusterManager, pool_cost_start_cluster: pl.Path
) -> clusterlib.ClusterLib:
    return cluster_manager.get(
        mark="minPoolCost",
        lock_resources=[cluster_management.Resources.CLUSTER],
        prio=True,
        cleanup=True,
        scriptsdir=pool_cost_start_cluster,
    )


@pytest.mark.xdist_group("minPoolCost")
class TestPoolCost: ...
```
