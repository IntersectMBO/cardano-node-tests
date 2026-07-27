"""Tests for `cluster_management.status_db`."""

import multiprocessing
import pathlib as pl
import typing as tp

import pytest

from cardano_node_tests.cluster_management import status_db
from cardano_node_tests.utils import temptools


@pytest.fixture
def db_dir(tmp_path: pl.Path, monkeypatch: pytest.MonkeyPatch) -> tp.Generator[pl.Path]:
    """Point the status database to a fresh temp dir and reset the cached connection."""
    monkeypatch.setattr(temptools.PytestTempDirs, "pytest_root_tmp", tmp_path)
    status_db._conn = None
    status_db._conn_pid = -1
    yield tmp_path
    # Close the connection created during the test and clear the cached connection object,
    # so a later `_get_conn()` call cannot reuse a closed connection.
    if status_db._conn is not None:
        status_db._conn.close()
    status_db._conn = None
    status_db._conn_pid = -1


def _populate_test_running() -> None:
    status_db.create_test_running(instance_num=0, worker_id="gw0", test_id="test_a")
    status_db.create_test_running(instance_num=0, worker_id="gw1", test_id="test_b", mark="markA")
    status_db.create_test_running(instance_num=1, worker_id="gw2", test_id="test_c", mark="markB")


@pytest.mark.usefixtures("db_dir")
class TestMarkSemantics:
    """Check that filter arguments match the behavior of the old status files globs."""

    def test_mark_none_matches_all(self):
        """Check that `mark=None` matches records with and without mark."""
        _populate_test_running()
        assert len(status_db.list_test_running(mark=None)) == 3

    def test_mark_star_matches_marked(self):
        """Check that `mark="*"` matches only records with non-empty mark."""
        _populate_test_running()
        rows = status_db.list_test_running(mark="*")
        assert {r.mark for r in rows} == {"markA", "markB"}

    def test_mark_empty_matches_unmarked(self):
        """Check that `mark=""` matches only records without mark."""
        _populate_test_running()
        rows = status_db.list_test_running(mark="")
        assert len(rows) == 1
        assert rows[0].test_id == "test_a"

    def test_mark_exact(self):
        """Check that an exact mark matches only records with that mark."""
        _populate_test_running()
        rows = status_db.list_test_running(mark="markA")
        assert len(rows) == 1
        assert rows[0].worker_id == "gw1"

    def test_mark_empty_any_worker(self):
        """Check the `mark=""` + `worker_id="*"` combination (old glob post-filter case)."""
        _populate_test_running()
        rows = status_db.list_test_running(worker_id="*", mark="")
        assert len(rows) == 1
        assert rows[0].mark == ""

    def test_instance_filter(self):
        """Check filtering by instance number."""
        _populate_test_running()
        assert len(status_db.list_test_running(instance_num=0)) == 2
        assert len(status_db.list_test_running(instance_num=1)) == 1
        assert len(status_db.list_test_running(instance_num=5)) == 0

    def test_worker_filter(self):
        """Check filtering by worker ID."""
        _populate_test_running()
        assert len(status_db.list_test_running(worker_id="gw0")) == 1
        assert len(status_db.list_test_running(worker_id="gw9")) == 0

    def test_resources_mark_matrix(self):
        """Check mark semantics on resource records."""
        status_db.create_resources(
            instance_num=0, worker_id="gw0", names=["pool1"], mode=status_db.MODE_LOCK
        )
        status_db.create_resources(
            instance_num=0,
            worker_id="gw1",
            names=["pool2", "pool3"],
            mode=status_db.MODE_LOCK,
            mark="markA",
        )
        status_db.create_resources(
            instance_num=0, worker_id="gw2", names=["pool4"], mode=status_db.MODE_USE
        )

        assert status_db.get_resource_names(mode=status_db.MODE_LOCK) == [
            "pool1",
            "pool2",
            "pool3",
        ]
        assert status_db.get_resource_names(mode=status_db.MODE_LOCK, mark="") == ["pool1"]
        assert status_db.get_resource_names(mode=status_db.MODE_LOCK, mark="*") == [
            "pool2",
            "pool3",
        ]
        assert status_db.get_resource_names(mode=status_db.MODE_USE) == ["pool4"]


@pytest.mark.usefixtures("db_dir")
class TestFlags:
    """Check flag records."""

    def test_touch_idempotency(self):
        """Check that repeated creation of the same record is a no-op."""
        for _ in range(3):
            status_db.create_respin_needed(instance_num=0, worker_id="gw0")
        assert len(status_db.list_respin_needed(instance_num=0)) == 1

    def test_rm_returns_deleted(self):
        """Check that `rm_*` functions return the deleted records."""
        status_db.create_curr_mark(instance_num=0, worker_id="gw0", mark="markA")
        removed = status_db.rm_curr_mark(instance_num=0, mark="markA")
        assert len(removed) == 1
        assert removed[0].mark == "markA"
        assert status_db.rm_curr_mark(instance_num=0, mark="markA") == []

    def test_flag_types_independent(self):
        """Check that flag records of different types don't interfere."""
        status_db.create_respin_needed(instance_num=0, worker_id="gw0")
        status_db.create_respin_progress(instance_num=0, worker_id="gw0")
        status_db.rm_respin_needed(instance_num=0)
        assert status_db.list_respin_needed(instance_num=0) == []
        assert len(status_db.list_respin_progress(instance_num=0)) == 1

    def test_prio_in_progress(self):
        """Check "priority test in progress" records."""
        status_db.create_prio_in_progress(worker_id="gw0")
        assert len(status_db.list_prio_in_progress()) == 1
        assert len(status_db.list_prio_in_progress(worker_id="gw0")) == 1
        assert status_db.list_prio_in_progress(worker_id="gw1") == []
        status_db.rm_prio_in_progress(worker_id="gw0")
        assert status_db.list_prio_in_progress() == []

    def test_respin_after_mark(self):
        """Check "respin after mark" records."""
        status_db.create_respin_after_mark(instance_num=0, worker_id="gw0", mark="markA")
        assert len(status_db.list_respin_after_mark(instance_num=0)) == 1
        removed = status_db.rm_respin_after_mark(instance_num=0, mark="markA")
        assert len(removed) == 1
        assert status_db.list_respin_after_mark(instance_num=0) == []


@pytest.mark.usefixtures("db_dir")
class TestClusterState:
    """Check cluster instance state records."""

    def test_state_booleans(self):
        """Check that cluster state checks report the recorded state."""
        assert not status_db.is_cluster_running(instance_num=0)
        status_db.set_cluster_running(instance_num=0)
        assert status_db.is_cluster_running(instance_num=0)
        assert not status_db.is_cluster_running(instance_num=1)

    def test_states_coexist(self):
        """Check that running / stopped / dead states are independent records."""
        status_db.set_cluster_running(instance_num=0)
        status_db.set_cluster_stopped(instance_num=0)
        status_db.set_cluster_dead(instance_num=0)
        assert status_db.is_cluster_running(instance_num=0)
        assert status_db.is_cluster_stopped(instance_num=0)
        assert status_db.is_cluster_dead(instance_num=0)

    def test_list_cluster_dead(self):
        """Check listing of dead cluster instances."""
        status_db.set_cluster_dead(instance_num=0)
        status_db.set_cluster_dead(instance_num=2)
        assert len(status_db.list_cluster_dead()) == 2
        assert len(status_db.list_cluster_dead(instance_num=2)) == 1


@pytest.mark.usefixtures("db_dir")
class TestTestRunning:
    """Check "test running" records."""

    def test_create_overwrites(self):
        """Check that re-creating a record for the same worker overwrites it."""
        status_db.create_test_running(instance_num=0, worker_id="gw0", test_id="test_a")
        status_db.create_test_running(instance_num=0, worker_id="gw0", test_id="test_b")
        rows = status_db.list_test_running(instance_num=0)
        assert len(rows) == 1
        assert rows[0].test_id == "test_b"

    def test_get_test_names(self):
        """Check that test names are returned from the records."""
        _populate_test_running()
        assert status_db.get_test_names(instance_num=0) == ["test_a", "test_b"]

    def test_get_marks_in_progress(self):
        """Check that marks of running tests are returned."""
        _populate_test_running()
        assert status_db.get_marks_in_progress(instance_num=0) == ["markA"]
        assert sorted(status_db.get_marks_in_progress()) == ["markA", "markB"]

    def test_rm_any_mark(self):
        """Check that the default `mark=None` removes marked and unmarked records."""
        _populate_test_running()
        removed = status_db.rm_test_running(instance_num=0, worker_id="*")
        assert len(removed) == 2
        assert status_db.list_test_running(instance_num=0) == []


def _mp_create_test_running(root_tmp: pl.Path, worker_name: str) -> None:
    """Insert a "test running" record from a separate process."""
    temptools.PytestTempDirs.pytest_root_tmp = root_tmp
    status_db.create_test_running(
        instance_num=0, worker_id=worker_name, test_id=f"test_{worker_name}"
    )


def test_multiprocess_inserts(db_dir: pl.Path):
    """Check concurrent inserts from multiple processes, each with its own connection."""
    mp_ctx = multiprocessing.get_context("spawn")
    procs = [
        mp_ctx.Process(target=_mp_create_test_running, args=(db_dir, f"gw{i}")) for i in range(5)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    assert all(p.exitcode == 0 for p in procs)
    rows = status_db.list_test_running(instance_num=0)
    assert {r.worker_id for r in rows} == {f"gw{i}" for i in range(5)}
