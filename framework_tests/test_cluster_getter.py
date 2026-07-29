"""Tests for `cluster_management.cluster_getter`."""

import pytest

from cardano_node_tests.cluster_management import cluster_getter
from cardano_node_tests.cluster_management import status_db


def _get_cluster_getter() -> cluster_getter.ClusterGetter:
    """Return a `ClusterGetter` instance suitable for unit testing.

    Requires the `db_dir` fixture - the constructor reads the pytest root temp dir.
    """
    return cluster_getter.ClusterGetter(
        worker_id="gw0",
        pytest_config=None,  # type: ignore[arg-type]
        num_of_instances=1,
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
