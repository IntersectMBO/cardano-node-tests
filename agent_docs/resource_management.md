# Resource Management

Several pytest workers run tests on the same cluster instance at the same time. Anything on the cluster instance that a test depends on, and that another test could change in the meantime, is a shared resource. The framework cannot detect what a test depends on - the test must declare it when requesting the cluster instance.

## The Rule

Declare every shared resource the test depends on in its `cluster_manager.get()` call:

- **`lock_resources`** - the test changes the resource. No other test can lock or use it until the test finishes.
- **`use_resources`** - the test does not change the resource, but would break if another test changed it during the test. Any number of tests can use a resource at the same time, and no test can lock it meanwhile.
- **Nothing** - only when no other test can change anything the test depends on.

"The test only reads it" is not a reason to declare nothing. Querying a pool's params, reading its VRF key or delegating to it all break when another test retires the pool, so they must `use` the pool.

The plain `cluster` fixture declares no resources. It only excludes tests that lock the whole cluster instance (`Resources.CLUSTER`).

## What Each Resource Protects

Resources are defined in `cluster_management.Resources`. Think about who is affected by the *effect* of a change, not only about which actors make it.

| Resource | Lock when the test... | Use when the test... |
| --- | --- | --- |
| `CLUSTER` | changes state of the whole instance that no other resource covers: stops or restarts nodes or services, changes topology or node config, changes the protocol version (hard fork), floods the mempool so much that other tests break. Use the `cluster_singleton` fixture. | never - every test implicitly uses it |
| `POOL1`..`POOLn`, `ALL_POOLS` | retires or re-registers a cluster pool, changes its params, owners or reward address, spends from its reward account, stops its node, rotates its KES, opcert or BLS keys | delegates to a cluster pool, reads its keys, params or state, relies on it producing blocks or voting |
| `POOL_FOR_OFFLINE` | stops a pool's block production (alias of one pool, so these tests don't take pools from others) | - |
| `RESERVES`, `TREASURY`, `REWARDS` (all three: `POTS`) | moves funds in or out of the pot, or changes how rewards are calculated | asserts exact pot balances or reward amounts |
| `DREPS`, `COMMITTEE` | enacts governance actions, changes the committee, its members or threshold, changes default DReps or their stake | votes with the default DReps or committee members, relies on their state or on the vote thresholds |
| `PLUTUS` | changes Plutus execution semantics: cost models, Plutus pparams, protocol version | runs Plutus scripts (the conftests in `tests_plutus*/` do it for every test) |
| `PERF` | generates heavy load | asserts on throughput, block space or timing |
| custom string | changes a shared on-chain object with no predefined resource, e.g. a stake credential or DRep derived from a script file - use `helpers.checksum(script_file)` | depends on such an object |

Use the existing fixtures where they fit, instead of calling `cluster_manager.get()` in every test:

- `tests/conftest.py`: `cluster_singleton`, `cluster_lock_pool`, `cluster_use_pool`, `cluster_use_committee`, `cluster_use_dreps`, `cluster_use_governance`, `cluster_lock_governance`, `cluster_lock_governance_plutus`
- `tests/delegation.py`: `cluster_and_pool` - a pool to delegate to, marked as "in use"

## Common Mistakes

Each of these has caused a race in the past.

- **Picking a pool without declaring it.** `cluster.g_query.get_stake_pools()[0]` can be a pool another test is about to retire, and `cluster_manager.cache.addrs_data[Resources.POOL1]` is not protected either. Get the pool from a fixture that marks it "in use" (`cluster_use_pool`, `delegation.cluster_and_pool`), or put the named pool into `use_resources`.
- **Locking the actors, not the effect.** A hard fork test locked only `COMMITTEE` and `DREPS` because it votes with them, but the protocol version change breaks every test on the instance - it must lock `CLUSTER`.
- **Starting the respin guard too late.** `cluster_manager.respin_on_failure()` must wrap the submit of the tx that changes shared state, not only the steps after it - submit helpers check the result after submitting, and a failure there would otherwise leave the changed state for the following tests. The same holds for `cluster_manager.set_needs_respin()`.
- **Passing shared files to commands that modify them.** Files in `cluster_manager.cache.addrs_data` (e.g. a pool's cold key counter) belong to the cluster instance. Copy such a file to the test's temp dir before passing it to a command that can update it.
- **Calling `cluster_manager.get()` more than once.** A test can request the cluster instance only once. Build all fixtures of a test on top of the same cluster fixture, otherwise the test fails or deadlocks.

## What Locking Does Not Cover

- **Epoch boundaries.** The ledger changes at every epoch boundary regardless of locks: stake distribution, enactment of governance actions, pool retirements, DRep expiry. When a test compares the results of several queries, or reads a value and then submits a tx that depends on it, first call `clusterlib_utils.wait_for_epoch_interval()` so that no epoch boundary falls in between.
- **Other users of a resource.** `use_resources` doesn't stop other tests from using the same resource: they can still vote with the default DReps or delegate to the same pool. Don't assert values that other users can change (e.g. DRep expiry, pool stake).
- **Script addresses.** Every test using the same script shares the script address. Select script UTxOs by the txid of the test's own tx, never by querying the script address or its balance.

## What Needs No Declaration

- Addresses and keys created by the test, including the ones cached with `cache_fixture` - the cache is per worker (see `fixtures_caching.md`), so no concurrent test knows them.
- Funding from the faucet - it is already serialized.

## Writing a Fixture

```python
@pytest.fixture
def cluster_lock_pool_use_rewards(
    cluster_manager: cluster_management.ClusterManager,
) -> tuple[clusterlib.ClusterLib, str]:
    """Lock any pool, mark rewards as "in use", and return the cluster instance and pool name."""
    cluster_obj = cluster_manager.get(
        # The test retires the pool
        lock_resources=[
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
        ],
        # The test checks reward amounts
        use_resources=[cluster_management.Resources.REWARDS],
    )
    pool_name = cluster_manager.get_locked_resources(
        from_set=cluster_management.Resources.ALL_POOLS
    )[0]
    return cluster_obj, pool_name
```

`resources_management.OneOf(resources=...)` selects one resource from the set that is available. Several `OneOf` filters in one request select different resources. Don't put the same resource into both `lock_resources` and `use_resources` of one request.

## Respin

When a test leaves the cluster instance in a state that cannot be reverted, the instance must be respun (restarted from scratch) before other tests can use it:

- `cleanup=True` in `cluster_manager.get()` - respin after the test, always. Combine with locking `CLUSTER`.
- `cluster_manager.set_needs_respin()` - respin once no test is running on the instance. Call it before the irreversible change, not after.
- `with cluster_manager.respin_on_failure():` - respin only when the wrapped code raises. Use it when the test itself reverts the change at the end.

## Sharing Expensive Setup

To share one expensive setup across multiple test scenarios, use pytest-subtests, or the cluster manager `mark` when subtests are not applicable. See `subtests.md`.
