import collections
import os
import typing as tp

import pytest
from xdist import scheduler
from xdist import workermanage
from xdist.remote import Producer

LONG_MARKER = "long"
GROUP_MARKER = "xdist_group"
SPLIT_MARKER = "xdist_split"
SPLIT_NODEID_PREFIX = "split="
# Default upper bound on parallel cluster instances when neither CLUSTERS_COUNT
# is set nor a worker count is available. Matches the framework default.
DEFAULT_MAX_CLUSTERS = 9


class OneLongScheduling(scheduler.LoadScopeScheduling):
    """Scheduling plugin with long-test balancing and split-key dispersion.

    :scope: An "xdist_group" marker value or full node id.

    Tests marked with ``@pytest.mark.long`` are tracked so that no more than one is
    scheduled per worker at a time.

    Tests marked with ``@pytest.mark.xdist_split("<key>")`` are NOT grouped onto one
    worker (unlike ``xdist_group``). Each gets its own per-test scope so the scheduler
    can reorder them independently, and the number of in-flight tests sharing a given
    split key is capped at the available cluster instance capacity
    (see ``self.clusters_count``). A test that locks multiple shared resources can
    declare several keys at once as positional args
    (e.g. ``@pytest.mark.xdist_split("governance", "plutus")``); the cap is then
    enforced independently for each key.

    Without this cap, when many tests with the same split key are collected together
    (e.g. governance tests in the same module), xdist hands them out at once to many
    workers; workers beyond the instance capacity then sit blocked in the cluster
    manager waiting for the shared resource (e.g. governance setup) to free up,
    instead of running unrelated non-conflicting tests that could share those same
    cluster instances in parallel. With the cap, only as many same-key tests are
    scheduled simultaneously as there are instances to host them, and the remaining
    workers pick non-conflicting work.

    Example: 9 cluster instances, 18 governance tests, 20 workers. Without the cap,
    18 workers all try to start governance — 9 run, the other 9 wait for instance
    capacity while 2 unrelated tests sit unassigned. With the cap, 9 workers run
    governance and the remaining 11 run non-governance tests on those same instances
    in parallel.

    :workqueue: Ordered dictionary that maps all available scopes with their
       associated tests (nodeid). Nodeids are in turn associated with their
       completion status. One entry of the workqueue is called a work unit.
       In turn, a collection of work unit is called a workload.
       ::

           workqueue = {
               '<scope>': {
                   '<full>/<path>/<to>/test_module.py::test_case1': False,
                   '<full>/<path>/<to>/test_module.py::test_case2': False,
                   (...)
               },
               (...)
           }

    :assigned_work: Ordered dictionary that maps worker nodes with their
       assigned work units.
       ::

           assigned_work = {
               '<scope>': {
                   '<full>/<path>/<to>/test_module.py': {
                       '<full>/<path>/<to>/test_module.py::test_case1': False,
                       '<full>/<path>/<to>/test_module.py::test_case2': False,
                       (...)
                   },
                   (...)
               },
               (...)
           }
    """

    def __init__(self, config: tp.Any, log: Producer | None = None) -> None:
        super().__init__(config, log)
        # Cap concurrent tests per split key by the cluster instance count:
        # explicit CLUSTERS_COUNT env wins; otherwise fall back to the xdist
        # worker count (controller-side, via self.numnodes), bounded by
        # DEFAULT_MAX_CLUSTERS; final fallback to 1.
        # PYTEST_XDIST_WORKER_COUNT is NOT used here: xdist sets it only inside
        # worker subprocesses, never on the controller where the scheduler runs.
        env_count = int(os.environ.get("CLUSTERS_COUNT") or 0)
        self.clusters_count = env_count or min(self.numnodes, DEFAULT_MAX_CLUSTERS) or 1

    def _split_scope(self, nodeid: str) -> str:
        """Determine the scope (grouping) of a nodeid.

        Example:
            example/loadsuite/test/test_gamma.py::test_beta0[param]@group_name@long
        """
        # Check the index of ']' to avoid the case: parametrize mark value has '@'
        param_end_idx = nodeid.rfind("]")
        scope_start_idx = param_end_idx if param_end_idx != -1 else 0

        if nodeid.rfind("@") <= scope_start_idx:
            return nodeid  # nodeid has neither group name nor long-running marker

        comps = nodeid[scope_start_idx:].split("@")
        has_long = comps[-1] == LONG_MARKER
        middle = comps[1:-1] if has_long else comps[1:]

        # An xdist_split marker does NOT define a group scope. Tests sharing a split key
        # remain in their own per-test scope so they can be spread across workers.
        if middle and not middle[0].startswith(SPLIT_NODEID_PREFIX):
            return middle[0]  # group scope

        return nodeid  # per-test scope (long-only, split, split+long, or unmarked)

    @staticmethod
    def _get_split_keys(nodeid: str) -> set[str]:
        """Return the set of split keys encoded in a nodeid (empty if none).

        A test can declare multiple split keys (locking multiple shared resources)
        via ``@pytest.mark.xdist_split("a", "b")``; each key is encoded as its own
        ``@split=<key>`` suffix.
        """
        param_end_idx = nodeid.rfind("]")
        scope_start_idx = param_end_idx if param_end_idx != -1 else 0
        if nodeid.rfind("@") <= scope_start_idx:
            return set()
        keys: set[str] = set()
        for comp in nodeid[scope_start_idx:].split("@")[1:]:
            if comp.startswith(SPLIT_NODEID_PREFIX):
                keys.add(comp[len(SPLIT_NODEID_PREFIX) :])
        return keys

    def _scope_split_keys(self, work_unit: dict) -> set[str]:
        """Return the split keys shared by nodeids in a work unit (empty if none)."""
        if not work_unit:
            return set()
        return self._get_split_keys(next(iter(work_unit)))

    def _get_saturated_split_keys(self) -> set[str]:
        """Return split keys with >= ``self.clusters_count`` pending tests across all workers.

        Such keys have no remaining cluster instance capacity, so additional tests
        with the key would just stall in the cluster manager. A test that declares
        multiple keys contributes to the count of each independently (each cluster
        instance has its own per-resource locks).
        """
        counts: collections.Counter[str] = collections.Counter()
        for assigned in self.assigned_work.values():
            for work_unit in assigned.values():
                for nodeid, completed in work_unit.items():
                    if completed:
                        continue
                    for key in self._get_split_keys(nodeid):
                        counts[key] += 1
        return {k for k, n in counts.items() if n >= self.clusters_count}

    @staticmethod
    def _is_long_unit(work_unit: dict) -> bool:
        """Return True if the work unit contains any long-running test."""
        return any(nid.endswith(f"@{LONG_MARKER}") for nid in work_unit)

    def _is_long_pending(self, assigned_to_node: dict) -> bool:
        """Return True if there is a long-running test pending."""
        for nodeids_dict in assigned_to_node.values():
            for nodeid, is_completed in nodeids_dict.items():
                if not is_completed and nodeid.endswith(f"@{LONG_MARKER}"):
                    return True

        return False

    def _get_short_scope(self, avoid_split_keys: tp.AbstractSet[str] = frozenset()) -> str:
        """Return first non-long work unit, preferring scopes without a conflicting split key."""
        fallback = ""
        for scope, work_unit in self.workqueue.items():
            if self._is_long_unit(work_unit):
                continue
            if avoid_split_keys and self._scope_split_keys(work_unit) & avoid_split_keys:
                fallback = fallback or str(scope)
                continue
            return str(scope)
        return fallback

    def _get_long_scope(self, avoid_split_keys: tp.AbstractSet[str] = frozenset()) -> str:
        """Return first long work unit, preferring scopes without a conflicting split key."""
        fallback = ""
        for scope, work_unit in self.workqueue.items():
            if not self._is_long_unit(work_unit):
                continue
            if avoid_split_keys and self._scope_split_keys(work_unit) & avoid_split_keys:
                fallback = fallback or str(scope)
                continue
            return str(scope)
        return fallback

    def _get_non_split_scope(self, avoid_split_keys: tp.AbstractSet[str]) -> str:
        """Return first scope whose split keys do not intersect the avoid set."""
        if not avoid_split_keys:
            return ""
        for scope, work_unit in self.workqueue.items():
            if self._scope_split_keys(work_unit) & avoid_split_keys:
                continue
            return str(scope)
        return ""

    def _assign_work_unit(self, node: workermanage.WorkerController) -> None:
        """Assign a work unit to a node."""
        assert self.workqueue

        assigned_to_node = self.assigned_work.setdefault(node, collections.OrderedDict())
        scope, work_unit = "", None

        # Check if there are any long-running tests already pending
        long_pending = self._is_long_pending(assigned_to_node)

        # Cap concurrent in-flight tests with the same split key at the cluster
        # instance capacity, so workers beyond that limit pick non-conflicting work
        # instead of stalling in the cluster manager.
        avoid_split_keys = self._get_saturated_split_keys()

        if long_pending:
            # Try to find a work unit with no long-running test if there is already a long-running
            # test pending
            scope = self._get_short_scope(avoid_split_keys=avoid_split_keys)
        else:
            # Try to find a work unit with long-running test if there is no long-running test
            # pending. We want to schedule long-running tests as early as possible
            scope = self._get_long_scope(avoid_split_keys=avoid_split_keys)

        # Long-balancing didn't pick anything; at least try to avoid split conflicts
        if not scope:
            scope = self._get_non_split_scope(avoid_split_keys=avoid_split_keys)

        if scope:
            work_unit = self.workqueue.pop(scope)

        # Grab the first unit of work if none was grabbed above
        if work_unit is None:
            scope, work_unit = self.workqueue.popitem(last=False)

        # Keep track of the assigned work
        assigned_to_node[scope] = work_unit

        # Ask the node to execute the workload
        worker_collection = self.registered_collections[node]
        nodeids_indexes = [
            worker_collection.index(nodeid)
            for nodeid, completed in work_unit.items()
            if not completed
        ]

        node.send_runtest_some(nodeids_indexes)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list) -> None:
    for item in items:
        group_marker = item.get_closest_marker(GROUP_MARKER)
        split_marker = item.get_closest_marker(SPLIT_MARKER)
        long_marker = item.get_closest_marker(LONG_MARKER)

        if not (group_marker or split_marker or long_marker):
            continue

        comps = [item.nodeid]

        # Add the group name to nodeid as suffix
        if group_marker:
            gname = (
                group_marker.args[0]
                if len(group_marker.args) > 0
                else group_marker.kwargs.get("name", "default")
            )
            comps.append(gname)

        # Add the split key(s) to nodeid as suffix. Recognized by the scheduler to spread
        # tests sharing a heavy runtime resource across workers, without grouping them.
        # Multiple keys can be supplied as separate positional args for tests that lock
        # several shared resources (e.g. ``xdist_split("governance", "plutus")``).
        if split_marker:
            skeys = split_marker.args or (split_marker.kwargs.get("name", "default"),)
            for raw in skeys:
                k = str(raw).strip()
                if k:
                    comps.append(f"{SPLIT_NODEID_PREFIX}{k}")

        # Add "long" to nodeid as suffix
        if long_marker:
            comps.append(LONG_MARKER)

        item._nodeid = "@".join(comps)


def pytest_xdist_make_scheduler(config: tp.Any, log: tp.Any) -> OneLongScheduling:
    return OneLongScheduling(config, log)
