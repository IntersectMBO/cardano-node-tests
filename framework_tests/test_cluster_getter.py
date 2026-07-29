"""Tests for `cluster_management.cluster_getter`."""

import time

import pytest

from cardano_node_tests.cluster_management import cluster_getter
from cardano_node_tests.cluster_management import resources
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.cluster_management import status_db


def _get_cluster_getter(num_of_instances: int = 1) -> cluster_getter.ClusterGetter:
    """Return a `ClusterGetter` instance suitable for unit testing.

    Requires the `db_dir` fixture - the constructor reads the pytest root temp dir.
    """
    return cluster_getter.ClusterGetter(
        worker_id="gw0",
        pytest_config=None,  # type: ignore[arg-type]
        num_of_instances=num_of_instances,
        log_func=lambda _msg: None,
    )


def _get_cget_status(instance_num: int, mark: str = "") -> cluster_getter._ClusterGetStatus:
    """Return a `_ClusterGetStatus` instance suitable for unit testing."""
    return cluster_getter._ClusterGetStatus(
        mark=mark,
        lock_resources=[],
        use_resources=[],
        prio=False,
        cleanup=False,
        scriptsdir="",
        current_test="test_x",
        instance_num=instance_num,
    )


def _populate_marked(instance_num: int = 0, mark: str = "markA") -> None:
    """Create status records of a marked group of tests."""
    status_db.create_curr_mark(instance_num=instance_num, worker_id="gw0", mark=mark)
    status_db.create_respin_after_mark(instance_num=instance_num, worker_id="gw0", mark=mark)
    status_db.create_resources(
        instance_num=instance_num,
        worker_id="gw0",
        names=["pool1"],
        mode=status_db.MODE_LOCK,
        mark=mark,
    )
    status_db.create_resources(
        instance_num=instance_num,
        worker_id="gw0",
        names=["pool2"],
        mode=status_db.MODE_USE,
        mark=mark,
    )
    # A marked record left by another worker of the same group
    status_db.create_resources(
        instance_num=instance_num,
        worker_id="gw1",
        names=["pool5"],
        mode=status_db.MODE_LOCK,
        mark=mark,
    )


@pytest.mark.usefixtures("db_dir")
def test_rm_marks_removes_marked_resources():
    """Check that removing marks of an instance removes also the marked resource records.

    With the "current mark" records gone, the mark staleness handling cannot see the
    marks anymore, so marked resource records left behind would stay locked or in-use
    for the rest of the testrun. Marked records of all workers are removed, unmarked
    records and records of other instances are left alone.
    """
    getter = _get_cluster_getter()

    _populate_marked(instance_num=0, mark="markA")
    # Unmarked resource records and records of other instances are left alone
    status_db.create_resources(
        instance_num=0, worker_id="gw1", names=["pool3"], mode=status_db.MODE_LOCK
    )
    status_db.create_resources(
        instance_num=1, worker_id="gw2", names=["pool4"], mode=status_db.MODE_LOCK, mark="markB"
    )

    getter._rm_marks(instance_num=0)

    assert status_db.list_curr_mark(instance_num=0) == []
    assert status_db.list_respin_after_mark(instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) == ["pool3"]
    assert status_db.get_resource_names(mode=status_db.MODE_USE, instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=1) == ["pool4"]


@pytest.mark.usefixtures("db_dir")
def test_rm_marks_single_mark():
    """Check that removing a single mark leaves records of other marks alone."""
    getter = _get_cluster_getter()

    _populate_marked(instance_num=0, mark="markA")
    status_db.create_curr_mark(instance_num=0, worker_id="gw2", mark="markB")
    status_db.create_respin_after_mark(instance_num=0, worker_id="gw2", mark="markB")
    status_db.create_resources(
        instance_num=0, worker_id="gw2", names=["pool6"], mode=status_db.MODE_LOCK, mark="markB"
    )

    getter._rm_marks(instance_num=0, mark="markA")

    assert status_db.list_curr_mark(instance_num=0, mark="markA") == []
    assert len(status_db.list_curr_mark(instance_num=0, mark="markB")) == 1
    assert len(status_db.list_respin_after_mark(instance_num=0, mark="markB")) == 1
    assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) == ["pool6"]


@pytest.mark.usefixtures("db_dir")
def test_rm_marks_rejects_empty_mark():
    """Check that removing records of an empty mark is rejected.

    An empty mark would match the unmarked resource records held by running tests.
    """
    getter = _get_cluster_getter()

    with pytest.raises(ValueError, match="must be"):
        getter._rm_marks(instance_num=0, mark="")


@pytest.mark.usefixtures("db_dir")
def test_on_marked_test_stop():
    """Check that finished marked group converts its "respin after mark" record.

    The "respin after mark" record is removed by `_rm_marks` and converted to
    "needs respin" from its return value, so the promised post-group respin happens
    even though the mark's status records are already gone.
    """
    getter = _get_cluster_getter()
    _populate_marked(instance_num=0, mark="markA")

    getter._on_marked_test_stop(instance_num=0, mark="markA")

    assert len(status_db.list_respin_needed(instance_num=0)) == 1
    assert status_db.list_respin_after_mark(instance_num=0) == []
    assert status_db.list_curr_mark(instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_USE, instance_num=0) == []


@pytest.mark.usefixtures("db_dir")
def test_on_marked_test_stop_no_respin_promise():
    """Check that finished marked group without a "respin after mark" doesn't respin."""
    getter = _get_cluster_getter()
    _populate_marked(instance_num=0, mark="markA")
    status_db.rm_respin_after_mark(instance_num=0, mark="markA")

    getter._on_marked_test_stop(instance_num=0, mark="markA")

    assert status_db.list_respin_needed(instance_num=0) == []
    assert status_db.list_curr_mark(instance_num=0) == []


@pytest.mark.usefixtures("db_dir")
def test_cleanup_dead_clusters_removes_marks():
    """Check that dead cluster instance cleanup removes marks and marked resources."""
    getter = _get_cluster_getter()
    cget_status = _get_cget_status(instance_num=0)
    cget_status.selected_instance = 0
    cget_status.respin_here = True
    cget_status.respin_ready = True

    _populate_marked(instance_num=0, mark="markA")

    getter._cleanup_dead_clusters(cget_status)

    assert cget_status.selected_instance == -1
    assert cget_status.respin_here is False
    assert cget_status.respin_ready is False
    assert status_db.list_curr_mark(instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_USE, instance_num=0) == []


@pytest.mark.usefixtures("db_dir")
def test_init_respin_own_mark():
    """Check that a marked test that triggers respin re-resolves its resources.

    When a non-initial marked test initiates a respin, the mark status records -
    including the group's resource records - are removed. The test must then be treated
    as the initial test of the mark again, so its resources get resolved and created
    anew. Otherwise the group would keep the cluster instance (the "current mark" record
    gets re-created) while holding no resource records at all.
    """
    getter = _get_cluster_getter()
    cget_status = _get_cget_status(instance_num=0, mark="markA")
    cget_status.cluster_needs_respin = True

    _populate_marked(instance_num=0, mark="markA")
    cget_status.marked_ready_rows = status_db.list_curr_mark(instance_num=0, mark="markA")
    assert cget_status.marked_ready_rows

    # The test cannot continue in this iteration - it must re-evaluate the instance
    # as the initial test of the mark
    assert getter._init_respin(cget_status) is False

    assert cget_status.marked_ready_rows == ()
    assert cget_status.respin_here is True
    assert cget_status.selected_instance == 0
    assert len(status_db.list_respin_progress(instance_num=0)) == 1
    # The respin stays scheduled even when the re-evaluation gets delayed
    assert len(status_db.list_respin_needed(instance_num=0)) == 1
    assert status_db.list_curr_mark(instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_USE, instance_num=0) == []

    # The re-evaluation re-creates the mark's records; the second `_init_respin` call
    # must short-circuit on `respin_here` and not wipe them again
    _populate_marked(instance_num=0, mark="markA")
    assert getter._init_respin(cget_status) is True
    assert len(status_db.list_curr_mark(instance_num=0)) == 1
    assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) != []
    assert len(status_db.list_respin_progress(instance_num=0)) == 1


@pytest.mark.usefixtures("db_dir")
def test_marked_select_instance_respinner_not_blocked():
    """Check that a respinning worker is not blocked by its mark on another instance.

    The worker's own mark records were removed when it initiated the respin, so another
    worker of the group could have claimed the mark on another instance in the meantime.
    The respinning worker is pinned to its instance and must proceed as the first test
    of the mark instead of waiting for the other instance forever.
    """
    getter = _get_cluster_getter()
    cget_status = _get_cget_status(instance_num=0, mark="markA")
    status_db.create_curr_mark(instance_num=1, worker_id="gw1", mark="markA")
    cget_status.marked_running_my_anywhere = status_db.list_curr_mark(mark="markA")
    assert cget_status.marked_running_my_anywhere

    # Without the pending respin, the mark on the other instance blocks this worker
    assert getter._marked_select_instance(cget_status) is False

    # The respinning worker proceeds as the first test of the mark
    cget_status.respin_here = True
    cget_status.selected_instance = 0  # `respin_here` always comes with the pinned instance
    assert getter._marked_select_instance(cget_status) is True


@pytest.mark.usefixtures("db_dir")
def test_init_respin_tests_running():
    """Check that respin is not initiated while tests are running on the instance.

    Nothing can be wiped and no respin state set - the worker must wait for the
    running tests to finish first.
    """
    getter = _get_cluster_getter()
    cget_status = _get_cget_status(instance_num=0, mark="markA")
    cget_status.cluster_needs_respin = True

    _populate_marked(instance_num=0, mark="markA")
    status_db.create_test_running(instance_num=0, worker_id="gw1", test_id="test_x")
    cget_status.started_tests_rows = status_db.list_test_running(instance_num=0)

    assert getter._init_respin(cget_status) is False

    assert cget_status.respin_here is False
    assert status_db.list_respin_progress(instance_num=0) == []
    assert len(status_db.list_curr_mark(instance_num=0)) == 1
    assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) != []


@pytest.mark.usefixtures("db_dir")
def test_init_respin_foreign_marks():
    """Check that respin init removes foreign marks and continues.

    When the respinning test doesn't belong to any marked group present on the
    instance, the marks are removed and the respin continues in the same iteration.
    """
    getter = _get_cluster_getter()
    cget_status = _get_cget_status(instance_num=0)
    cget_status.cluster_needs_respin = True

    _populate_marked(instance_num=0, mark="markA")

    assert getter._init_respin(cget_status) is True

    assert cget_status.respin_here is True
    assert status_db.list_curr_mark(instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) == []


@pytest.mark.usefixtures("db_dir")
class TestInstancesOrder:
    """Check the iteration order over cluster instances."""

    def test_light_test_starts_at_tail(self):
        """Check that a light test iterates the two tail instances first, in fixed order."""
        getter = _get_cluster_getter(num_of_instances=9)
        order = getter._make_instances_order(
            available_instances=list(range(9)),
            lock_resources=[],
            use_resources=[resources.Resources.CLUSTER],
        )
        assert order[:2] == (7, 8)
        assert sorted(order) == list(range(9))

    def test_heavy_test_starts_at_head(self):
        """Check that a test locking resources iterates the tail instances last."""
        getter = _get_cluster_getter(num_of_instances=9)
        order = getter._make_instances_order(
            available_instances=list(range(9)),
            lock_resources=[resources.Resources.CLUSTER],
            use_resources=[resources.Resources.CLUSTER],
        )
        assert order[-2:] == (7, 8)
        assert sorted(order) == list(range(9))

    def test_multiple_use_resources_is_heavy(self):
        """Check that a test using more than one resource is not treated as light."""
        getter = _get_cluster_getter(num_of_instances=9)
        order = getter._make_instances_order(
            available_instances=list(range(9)),
            lock_resources=[],
            use_resources=[resources.Resources.CLUSTER, resources.Resources.POOL1],
        )
        assert order[-2:] == (7, 8)

    def test_single_instance(self):
        """Check the order with a single cluster instance."""
        getter = _get_cluster_getter(num_of_instances=1)
        order = getter._make_instances_order(
            available_instances=[0],
            lock_resources=[],
            use_resources=[resources.Resources.CLUSTER],
        )
        assert order == (0,)

    def test_head_is_randomized(self):
        """Check that the head instances are iterated in a randomized order.

        The randomization spreads workers across the head instances. With 7 head
        instances, the chance of 10 identical samples in a row is (1/7!)^9 - a repeated
        order means the randomization was lost.
        """
        getter = _get_cluster_getter(num_of_instances=9)
        orders = {
            getter._make_instances_order(
                available_instances=list(range(9)),
                lock_resources=[resources.Resources.CLUSTER],
                use_resources=[resources.Resources.CLUSTER],
            )
            for _ in range(10)
        }
        assert len(orders) > 1


@pytest.mark.usefixtures("db_dir")
class TestInitUseResources:
    """Check the initialization of "use" resources."""

    def test_adds_cluster(self):
        """Check that the `CLUSTER` resource is always added to "use" resources."""
        getter = _get_cluster_getter()
        use = getter._init_use_resources(lock_resources=[], use_resources=[])
        assert list(use) == [resources.Resources.CLUSTER]

    def test_lock_filtered_from_use(self):
        """Check that locked resources are filtered out of "use" resources."""
        getter = _get_cluster_getter()
        use = getter._init_use_resources(
            lock_resources=[resources.Resources.CLUSTER, "poolX"],
            use_resources=["poolX", "poolY"],
        )
        assert sorted(use) == ["poolY"]  # type: ignore[type-var]

    def test_filters_preserved(self):
        """Check that resource filter objects are passed through untouched."""
        getter = _get_cluster_getter()
        one_of = resources_management.OneOf(resources=["pool1", "pool2"])
        use = list(getter._init_use_resources(lock_resources=[], use_resources=[one_of]))
        assert one_of in use
        assert resources.Resources.CLUSTER in use

    def test_one_shot_iterator(self):
        """Check that a one-shot iterator of "use" resources is fully consumed.

        The resources are iterated twice internally - without materialization, filter
        objects would be silently dropped on the second pass.
        """
        getter = _get_cluster_getter()
        one_of = resources_management.OneOf(resources=["pool1", "pool2"])
        use = list(
            getter._init_use_resources(lock_resources=[], use_resources=iter(["poolX", one_of]))
        )
        assert "poolX" in use
        assert one_of in use


@pytest.mark.usefixtures("db_dir")
def test_test_needs_respin():
    """Check the conditions for a test-requested respin.

    Only a test with custom cluster scripts needs a respin, and only when it is the
    initial test of its mark - a non-initial marked test reuses the setup done by the
    initial one.
    """
    getter = _get_cluster_getter()

    cget_status = _get_cget_status(instance_num=0)
    assert getter._test_needs_respin(cget_status) is False

    cget_status.scriptsdir = "/custom/scripts"
    assert getter._test_needs_respin(cget_status) is True

    # Non-initial marked test - respin was already done by the initial marked test
    cget_status.mark = "markA"
    cget_status.marked_ready_rows = [
        status_db.StatusRow(instance_num=0, worker_id="gw0", mark="markA")
    ]
    assert getter._test_needs_respin(cget_status) is False


@pytest.mark.usefixtures("db_dir")
class TestUpdateMarkedTests:
    """Check the mark staleness handling."""

    def _set_mark_age(self, age_sec: float, mark: str = "*") -> float:
        """Set creation time of "current mark" records and return the timestamp.

        The direct SQL update bypasses the write generation counter, so the tests must
        refresh the getter's snapshot explicitly after calling this helper.
        """
        created_at = time.time() - age_sec
        mark_clause = "" if mark == "*" else " AND mark = ?"
        params = (
            [created_at, status_db.CURR_MARK]
            if mark == "*"
            else [created_at, status_db.CURR_MARK, mark]
        )
        status_db._get_conn().execute(
            f"UPDATE flags SET created_at = ? WHERE type = ?{mark_clause}",
            params,
        )
        return created_at

    def test_no_marks_noop(self):
        """Check that instance without marks doesn't get any status writes."""
        getter = _get_cluster_getter()
        status_db.create_test_running(instance_num=0, worker_id="gw1", test_id="test_x")
        gen_before = status_db._write_generation

        getter._update_marked_tests(cget_status=_get_cget_status(instance_num=0))

        assert status_db._write_generation == gen_before

    def test_stale_mark_cleaned(self):
        """Check that a mark with no test running for too long is cleaned up.

        The promised post-group respin ("respin after mark") is converted to
        "needs respin" during the cleanup.
        """
        getter = _get_cluster_getter()
        _populate_marked(instance_num=0, mark="markA")
        self._set_mark_age(cluster_getter.MARK_STALENESS_SEC + 1)
        getter.snap.refresh()

        getter._update_marked_tests(cget_status=_get_cget_status(instance_num=0))

        assert status_db.list_curr_mark(instance_num=0) == []
        assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) == []
        assert len(status_db.list_respin_needed(instance_num=0)) == 1

    def test_stale_mark_kept_during_respin(self):
        """Check that marks are left alone while the cluster instance is being respun."""
        getter = _get_cluster_getter()
        _populate_marked(instance_num=0, mark="markA")
        status_db.create_respin_progress(instance_num=0, worker_id="gw1")
        self._set_mark_age(cluster_getter.MARK_STALENESS_SEC + 1)
        getter.snap.refresh()

        getter._update_marked_tests(cget_status=_get_cget_status(instance_num=0))

        assert len(status_db.list_curr_mark(instance_num=0)) == 1

    def test_fresh_idle_mark_kept(self):
        """Check that a mark between two marked tests is kept until it goes stale."""
        getter = _get_cluster_getter()
        _populate_marked(instance_num=0, mark="markA")
        self._set_mark_age(cluster_getter.MARK_STALENESS_SEC - 10)
        getter.snap.refresh()

        getter._update_marked_tests(cget_status=_get_cget_status(instance_num=0))

        assert len(status_db.list_curr_mark(instance_num=0)) == 1

    def test_running_mark_refreshed(self):
        """Check that a mark with a running marked test gets its timestamp refreshed."""
        getter = _get_cluster_getter()
        _populate_marked(instance_num=0, mark="markA")
        status_db.create_test_running(
            instance_num=0, worker_id="gw1", test_id="test_x", mark="markA"
        )
        created_at = self._set_mark_age(cluster_getter.MARK_REFRESH_SEC + 1)
        getter.snap.refresh()

        getter._update_marked_tests(cget_status=_get_cget_status(instance_num=0))

        rows = status_db.list_curr_mark(instance_num=0)
        assert len(rows) == 1
        assert rows[0].created_at > created_at

    def test_running_fresh_mark_not_refreshed(self):
        """Check that a fresh mark is not refreshed, so polling doesn't write on every pass."""
        getter = _get_cluster_getter()
        _populate_marked(instance_num=0, mark="markA")
        status_db.create_test_running(
            instance_num=0, worker_id="gw1", test_id="test_x", mark="markA"
        )
        created_at = self._set_mark_age(cluster_getter.MARK_REFRESH_SEC - 5)
        getter.snap.refresh()

        getter._update_marked_tests(cget_status=_get_cget_status(instance_num=0))

        rows = status_db.list_curr_mark(instance_num=0)
        assert len(rows) == 1
        assert rows[0].created_at == created_at

    def test_newest_record_decides(self):
        """Check that the newest record of a mark decides staleness for the whole mark."""
        getter = _get_cluster_getter()
        status_db.create_curr_mark(instance_num=0, worker_id="gw0", mark="markA")
        self._set_mark_age(cluster_getter.MARK_STALENESS_SEC + 100)
        status_db.create_curr_mark(instance_num=0, worker_id="gw1", mark="markA")
        getter.snap.refresh()

        getter._update_marked_tests(cget_status=_get_cget_status(instance_num=0))

        assert len(status_db.list_curr_mark(instance_num=0)) == 2

    def test_mixed_marks_staleness(self):
        """Check that only the stale mark is cleaned when an instance has multiple marks.

        Staleness is decided per mark - the records of the healthy mark, including its
        resource records, must survive the cleanup of the stale mark.
        """
        getter = _get_cluster_getter()
        _populate_marked(instance_num=0, mark="markA")
        status_db.create_curr_mark(instance_num=0, worker_id="gw2", mark="markB")
        status_db.create_resources(
            instance_num=0, worker_id="gw2", names=["pool6"], mode=status_db.MODE_LOCK, mark="markB"
        )
        self._set_mark_age(cluster_getter.MARK_STALENESS_SEC + 1, mark="markA")
        getter.snap.refresh()

        getter._update_marked_tests(cget_status=_get_cget_status(instance_num=0))

        assert status_db.list_curr_mark(instance_num=0, mark="markA") == []
        assert len(status_db.list_curr_mark(instance_num=0, mark="markB")) == 1
        assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) == ["pool6"]

    def test_running_ancient_mark_not_cleaned(self):
        """Check that a mark with a running test is never cleaned, no matter how old.

        The "test is running" check must take precedence over the staleness check -
        cleaning the mark would tear down the resource records of a live marked group.
        """
        getter = _get_cluster_getter()
        _populate_marked(instance_num=0, mark="markA")
        status_db.create_test_running(
            instance_num=0, worker_id="gw1", test_id="test_x", mark="markA"
        )
        created_at = self._set_mark_age(cluster_getter.MARK_STALENESS_SEC + 100)
        getter.snap.refresh()

        getter._update_marked_tests(cget_status=_get_cget_status(instance_num=0))

        rows = status_db.list_curr_mark(instance_num=0, mark="markA")
        assert len(rows) == 1
        # The ancient records got refreshed rather than cleaned
        assert rows[0].created_at > created_at
        assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) != []


@pytest.mark.usefixtures("db_dir")
class TestResolveResources:
    """Check the resolution of "use" and "lock" resources availability."""

    def test_lock_conflict_with_locked(self):
        """Check that a resource locked by another test cannot be locked."""
        getter = _get_cluster_getter()
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool1"], mode=status_db.MODE_LOCK
        )
        cget_status = _get_cget_status(instance_num=0)
        cget_status.lock_resources = ["pool1"]

        assert getter._resolve_resources_availability(cget_status) is False
        # Resolved resources are recorded only on success
        assert list(cget_status.final_lock_resources) == []
        assert list(cget_status.final_use_resources) == []

    def test_lock_conflict_with_used(self):
        """Check that a resource in use by another test cannot be locked."""
        getter = _get_cluster_getter()
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool1"], mode=status_db.MODE_USE
        )
        cget_status = _get_cget_status(instance_num=0)
        cget_status.lock_resources = ["pool1"]

        assert getter._resolve_resources_availability(cget_status) is False

    def test_use_conflict_with_locked(self):
        """Check that a resource locked by another test cannot be used."""
        getter = _get_cluster_getter()
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool1"], mode=status_db.MODE_LOCK
        )
        cget_status = _get_cget_status(instance_num=0)
        cget_status.use_resources = ["pool1"]

        assert getter._resolve_resources_availability(cget_status) is False

    def test_use_not_blocked_by_used(self):
        """Check that a resource in use by another test can still be used."""
        getter = _get_cluster_getter()
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool1"], mode=status_db.MODE_USE
        )
        cget_status = _get_cget_status(instance_num=0)
        cget_status.use_resources = ["pool1"]

        assert getter._resolve_resources_availability(cget_status) is True
        assert list(cget_status.final_use_resources) == ["pool1"]

    def test_resolved_resources(self):
        """Check that resolved resources are recorded, with locked implying in-use."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status(instance_num=0)
        cget_status.lock_resources = ["pool1"]
        cget_status.use_resources = ["pool1", "pool2"]

        assert getter._resolve_resources_availability(cget_status) is True
        assert list(cget_status.final_lock_resources) == ["pool1"]
        # Locked resources are in use implicitly, only the rest is recorded as "use"
        assert list(cget_status.final_use_resources) == ["pool2"]

    def test_one_of_filter(self):
        """Check that a `OneOf` filter picks an available resource."""
        getter = _get_cluster_getter()
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool1"], mode=status_db.MODE_LOCK
        )
        cget_status = _get_cget_status(instance_num=0)
        cget_status.lock_resources = [resources_management.OneOf(resources=["pool1", "pool2"])]

        assert getter._resolve_resources_availability(cget_status) is True
        assert list(cget_status.final_lock_resources) == ["pool2"]


@pytest.mark.usefixtures("db_dir")
class TestPrio:
    """Check the priority test handling."""

    def test_init_prio(self):
        """Check that "prio" status record is created only for priority tests."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status(instance_num=0)

        getter._init_prio(cget_status)
        assert cget_status.prio_here is False
        assert status_db.list_prio_in_progress() == []

        cget_status.prio = True
        getter._init_prio(cget_status)
        assert cget_status.prio_here is True
        assert len(status_db.list_prio_in_progress()) == 1

    def test_wait_for_other_prio(self):
        """Check that a test waits when a priority test setup is in progress."""
        getter = _get_cluster_getter()
        status_db.create_prio_in_progress(worker_id="gw1")
        cget_status = _get_cget_status(instance_num=0)

        assert getter._wait_for_prio(cget_status) is True

    def test_no_wait_cases(self):
        """Check the cases where a priority test in progress doesn't block.

        Own priority setup, a selected cluster instance and marked tests running
        anywhere all take precedence over waiting.
        """
        getter = _get_cluster_getter()
        status_db.create_prio_in_progress(worker_id="gw1")

        cget_status = _get_cget_status(instance_num=0)
        cget_status.prio_here = True
        assert getter._wait_for_prio(cget_status) is False

        cget_status = _get_cget_status(instance_num=0)
        cget_status.selected_instance = 0
        assert getter._wait_for_prio(cget_status) is False

        cget_status = _get_cget_status(instance_num=0, mark="markA")
        cget_status.marked_running_my_anywhere = [
            status_db.StatusRow(instance_num=1, worker_id="gw1", mark="markA")
        ]
        assert getter._wait_for_prio(cget_status) is False

    def test_no_prio_no_wait(self):
        """Check that nothing blocks when no priority test is in progress."""
        getter = _get_cluster_getter()
        assert getter._wait_for_prio(_get_cget_status(instance_num=0)) is False


@pytest.mark.usefixtures("db_dir")
def test_respun_by_other_worker():
    """Check the detection of a respin done by another worker."""
    getter = _get_cluster_getter()
    cget_status = _get_cget_status(instance_num=0)

    assert getter._respun_by_other_worker(cget_status) is False

    status_db.create_respin_progress(instance_num=0, worker_id="gw1")
    getter.snap.refresh()
    assert getter._respun_by_other_worker(cget_status) is True

    # The worker doing the respin is not blocked by its own respin
    cget_status.respin_here = True
    assert getter._respun_by_other_worker(cget_status) is False


@pytest.mark.usefixtures("db_dir")
def test_is_already_running():
    """Check the detection of a test that is already set up and running."""
    getter = _get_cluster_getter()

    assert getter._is_already_running() is False

    # A "test running" record alone is not enough - the cluster instance must be set
    status_db.create_test_running(instance_num=0, worker_id="gw0", test_id="test_x")
    assert getter._is_already_running() is False

    getter._cluster_instance_num = 0
    assert getter._is_already_running() is True


@pytest.mark.usefixtures("db_dir")
def test_finish_respin_phases():
    """Check the two-phase respin finishing.

    The first call only signals that the cluster instance is ready to be respun (the
    actual respin runs outside of the global lock). The second call cleans up the
    respin status records.
    """
    getter = _get_cluster_getter()
    cget_status = _get_cget_status(instance_num=0)

    # No respin on this worker - nothing to do
    assert getter._finish_respin(cget_status) is True

    cget_status.respin_here = True
    status_db.create_respin_progress(instance_num=0, worker_id="gw0")
    status_db.create_respin_needed(instance_num=0, worker_id="gw0")

    # First call - ready to respin, cannot continue in this iteration
    assert getter._finish_respin(cget_status) is False
    assert cget_status.respin_ready is True
    assert len(status_db.list_respin_progress(instance_num=0)) == 1

    # Second call - respin done, clean up
    assert getter._finish_respin(cget_status) is True
    assert cget_status.respin_ready is False
    assert cget_status.respin_here is False
    assert status_db.list_respin_progress(instance_num=0) == []
    assert status_db.list_respin_needed(instance_num=0) == []


@pytest.mark.usefixtures("db_dir")
class TestCreateTestStatusRecords:
    """Check the creation of status records for a starting test."""

    def _get_getter_status(
        self, mark: str = "", cleanup: bool = False
    ) -> tuple[cluster_getter.ClusterGetter, cluster_getter._ClusterGetStatus]:
        """Return getter and status prepared for creating status records on instance 0."""
        getter = _get_cluster_getter()
        getter._cluster_instance_num = 0
        cget_status = _get_cget_status(instance_num=0, mark=mark)
        cget_status.cleanup = cleanup
        cget_status.current_test = "test_x (setup)"
        cget_status.final_lock_resources = ["pool1"]
        cget_status.final_use_resources = [resources.Resources.CLUSTER]
        return getter, cget_status

    def test_plain_test(self):
        """Check records of a test without mark and cleanup."""
        getter, cget_status = self._get_getter_status()

        getter._create_test_status_records(cget_status)

        assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) == ["pool1"]
        assert status_db.get_resource_names(mode=status_db.MODE_USE, instance_num=0) == [
            resources.Resources.CLUSTER
        ]
        # The "(setup)" part is left out of the test id
        assert status_db.get_test_names(instance_num=0) == ["test_x"]
        assert status_db.list_respin_needed(instance_num=0) == []
        assert status_db.list_respin_after_mark(instance_num=0) == []

    def test_marked_cleanup(self):
        """Check that marked test with cleanup schedules respin after the whole group."""
        getter, cget_status = self._get_getter_status(mark="markA", cleanup=True)

        getter._create_test_status_records(cget_status)

        assert len(status_db.list_respin_after_mark(instance_num=0, mark="markA")) == 1
        assert status_db.list_respin_needed(instance_num=0) == []
        # The resource records carry the mark
        assert status_db.get_resource_names(
            mode=status_db.MODE_LOCK, instance_num=0, mark="markA"
        ) == ["pool1"]

    def test_unmarked_cleanup(self):
        """Check that unmarked test with cleanup schedules respin right after the test."""
        getter, cget_status = self._get_getter_status(cleanup=True)

        getter._create_test_status_records(cget_status)

        assert len(status_db.list_respin_needed(instance_num=0)) == 1
        assert status_db.list_respin_after_mark(instance_num=0) == []


@pytest.mark.usefixtures("db_dir")
class TestDeadFraction:
    """Check the dead cluster instances thresholds."""

    def test_all_dead(self):
        """Check that the run fails when all cluster instances are dead."""
        getter = _get_cluster_getter(num_of_instances=2)
        status_db.set_cluster_dead(instance_num=0)
        status_db.set_cluster_dead(instance_num=1)

        with pytest.raises(RuntimeError, match="All cluster instances are dead"):
            getter._check_dead_fraction(max_dead_fraction=1.0)

    def test_below_threshold(self):
        """Check that dead instances below the threshold don't fail the run."""
        getter = _get_cluster_getter(num_of_instances=2)
        status_db.set_cluster_dead(instance_num=0)

        getter._check_dead_fraction(max_dead_fraction=0.51)

    def test_above_threshold(self):
        """Check that too many dead instances fail the run."""
        getter = _get_cluster_getter(num_of_instances=2)
        status_db.set_cluster_dead(instance_num=0)

        with pytest.raises(RuntimeError, match="Too many cluster instances are dead"):
            getter._check_dead_fraction(max_dead_fraction=0.5)

    def test_no_instances(self):
        """Check that zero configured cluster instances is rejected."""
        getter = _get_cluster_getter(num_of_instances=0)

        with pytest.raises(ValueError, match="greater than 0"):
            getter._check_dead_fraction(max_dead_fraction=1.0)

    def test_strict_window(self):
        """Check that the stricter dead instances threshold applies close to the deadline.

        Far from the deadline only all-dead fails the run; within the strict check
        window a majority of dead instances is enough.
        """
        getter = _get_cluster_getter(num_of_instances=3)
        status_db.set_cluster_dead(instance_num=0)
        status_db.set_cluster_dead(instance_num=1)

        # Far from the deadline - 2 of 3 dead is tolerated
        getter._fail_on_dead_clusters(remaining_time_sec=getter.strict_check_window + 1)

        # Within the strict check window - 2 of 3 dead fails the run
        with pytest.raises(RuntimeError, match="Too many cluster instances are dead"):
            getter._fail_on_dead_clusters(remaining_time_sec=getter.strict_check_window)
