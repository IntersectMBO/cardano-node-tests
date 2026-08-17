"""Tests for `cluster_management.cluster_getter`."""

import time

import pytest

from cardano_node_tests.cluster_management import cluster_getter
from cardano_node_tests.cluster_management import resources
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.cluster_management import status_db
from cardano_node_tests.utils import configuration


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


def _get_cget_status(mark: str = "") -> cluster_getter._ClusterGetStatus:
    """Return a `_ClusterGetStatus` instance suitable for unit testing."""
    return cluster_getter._ClusterGetStatus(
        mark=mark,
        lock_resources=[],
        use_resources=[],
        prio=False,
        cleanup=False,
        scriptsdir="",
        current_test="test_x",
    )


def _get_scratch(instance_num: int = 0) -> cluster_getter._InstanceScratch:
    """Return an `_InstanceScratch` instance suitable for unit testing."""
    return cluster_getter._InstanceScratch(instance_num=instance_num)


def _get_claim(
    instance_num: int = 0,
    phase: cluster_getter._RespinPhase = cluster_getter._RespinPhase.CLAIMED,
) -> cluster_getter._RespinClaim:
    """Return a `_RespinClaim` instance suitable for unit testing."""
    return cluster_getter._RespinClaim(instance_num=instance_num, phase=phase)


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
def test_cleanup_dead_cluster_removes_marks():
    """Check that dead cluster instance cleanup removes marks and marked resources."""
    getter = _get_cluster_getter()
    cget_status = _get_cget_status()
    cget_status.selected_instance = 0
    # The state a worker is in when its respin attempt failed and killed the instance
    cget_status.respin = _get_claim(instance_num=0, phase=cluster_getter._RespinPhase.RESPUN)

    _populate_marked(instance_num=0, mark="markA")

    getter._cleanup_dead_cluster(cget_status, instance_num=0)

    assert cget_status.selected_instance == -1
    assert cget_status.respin is None
    assert status_db.list_curr_mark(instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_USE, instance_num=0) == []


@pytest.mark.usefixtures("db_dir")
def test_cleanup_dead_cluster_removes_respin_records():
    """Check that dead cluster instance cleanup removes respin records of all workers.

    The instance is dead for good, so no worker can respin it and the records are of
    no use to anyone. Records of other cluster instances must not be touched.
    """
    getter = _get_cluster_getter()
    cget_status = _get_cget_status()
    cget_status.selected_instance = 0
    cget_status.respin = _get_claim(instance_num=0, phase=cluster_getter._RespinPhase.RESPUN)

    status_db.create_respin_progress(instance_num=0, worker_id="gw0")
    status_db.create_respin_needed(instance_num=0, worker_id="gw1")
    status_db.create_respin_needed(instance_num=1, worker_id="gw1")

    getter._cleanup_dead_cluster(cget_status, instance_num=0)

    assert status_db.list_respin_progress(instance_num=0) == []
    assert status_db.list_respin_needed(instance_num=0) == []
    assert len(status_db.list_respin_needed(instance_num=1)) == 1


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
    cget_status = _get_cget_status(mark="markA")
    scratch = _get_scratch(instance_num=0)
    scratch.cluster_needs_respin = True

    _populate_marked(instance_num=0, mark="markA")
    scratch.marked_ready_rows = status_db.list_curr_mark(instance_num=0, mark="markA")
    assert scratch.marked_ready_rows

    # The test cannot continue in this iteration - it must re-evaluate the instance
    # as the initial test of the mark. The findings of this evaluation, incl. the
    # mark's records, are discarded together with the scratch object.
    decision = getter._init_respin(cget_status, scratch)
    assert decision.verdict is cluster_getter._InstanceVerdict.DEFER

    assert cget_status.respin is not None
    assert cget_status.respin.phase is cluster_getter._RespinPhase.CLAIMED
    assert cget_status.selected_instance == 0
    assert len(status_db.list_respin_progress(instance_num=0)) == 1
    # The respin stays scheduled even when the re-evaluation gets delayed
    assert len(status_db.list_respin_needed(instance_num=0)) == 1
    assert status_db.list_curr_mark(instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_LOCK, instance_num=0) == []
    assert status_db.get_resource_names(mode=status_db.MODE_USE, instance_num=0) == []

    # The re-evaluation re-creates the mark's records; later `_init_respin` calls
    # must short-circuit on the owned respin and not wipe them again. The short-circuit
    # must hold in every owning phase, incl. the post-respin re-entry.
    _populate_marked(instance_num=0, mark="markA")
    for own_phase in cluster_getter._RespinPhase:
        cget_status.respin = _get_claim(instance_num=0, phase=own_phase)
        reeval_scratch = _get_scratch(instance_num=0)
        # Without the short-circuit, the instance still needing a respin would make
        # the claim - and the wipe of the mark's records - run again
        reeval_scratch.cluster_needs_respin = True
        decision = getter._init_respin(cget_status, reeval_scratch)
        assert decision.verdict is cluster_getter._InstanceVerdict.START
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
    cget_status = _get_cget_status(mark="markA")
    scratch = _get_scratch(instance_num=0)
    status_db.create_curr_mark(instance_num=1, worker_id="gw1", mark="markA")
    cget_status.marked_running_my_anywhere = status_db.list_curr_mark(mark="markA")
    assert cget_status.marked_running_my_anywhere

    # Without the pending respin, the mark on the other instance blocks this worker
    assert getter._marked_select_instance(cget_status, scratch) is False

    # The respinning worker proceeds as the first test of the mark
    cget_status.claim_respin(0)
    assert getter._marked_select_instance(cget_status, scratch) is True

    # A claim on another cluster instance doesn't unblock this one
    cget_status.claim_respin(1)
    assert getter._marked_select_instance(cget_status, scratch) is False


@pytest.mark.usefixtures("db_dir")
def test_init_respin_tests_running():
    """Check that respin is not initiated while tests are running on the instance.

    Nothing can be wiped and no respin state set - the worker must wait for the
    running tests to finish first.
    """
    getter = _get_cluster_getter()
    cget_status = _get_cget_status(mark="markA")
    scratch = _get_scratch(instance_num=0)
    scratch.cluster_needs_respin = True

    _populate_marked(instance_num=0, mark="markA")
    status_db.create_test_running(instance_num=0, worker_id="gw1", test_id="test_x")
    scratch.started_tests_rows = status_db.list_test_running(instance_num=0)

    decision = getter._init_respin(cget_status, scratch)
    assert decision.verdict is cluster_getter._InstanceVerdict.SKIP

    assert cget_status.respin is None
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
    cget_status = _get_cget_status()
    scratch = _get_scratch(instance_num=0)
    scratch.cluster_needs_respin = True

    _populate_marked(instance_num=0, mark="markA")

    decision = getter._init_respin(cget_status, scratch)
    assert decision.verdict is cluster_getter._InstanceVerdict.START

    assert cget_status.respin is not None
    assert cget_status.respin.phase is cluster_getter._RespinPhase.CLAIMED
    assert cget_status.selected_instance == 0
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

    cget_status = _get_cget_status()
    scratch = _get_scratch(instance_num=0)
    assert getter._test_needs_respin(cget_status, scratch) is False

    cget_status.scriptsdir = "/custom/scripts"
    assert getter._test_needs_respin(cget_status, scratch) is True

    # Non-initial marked test - respin was already done by the initial marked test
    cget_status.mark = "markA"
    scratch.marked_ready_rows = [status_db.StatusRow(instance_num=0, worker_id="gw0", mark="markA")]
    assert getter._test_needs_respin(cget_status, scratch) is False


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

        getter._update_marked_tests(instance_num=0)

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

        getter._update_marked_tests(instance_num=0)

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

        getter._update_marked_tests(instance_num=0)

        assert len(status_db.list_curr_mark(instance_num=0)) == 1

    def test_fresh_idle_mark_kept(self):
        """Check that a mark between two marked tests is kept until it goes stale."""
        getter = _get_cluster_getter()
        _populate_marked(instance_num=0, mark="markA")
        self._set_mark_age(cluster_getter.MARK_STALENESS_SEC - 10)
        getter.snap.refresh()

        getter._update_marked_tests(instance_num=0)

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

        getter._update_marked_tests(instance_num=0)

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

        getter._update_marked_tests(instance_num=0)

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

        getter._update_marked_tests(instance_num=0)

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

        getter._update_marked_tests(instance_num=0)

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

        getter._update_marked_tests(instance_num=0)

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
        cget_status = _get_cget_status()
        cget_status.lock_resources = ["pool1"]
        scratch = _get_scratch(instance_num=0)

        assert getter._resolve_resources_availability(cget_status, scratch) is False
        # Resolved resources are recorded only on success
        assert list(scratch.final_lock_resources) == []
        assert list(scratch.final_use_resources) == []

    def test_lock_conflict_with_used(self):
        """Check that a resource in use by another test cannot be locked."""
        getter = _get_cluster_getter()
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool1"], mode=status_db.MODE_USE
        )
        cget_status = _get_cget_status()
        cget_status.lock_resources = ["pool1"]

        assert getter._resolve_resources_availability(cget_status, _get_scratch()) is False

    def test_use_conflict_with_locked(self):
        """Check that a resource locked by another test cannot be used."""
        getter = _get_cluster_getter()
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool1"], mode=status_db.MODE_LOCK
        )
        cget_status = _get_cget_status()
        cget_status.use_resources = ["pool1"]

        assert getter._resolve_resources_availability(cget_status, _get_scratch()) is False

    def test_use_not_blocked_by_used(self):
        """Check that a resource in use by another test can still be used."""
        getter = _get_cluster_getter()
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool1"], mode=status_db.MODE_USE
        )
        cget_status = _get_cget_status()
        cget_status.use_resources = ["pool1"]
        scratch = _get_scratch(instance_num=0)

        assert getter._resolve_resources_availability(cget_status, scratch) is True
        assert list(scratch.final_use_resources) == ["pool1"]

    def test_resolved_resources(self):
        """Check that resolved resources are recorded, with locked implying in-use."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status()
        cget_status.lock_resources = ["pool1"]
        cget_status.use_resources = ["pool1", "pool2"]
        scratch = _get_scratch(instance_num=0)

        assert getter._resolve_resources_availability(cget_status, scratch) is True
        assert list(scratch.final_lock_resources) == ["pool1"]
        # Locked resources are in use implicitly, only the rest is recorded as "use"
        assert list(scratch.final_use_resources) == ["pool2"]

    def test_one_of_filter(self):
        """Check that a `OneOf` filter picks an available resource."""
        getter = _get_cluster_getter()
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool1"], mode=status_db.MODE_LOCK
        )
        cget_status = _get_cget_status()
        cget_status.lock_resources = [resources_management.OneOf(resources=["pool1", "pool2"])]
        scratch = _get_scratch(instance_num=0)

        assert getter._resolve_resources_availability(cget_status, scratch) is True
        assert list(scratch.final_lock_resources) == ["pool2"]


@pytest.mark.usefixtures("db_dir")
class TestPrio:
    """Check the priority test handling."""

    def test_init_prio(self):
        """Check that "prio" status record is created only for priority tests."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status()

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
        cget_status = _get_cget_status()

        assert getter._wait_for_prio(cget_status) is True

    def test_no_wait_cases(self):
        """Check the cases where a priority test in progress doesn't block.

        Own priority setup, a selected cluster instance and marked tests running
        anywhere all take precedence over waiting.
        """
        getter = _get_cluster_getter()
        status_db.create_prio_in_progress(worker_id="gw1")

        cget_status = _get_cget_status()
        cget_status.prio_here = True
        assert getter._wait_for_prio(cget_status) is False

        cget_status = _get_cget_status()
        cget_status.selected_instance = 0
        assert getter._wait_for_prio(cget_status) is False

        cget_status = _get_cget_status(mark="markA")
        cget_status.marked_running_my_anywhere = [
            status_db.StatusRow(instance_num=1, worker_id="gw1", mark="markA")
        ]
        assert getter._wait_for_prio(cget_status) is False

    def test_no_prio_no_wait(self):
        """Check that nothing blocks when no priority test is in progress."""
        getter = _get_cluster_getter()
        assert getter._wait_for_prio(_get_cget_status()) is False


@pytest.mark.usefixtures("db_dir")
def test_being_respun_by_other_worker():
    """Check the detection of a respin done by another worker."""
    getter = _get_cluster_getter()
    cget_status = _get_cget_status()

    assert getter._being_respun_by_other_worker(cget_status, instance_num=0) is False

    status_db.create_respin_progress(instance_num=0, worker_id="gw1")
    getter.snap.refresh()
    assert getter._being_respun_by_other_worker(cget_status, instance_num=0) is True

    # The worker doing the respin is not blocked by its own respin, in any phase
    for own_phase in cluster_getter._RespinPhase:
        cget_status.respin = _get_claim(instance_num=0, phase=own_phase)
        assert getter._being_respun_by_other_worker(cget_status, instance_num=0) is False

    # A claim on another cluster instance doesn't hide the respin done here
    cget_status.respin = _get_claim(instance_num=1)
    assert getter._being_respun_by_other_worker(cget_status, instance_num=0) is True


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
def test_respin_arm_and_cleanup():
    """Check respin arming and the cleanup after the respin.

    Arming only signals that the cluster instance is ready to be respun (the actual
    respin runs outside of the global lock). The cleanup afterwards removes the respin
    status records.
    """
    getter = _get_cluster_getter()
    cget_status = _get_cget_status()

    cget_status.claim_respin(0)
    assert cget_status.respin is not None
    status_db.create_respin_progress(instance_num=0, worker_id="gw0")
    status_db.create_respin_needed(instance_num=0, worker_id="gw0")

    # Arm the respin - the status records must stay until the respin is performed
    getter._arm_respin(cget_status.respin)
    assert cget_status.respin.phase is cluster_getter._RespinPhase.ARMED
    assert len(status_db.list_respin_progress(instance_num=0)) == 1
    assert len(status_db.list_respin_needed(instance_num=0)) == 1

    # The respin itself runs in `get_cluster_instance`, outside of the global lock
    cget_status.respin.phase = cluster_getter._RespinPhase.RESPUN

    # Respin done - clean up
    getter._cleanup_after_respin(cget_status)
    assert cget_status.respin is None
    assert status_db.list_respin_progress(instance_num=0) == []
    assert status_db.list_respin_needed(instance_num=0) == []


@pytest.mark.usefixtures("db_dir")
def test_respin_wrong_state_rejected():
    """Check that arming and cleanup fail fast when called in a wrong respin state."""
    getter = _get_cluster_getter()
    cget_status = _get_cget_status()

    # Arming is valid only for a claimed respin
    for phase in (cluster_getter._RespinPhase.ARMED, cluster_getter._RespinPhase.RESPUN):
        with pytest.raises(RuntimeError, match="Cannot arm respin"):
            getter._arm_respin(_get_claim(instance_num=0, phase=phase))

    # Cleanup is valid only for a performed respin - a premature call would delete
    # another worker's respin status records
    status_db.create_respin_progress(instance_num=0, worker_id="gw1")
    for claim in (
        None,
        _get_claim(instance_num=0, phase=cluster_getter._RespinPhase.CLAIMED),
        _get_claim(instance_num=0, phase=cluster_getter._RespinPhase.ARMED),
    ):
        cget_status.respin = claim
        with pytest.raises(RuntimeError, match="Cannot clean up after respin"):
            getter._cleanup_after_respin(cget_status)
    assert len(status_db.list_respin_progress(instance_num=0)) == 1


@pytest.mark.usefixtures("db_dir")
class TestCreateTestStatusRecords:
    """Check the creation of status records for a starting test."""

    def _get_getter_status(
        self, mark: str = "", cleanup: bool = False
    ) -> tuple[
        cluster_getter.ClusterGetter,
        cluster_getter._ClusterGetStatus,
        cluster_getter._InstanceScratch,
    ]:
        """Return getter, status and scratch for creating status records on instance 0."""
        getter = _get_cluster_getter()
        getter._cluster_instance_num = 0
        cget_status = _get_cget_status(mark=mark)
        cget_status.cleanup = cleanup
        cget_status.current_test = "test_x (setup)"
        scratch = _get_scratch(instance_num=0)
        scratch.final_lock_resources = ["pool1"]
        scratch.final_use_resources = [resources.Resources.CLUSTER]
        return getter, cget_status, scratch

    def test_plain_test(self):
        """Check records of a test without mark and cleanup."""
        getter, cget_status, scratch = self._get_getter_status()

        getter._create_test_status_records(cget_status, scratch)

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
        getter, cget_status, scratch = self._get_getter_status(mark="markA", cleanup=True)

        getter._create_test_status_records(cget_status, scratch)

        assert len(status_db.list_respin_after_mark(instance_num=0, mark="markA")) == 1
        assert status_db.list_respin_needed(instance_num=0) == []
        # The resource records carry the mark
        assert status_db.get_resource_names(
            mode=status_db.MODE_LOCK, instance_num=0, mark="markA"
        ) == ["pool1"]

    def test_unmarked_cleanup(self):
        """Check that unmarked test with cleanup schedules respin right after the test."""
        getter, cget_status, scratch = self._get_getter_status(cleanup=True)

        getter._create_test_status_records(cget_status, scratch)

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


@pytest.mark.usefixtures("db_dir")
def test_respin_if_armed(monkeypatch: pytest.MonkeyPatch):
    """Check that the armed respin runs exactly once per claim.

    The respin runs only in the ARMED phase and the move to RESPUN right after
    the run prevents another restart when the instance loop is re-entered before
    the cleanup.
    """
    getter = _get_cluster_getter()
    cget_status = _get_cget_status()
    cget_status.scriptsdir = "custom"

    respin_calls: list[str] = []
    monkeypatch.setattr(getter, "_respin", lambda scriptsdir: respin_calls.append(scriptsdir))

    # Nothing runs while no respin is claimed
    getter._respin_if_armed(cget_status)
    assert respin_calls == []
    assert cget_status.respin is None

    # Nothing runs while the claimed respin is not armed
    for phase in (cluster_getter._RespinPhase.CLAIMED, cluster_getter._RespinPhase.RESPUN):
        cget_status.respin = _get_claim(instance_num=0, phase=phase)
        getter._respin_if_armed(cget_status)
        assert respin_calls == []
        assert cget_status.respin.phase is phase

    # The armed respin runs with the test's custom scripts and moves to RESPUN
    cget_status.respin = _get_claim(instance_num=0, phase=cluster_getter._RespinPhase.ARMED)
    getter._respin_if_armed(cget_status)
    assert respin_calls == ["custom"]
    assert cget_status.respin.phase is cluster_getter._RespinPhase.RESPUN

    # Re-entering must not run the respin again
    getter._respin_if_armed(cget_status)
    assert respin_calls == ["custom"]


@pytest.mark.usefixtures("db_dir")
def test_respin_if_armed_failure(monkeypatch: pytest.MonkeyPatch):
    """Check that a failed respin still moves to RESPUN and is not retried.

    A failed `_respin` marks the cluster instance dead and the recovery is
    handled by the dead cluster check on the next iteration - not by running
    the respin again.
    """
    getter = _get_cluster_getter()
    cget_status = _get_cget_status()
    cget_status.respin = _get_claim(instance_num=0, phase=cluster_getter._RespinPhase.ARMED)

    respin_calls: list[str] = []

    def _failing_respin(scriptsdir: str = "") -> bool:
        respin_calls.append(scriptsdir)
        status_db.set_cluster_dead(instance_num=0)
        return False

    monkeypatch.setattr(getter, "_respin", _failing_respin)

    getter._respin_if_armed(cget_status)

    assert respin_calls == [""]
    assert cget_status.respin is not None
    assert cget_status.respin.phase is cluster_getter._RespinPhase.RESPUN

    # Re-entering must not retry the failed respin
    getter._respin_if_armed(cget_status)
    assert respin_calls == [""]


@pytest.mark.usefixtures("db_dir")
class TestEvaluateInstance:
    """Check the evaluation of a single cluster instance for the current test."""

    def test_dead_instance(self):
        """Check that a dead instance is skipped and the worker state is reset."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status()
        cget_status.selected_instance = 0
        cget_status.respin = _get_claim(instance_num=0, phase=cluster_getter._RespinPhase.RESPUN)
        status_db.set_cluster_dead(instance_num=0)
        getter.snap.refresh()

        decision, scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.SKIP
        assert scratch.instance_num == 0
        assert cget_status.selected_instance == -1
        assert cget_status.respin is None

    def test_respun_elsewhere(self):
        """Check that an instance being respun by another worker is skipped."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status()
        status_db.create_respin_progress(instance_num=0, worker_id="gw1")
        getter.snap.refresh()

        decision, _scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.SKIP
        assert decision.backoff_sec == 5

    def test_too_many_tests(self):
        """Check that a full instance is skipped when there are more instances."""
        getter = _get_cluster_getter(num_of_instances=2)
        cget_status = _get_cget_status()
        status_db.set_cluster_running(instance_num=0)
        for tno in range(configuration.MAX_TESTS_PER_CLUSTER):
            status_db.create_test_running(instance_num=0, worker_id=f"gw{tno}", test_id=f"t{tno}")
        getter.snap.refresh()

        decision, _scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.SKIP
        assert decision.backoff_sec == 2

    def test_needs_respin_try_next(self):
        """Check that an instance that needs respin is skipped while others remain untried."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status()

        # The cluster instance was never started, so it needs respin
        decision, scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.SKIP
        assert scratch.cluster_needs_respin is True
        assert cget_status.respin is None

    def test_needs_respin_claimed_last_resort(self):
        """Check that the respin is claimed once all instances were tried."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status()
        cget_status.tried_all_instances = True

        decision, _scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.START
        assert cget_status.respin is not None
        assert cget_status.respin.phase is cluster_getter._RespinPhase.CLAIMED
        assert cget_status.selected_instance == 0
        assert len(status_db.list_respin_progress(instance_num=0)) == 1

    def test_resource_conflict(self, monkeypatch: pytest.MonkeyPatch):
        """Check that an instance without free resources is skipped."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status()
        cget_status.lock_resources = ["pool1"]
        status_db.set_cluster_running(instance_num=0)
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool1"], mode=status_db.MODE_USE
        )
        monkeypatch.setattr(getter, "_is_healthy", lambda _instance_num: True)
        getter.snap.refresh()

        decision, _scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.SKIP
        assert decision.backoff_sec == 5

    def test_all_clear(self, monkeypatch: pytest.MonkeyPatch):
        """Check that a healthy running instance with free resources can start the test."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status()
        cget_status.lock_resources = ["pool1"]
        status_db.set_cluster_running(instance_num=0)
        monkeypatch.setattr(getter, "_is_healthy", lambda _instance_num: True)
        getter.snap.refresh()

        decision, scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.START
        assert cget_status.respin is None
        assert scratch.final_lock_resources == ["pool1"]

    def test_needs_respin_custom_scripts(self):
        """Check that a test with custom scripts claims the respin right away.

        The respin is needed by the test itself, so the worker doesn't try other
        instances first.
        """
        getter = _get_cluster_getter()
        cget_status = _get_cget_status()
        cget_status.scriptsdir = "custom"

        decision, _scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.START
        assert cget_status.respin is not None
        assert cget_status.respin.phase is cluster_getter._RespinPhase.CLAIMED
        assert cget_status.selected_instance == 0

    def test_max_tests_single_instance(self, monkeypatch: pytest.MonkeyPatch):
        """Check that the tests-per-cluster cap does not apply with a single instance.

        With a single instance there is nowhere else to go, so the cap would only
        deadlock the run.
        """
        getter = _get_cluster_getter(num_of_instances=1)
        cget_status = _get_cget_status()
        status_db.set_cluster_running(instance_num=0)
        for tno in range(configuration.MAX_TESTS_PER_CLUSTER):
            status_db.create_test_running(instance_num=0, worker_id=f"gw{tno}", test_id=f"t{tno}")
        monkeypatch.setattr(getter, "_is_healthy", lambda _instance_num: True)
        getter.snap.refresh()

        decision, _scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.START

    def test_marked_ready_inherits_resources(self, monkeypatch: pytest.MonkeyPatch):
        """Check that a non-initial marked test inherits the group's resources.

        The initial test of the mark already resolved and locked the resources.
        Re-resolving them would conflict with the group's own records and block
        the test forever.
        """
        getter = _get_cluster_getter()
        cget_status = _get_cget_status(mark="markA")
        cget_status.lock_resources = ["pool1"]
        status_db.set_cluster_running(instance_num=0)
        status_db.create_curr_mark(instance_num=0, worker_id="gw1", mark="markA")
        # The group's already-locked resource would block re-resolution
        status_db.create_resources(
            instance_num=0,
            worker_id="gw1",
            names=["pool1"],
            mode=status_db.MODE_LOCK,
            mark="markA",
        )
        monkeypatch.setattr(getter, "_is_healthy", lambda _instance_num: True)
        getter.snap.refresh()

        decision, scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.START
        # Pinned to the instance that has the mark
        assert cget_status.selected_instance == 0
        # Resources were not re-resolved
        assert scratch.final_lock_resources == ()

    def test_respin_postponed_while_tests_run(self):
        """Check that a last-resort respin claim is postponed while tests are running."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status()
        cget_status.tried_all_instances = True
        status_db.create_test_running(instance_num=0, worker_id="gw1", test_id="test_y")
        getter.snap.refresh()

        decision, _scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.SKIP
        assert cget_status.respin is None
        assert status_db.list_respin_progress(instance_num=0) == []

    def test_marked_ready_respin_deferred(self):
        """Check that a marked test claiming a respin defers instead of skipping.

        The claim pins the worker to this instance, so trying the other instances
        would be pointless - the instance is re-evaluated on a later iteration.
        """
        getter = _get_cluster_getter(num_of_instances=2)
        cget_status = _get_cget_status(mark="markA")
        cget_status.scriptsdir = "custom"
        _populate_marked(instance_num=0, mark="markA")
        getter.snap.refresh()

        decision, _scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.DEFER
        assert cget_status.respin is not None
        assert cget_status.selected_instance == 0

    def test_mark_running_elsewhere(self):
        """Check that a marked test waits when its mark runs on another instance."""
        getter = _get_cluster_getter(num_of_instances=2)
        cget_status = _get_cget_status(mark="markA")
        status_db.create_curr_mark(instance_num=1, worker_id="gw1", mark="markA")
        getter.snap.refresh()
        cget_status.marked_running_my_anywhere = getter.snap.list_curr_mark(mark="markA")

        decision, _scratch = getter._evaluate_instance(cget_status, instance_num=0)

        assert decision.verdict is cluster_getter._InstanceVerdict.SKIP
        assert decision.backoff_sec == 2


@pytest.mark.usefixtures("db_dir")
class TestPrepareSelectedInstance:
    """Check the selection of an evaluated cluster instance."""

    def _get_getter_status(
        self, monkeypatch: pytest.MonkeyPatch, mark: str = ""
    ) -> tuple[
        cluster_getter.ClusterGetter,
        cluster_getter._ClusterGetStatus,
        cluster_getter._InstanceScratch,
        list[int],
    ]:
        """Return getter, status, scratch and env setup calls for selecting instance 0."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status(mark=mark)
        scratch = _get_scratch(instance_num=0)
        env_calls: list[int] = []
        monkeypatch.setattr(
            cluster_getter.cluster_nodes,
            "set_cluster_env",
            lambda instance_num: env_calls.append(instance_num),
        )
        return getter, cget_status, scratch, env_calls

    def test_no_respin(self, monkeypatch: pytest.MonkeyPatch):
        """Check plain selection - instance pinned, no respin-related actions."""
        getter, cget_status, scratch, env_calls = self._get_getter_status(monkeypatch)

        decision = getter._prepare_selected_instance(cget_status, scratch)

        assert decision.verdict is cluster_getter._InstanceVerdict.START
        assert cget_status.selected_instance == 0
        assert getter._cluster_instance_num == 0
        assert env_calls == [0]

    def test_claimed_respin_armed(self, monkeypatch: pytest.MonkeyPatch):
        """Check that a claimed respin is armed while the instance stays pinned.

        The DEFER verdict defers the test start - the respin runs first and the
        pinned instance is re-entered afterwards.
        """
        getter, cget_status, scratch, _env_calls = self._get_getter_status(monkeypatch)
        cget_status.claim_respin(0)

        decision = getter._prepare_selected_instance(cget_status, scratch)

        assert decision.verdict is cluster_getter._InstanceVerdict.DEFER
        assert cget_status.respin is not None
        assert cget_status.respin.phase is cluster_getter._RespinPhase.ARMED
        assert cget_status.selected_instance == 0

    def test_claimed_respin_marked(self, monkeypatch: pytest.MonkeyPatch):
        """Check that the mark record is created before the claimed respin is armed.

        Other tests of the marked group must see the mark on this instance during
        the respin window, so they don't prepare another cluster instance.
        """
        getter, cget_status, scratch, _env_calls = self._get_getter_status(
            monkeypatch, mark="markA"
        )
        cget_status.claim_respin(0)

        decision = getter._prepare_selected_instance(cget_status, scratch)

        assert decision.verdict is cluster_getter._InstanceVerdict.DEFER
        assert cget_status.respin is not None
        assert cget_status.respin.phase is cluster_getter._RespinPhase.ARMED
        assert len(status_db.list_curr_mark(instance_num=0, mark="markA")) == 1

    def test_armed_respin_rejected(self, monkeypatch: pytest.MonkeyPatch):
        """Check that selection fails fast when the armed respin was not performed yet."""
        getter, cget_status, scratch, _env_calls = self._get_getter_status(monkeypatch)
        cget_status.respin = _get_claim(instance_num=0, phase=cluster_getter._RespinPhase.ARMED)

        with pytest.raises(RuntimeError, match="Cannot select instance"):
            getter._prepare_selected_instance(cget_status, scratch)

        # The guard precedes all mutations - nothing was selected
        assert cget_status.selected_instance == -1

    def test_respun_cleaned_up(self, monkeypatch: pytest.MonkeyPatch):
        """Check that the performed respin is cleaned up and the selection finishes."""
        getter, cget_status, scratch, _env_calls = self._get_getter_status(monkeypatch)
        cget_status.respin = _get_claim(instance_num=0, phase=cluster_getter._RespinPhase.RESPUN)
        status_db.create_respin_progress(instance_num=0, worker_id="gw0")
        status_db.create_respin_needed(instance_num=0, worker_id="gw0")

        decision = getter._prepare_selected_instance(cget_status, scratch)

        assert decision.verdict is cluster_getter._InstanceVerdict.START
        assert cget_status.respin is None
        assert status_db.list_respin_progress(instance_num=0) == []
        assert status_db.list_respin_needed(instance_num=0) == []

    def test_mark_and_prio_records(self, monkeypatch: pytest.MonkeyPatch):
        """Check that selection creates the mark record and removes the prio record."""
        getter, cget_status, scratch, _env_calls = self._get_getter_status(
            monkeypatch, mark="markA"
        )
        cget_status.prio = True
        status_db.create_prio_in_progress(worker_id="gw0")

        decision = getter._prepare_selected_instance(cget_status, scratch)

        assert decision.verdict is cluster_getter._InstanceVerdict.START
        assert len(status_db.list_curr_mark(instance_num=0, mark="markA")) == 1
        assert status_db.list_prio_in_progress() == []


@pytest.mark.usefixtures("db_dir")
class TestTryInstances:
    """Check the iteration over the cluster instances."""

    def test_start_creates_records(self, monkeypatch: pytest.MonkeyPatch):
        """Check that the test is set up on a suitable cluster instance."""
        getter = _get_cluster_getter()
        cget_status = _get_cget_status()
        status_db.set_cluster_running(instance_num=0)
        monkeypatch.setattr(getter, "_is_healthy", lambda _instance_num: True)
        monkeypatch.setattr(cluster_getter.cluster_nodes, "set_cluster_env", lambda **_kwargs: None)
        getter.snap.refresh()

        decision = getter._try_instances(cget_status, instances_order=(0,))

        assert decision.verdict is cluster_getter._InstanceVerdict.START
        assert len(status_db.list_test_running(instance_num=0, worker_id="gw0")) == 1
        # Nothing asked for a back-off
        assert cget_status.sleep_delay == 0

    def test_no_usable_instance(self):
        """Check that trying all instances in vain records it and asks for a back-off."""
        getter = _get_cluster_getter(num_of_instances=2)
        cget_status = _get_cget_status()
        for instance_num in (0, 1):
            status_db.create_respin_progress(instance_num=instance_num, worker_id="gw1")
        getter.snap.refresh()

        decision = getter._try_instances(cget_status, instances_order=(0, 1))

        assert decision.verdict is cluster_getter._InstanceVerdict.SKIP
        assert cget_status.tried_all_instances is True
        # The back-off asked for by the last verdict is in effect
        assert cget_status.sleep_delay == 5

    def test_deferred_instance_stays_pinned(self, monkeypatch: pytest.MonkeyPatch):
        """Check that a deferred instance ends the iteration with the pin kept.

        The worker is pinned to the instance by the claimed respin, so the other
        instances must not be tried - and they were not all tried, so a later
        iteration can still fall back to them if the pin is released.
        """
        getter = _get_cluster_getter(num_of_instances=2)
        cget_status = _get_cget_status()
        cget_status.scriptsdir = "custom"
        monkeypatch.setattr(cluster_getter.cluster_nodes, "set_cluster_env", lambda **_kwargs: None)

        decision = getter._try_instances(cget_status, instances_order=(0, 1))

        assert decision.verdict is cluster_getter._InstanceVerdict.DEFER
        assert cget_status.selected_instance == 0
        assert cget_status.respin is not None
        assert cget_status.respin.phase is cluster_getter._RespinPhase.ARMED
        assert cget_status.tried_all_instances is False
        # The test was not set up yet
        assert status_db.list_test_running(instance_num=0) == []


@pytest.mark.usefixtures("db_dir")
def test_get_cluster_instance_respin_cycle(monkeypatch: pytest.MonkeyPatch):
    """Check the full respin cycle driven by `get_cluster_instance`.

    The cluster instance was never started, so the worker claims the respin, arms
    it, performs it outside of the global lock and cleans up the respin records,
    all in successive iterations of the top-level loop, and finally starts the
    test on the freshly started instance.
    """
    getter = _get_cluster_getter()

    respin_calls: list[str] = []

    def _fake_respin(scriptsdir: str = "") -> bool:
        respin_calls.append(scriptsdir)
        status_db.set_cluster_running(instance_num=0)
        return True

    monkeypatch.setattr(getter, "_respin", _fake_respin)
    monkeypatch.setattr(getter, "_is_healthy", lambda _instance_num: True)
    monkeypatch.setattr(cluster_getter.cluster_nodes, "set_cluster_env", lambda **_kwargs: None)
    # Make the test independent of the environment it runs in
    monkeypatch.setattr(configuration, "DEV_CLUSTER_RUNNING", False)
    monkeypatch.setattr(configuration, "FORBID_RESTART", False)

    instance_num = getter.get_cluster_instance()

    assert instance_num == 0
    assert getter.cluster_instance_num == 0
    # The respin ran exactly once
    assert respin_calls == [""]
    # The respin records were cleaned up and the test is running
    assert status_db.list_respin_progress(instance_num=0) == []
    assert status_db.list_respin_needed(instance_num=0) == []
    assert len(status_db.list_test_running(instance_num=0, worker_id="gw0")) == 1
