from collections import OrderedDict
from typing import Any

import pytest
from xdist import scheduler
from xdist import workermanage

LONG_MARKER = "long"


class OneLongScheduling(scheduler.LoadScopeScheduling):
    """Scheduling plugin that tries to schedule no more than one long-running test per worker.

    :scope: A "xdist_group" marker value or full node id.

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

    # pylint: disable=abstract-method

    def _split_scope(self, nodeid: str) -> str:
        """Determine the scope (grouping) of a nodeid.

        Example:
            example/loadsuite/test/test_gamma.py::test_beta0[param]@group_name@long
        """
        # check the index of ']' to avoid the case: parametrize mark value has '@'
        param_end_idx = nodeid.rfind("]")
        scope_start_idx = param_end_idx if param_end_idx != -1 else 0

        if nodeid.rfind("@") <= scope_start_idx:
            return nodeid  # nodeid has neither group name nor long-running marker

        comps = nodeid[scope_start_idx:].split("@")

        if len(comps) == 3:  # nodeid has a group name and a long-running marker
            return comps[1]
        if comps[-1] == LONG_MARKER:  # nodeid has a long-running marker
            return nodeid

        return comps[1]  # nodeid has a group name

    def _is_long_pending(self, assigned_to_node: OrderedDict) -> bool:
        """Return True if there is a long-running test pending."""
        for nodeids_dict in assigned_to_node.values():
            for nodeid, is_completed in nodeids_dict.items():
                if not is_completed and nodeid.endswith(f"@{LONG_MARKER}"):
                    return True

        return False

    def _get_short_scope(self) -> str:
        """Return first non-long work unit."""
        for scope, nodeids_dict in self.workqueue.items():
            for nodeid in nodeids_dict:
                if nodeid.endswith(f"@{LONG_MARKER}"):
                    break
            else:
                return str(scope)

        return ""

    def _get_long_scope(self) -> str:
        """Return first long work unit."""
        for scope, nodeids_dict in self.workqueue.items():
            for nodeid in nodeids_dict:
                if nodeid.endswith(f"@{LONG_MARKER}"):
                    return str(scope)

        return ""

    def _assign_work_unit(self, node: workermanage.WorkerController) -> None:
        """Assign a work unit to a node."""
        assert self.workqueue

        assigned_to_node = self.assigned_work.setdefault(node, default=OrderedDict())
        scope, work_unit = None, None

        # check if there are any long-running tests already pending
        long_pending = self._is_long_pending(assigned_to_node)

        if long_pending:
            # try to find a work unit with no long-running test if there is already a long-running
            # test pending
            scope = self._get_short_scope()
            if scope:
                work_unit = self.workqueue.pop(scope)
        else:
            # Try to find a work unit with long-running test if there is no long-running test
            # pending. We want to schedule long-running tests as early as possible
            scope = self._get_long_scope()
            if scope:
                work_unit = self.workqueue.pop(scope)

        # grab the first unit of work if none was grabbed above
        if work_unit is None:
            scope, work_unit = self.workqueue.popitem(last=False)

        # keep track of the assigned work
        assigned_to_node[scope] = work_unit

        # ask the node to execute the workload
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
        group_marker = item.get_closest_marker("xdist_group")
        long_marker = item.get_closest_marker(LONG_MARKER)

        if not (group_marker or long_marker):
            continue

        comps = [item.nodeid]

        # add the group name to nodeid as suffix
        if group_marker:
            gname = (
                group_marker.args[0]
                if len(group_marker.args) > 0
                else group_marker.kwargs.get("name", "default")
            )
            comps.append(gname)

        # add "long" to nodeid as suffix
        if long_marker:
            comps.append(LONG_MARKER)

        item._nodeid = "@".join(comps)


def pytest_xdist_make_scheduler(config: Any, log: Any) -> OneLongScheduling:
    return OneLongScheduling(config, log)
