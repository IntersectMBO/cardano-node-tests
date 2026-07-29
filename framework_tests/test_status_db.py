"""Tests for `cluster_management.status_db`."""

import multiprocessing
import os
import pathlib as pl
import time

import pytest

from cardano_node_tests.cluster_management import status_db
from cardano_node_tests.utils import temptools


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

    def test_refresh_curr_mark(self):
        """Check that refreshing "current mark" records updates their creation time.

        All records of the given mark on the given instance are refreshed together,
        records of other marks and other instances are left alone.
        """
        status_db.create_curr_mark(instance_num=0, worker_id="gw0", mark="markA")
        status_db.create_curr_mark(instance_num=0, worker_id="gw1", mark="markA")
        status_db.create_curr_mark(instance_num=0, worker_id="gw2", mark="markB")
        status_db.create_curr_mark(instance_num=1, worker_id="gw3", mark="markA")
        status_db._get_conn().execute("UPDATE flags SET created_at = 1.0")

        status_db.refresh_curr_mark(instance_num=0, mark="markA")

        refreshed = status_db.list_curr_mark(instance_num=0, mark="markA")
        assert len(refreshed) == 2
        assert all(r.created_at > 1.0 for r in refreshed)
        assert status_db.list_curr_mark(instance_num=0, mark="markB")[0].created_at == 1.0
        assert status_db.list_curr_mark(instance_num=1, mark="markA")[0].created_at == 1.0

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


@pytest.mark.usefixtures("db_dir")
class TestSnapshot:
    """Check that `StatusSnapshot` accessors match the module-level query functions."""

    def _populate(self) -> None:
        _populate_test_running()
        status_db.create_resources(
            instance_num=0, worker_id="gw0", names=["pool1"], mode=status_db.MODE_LOCK
        )
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool2"], mode=status_db.MODE_USE, mark="markA"
        )
        status_db.create_curr_mark(instance_num=0, worker_id="gw1", mark="markA")
        status_db.create_respin_needed(instance_num=1, worker_id="gw2")
        status_db.create_respin_progress(instance_num=1, worker_id="gw2")
        status_db.create_prio_in_progress(worker_id="gw0")
        status_db.set_cluster_running(instance_num=0)
        status_db.set_cluster_dead(instance_num=1)

    def test_parity(self):
        """Check that snapshot reads return the same records as the module functions."""
        self._populate()
        snap = status_db.StatusSnapshot()

        for mark in (None, "*", "", "markA"):
            assert snap.list_test_running(mark=mark) == status_db.list_test_running(mark=mark)
            for mode in (status_db.MODE_LOCK, status_db.MODE_USE):
                assert snap.get_resource_names(mode=mode, mark=mark) == (
                    status_db.get_resource_names(mode=mode, mark=mark)
                )
        for instance_num in (None, 0, 1, 5):
            assert snap.list_test_running(instance_num=instance_num) == (
                status_db.list_test_running(instance_num=instance_num)
            )
            assert snap.list_respin_needed(instance_num=instance_num) == (
                status_db.list_respin_needed(instance_num=instance_num)
            )
            assert snap.list_respin_progress(instance_num=instance_num) == (
                status_db.list_respin_progress(instance_num=instance_num)
            )
            assert snap.list_curr_mark(instance_num=instance_num) == (
                status_db.list_curr_mark(instance_num=instance_num)
            )
            assert snap.list_cluster_dead(instance_num=instance_num) == (
                status_db.list_cluster_dead(instance_num=instance_num)
            )
        assert snap.list_test_running(worker_id="gw0") == status_db.list_test_running(
            worker_id="gw0"
        )
        assert snap.list_prio_in_progress() == status_db.list_prio_in_progress()
        assert snap.get_marks_in_progress() == status_db.get_marks_in_progress()
        assert snap.is_cluster_running(instance_num=0) == status_db.is_cluster_running(
            instance_num=0
        )
        assert snap.is_cluster_dead(instance_num=1) == status_db.is_cluster_dead(instance_num=1)
        assert snap.is_cluster_stopped(instance_num=0) == status_db.is_cluster_stopped(
            instance_num=0
        )

    def test_auto_refresh_on_own_writes(self):
        """Check that the snapshot sees writes done by this process."""
        snap = status_db.StatusSnapshot()
        assert snap.list_test_running() == []

        status_db.create_test_running(instance_num=0, worker_id="gw0", test_id="test_a")
        assert len(snap.list_test_running()) == 1

        status_db.rm_test_running(instance_num=0)
        assert snap.list_test_running() == []

    def test_explicit_refresh(self):
        """Check that writes not visible via the generation counter appear after refresh."""
        snap = status_db.StatusSnapshot()
        # Simulate a write by another process - direct SQL doesn't bump the generation counter
        status_db._get_conn().execute(
            "INSERT INTO test_running (instance_num, worker_id, test_id) VALUES (0, 'gw9', 't')"
        )
        assert snap.list_test_running() == []
        snap.refresh()
        assert len(snap.list_test_running()) == 1


@pytest.mark.usefixtures("db_dir")
def test_overview_view():
    """Check that the `overview` view combines all status records."""
    status_db.create_test_running(instance_num=0, worker_id="gw0", test_id="test_a")
    status_db.create_resources(
        instance_num=0, worker_id="gw0", names=["pool1"], mode=status_db.MODE_LOCK
    )
    status_db.create_respin_needed(instance_num=1, worker_id="gw1")
    status_db.gc_stale_records(min_interval_sec=3600)

    rows = status_db._get_conn().execute("SELECT kind, item FROM overview ORDER BY kind").fetchall()

    # The "gc last run" bookkeeping record is not part of the overview
    assert [tuple(r) for r in rows] == [
        ("flag:respin_needed", "respin_needed"),
        ("resource:lock", "pool1"),
        ("test", "test_a"),
    ]


def _get_dead_pid() -> int:
    """Return PID of a process that is no longer running."""
    proc = multiprocessing.get_context("spawn").Process(target=_noop)
    proc.start()
    pid = proc.pid
    proc.join()
    assert pid is not None
    return pid


def _noop() -> None:
    """Do nothing - target for a short-lived process."""


def _set_pid(table: str, pid: int) -> None:
    """Set the writer PID on all records in the given table."""
    status_db._get_conn().execute(f"UPDATE {table} SET pid = ?", (pid,))


@pytest.mark.usefixtures("db_dir")
class TestGC:
    """Check garbage collection of records left by crashed pytest workers."""

    def test_no_dead_workers(self):
        """Check that records of running workers are kept."""
        _populate_test_running()
        assert status_db.gc_stale_records() == []
        assert len(status_db.list_test_running()) == 3

    def test_removes_dead_worker_records(self):
        """Check that records of a dead worker are removed and other records are kept."""
        dead_pid = _get_dead_pid()
        status_db.create_test_running(instance_num=0, worker_id="gw0", test_id="test_alive")
        status_db.create_test_running(instance_num=0, worker_id="gw1", test_id="test_dead")
        status_db.create_resources(
            instance_num=0, worker_id="gw1", names=["pool1"], mode=status_db.MODE_LOCK
        )
        status_db.create_prio_in_progress(worker_id="gw1")
        status_db._get_conn().execute(
            "UPDATE test_running SET pid = ? WHERE worker_id = 'gw1'", (dead_pid,)
        )
        _set_pid(table="resources", pid=dead_pid)
        _set_pid(table="flags", pid=dead_pid)

        removed = status_db.gc_stale_records()

        assert len(removed) == 3
        assert status_db.get_test_names() == ["test_alive"]
        assert status_db.get_resource_names(mode=status_db.MODE_LOCK) == []
        assert status_db.list_prio_in_progress() == []

    def test_keeps_marked_resources(self):
        """Check that marked resource records of a dead worker are kept."""
        dead_pid = _get_dead_pid()
        status_db.create_resources(
            instance_num=0,
            worker_id="gw0",
            names=["pool1"],
            mode=status_db.MODE_LOCK,
            mark="markA",
        )
        _set_pid(table="resources", pid=dead_pid)

        status_db.gc_stale_records()

        assert status_db.get_resource_names(mode=status_db.MODE_LOCK, mark="markA") == ["pool1"]

    def test_keeps_respin_needed(self):
        """Check that "needs respin" records of a dead worker are kept."""
        dead_pid = _get_dead_pid()
        status_db.create_respin_needed(instance_num=0, worker_id="gw0")
        _set_pid(table="flags", pid=dead_pid)

        status_db.gc_stale_records()

        assert len(status_db.list_respin_needed(instance_num=0)) == 1

    def test_respin_in_progress_converted(self):
        """Check that "respin in progress" of a dead worker is replaced with "needs respin"."""
        dead_pid = _get_dead_pid()
        status_db.create_respin_progress(instance_num=0, worker_id="gw0")
        _set_pid(table="flags", pid=dead_pid)

        removed = status_db.gc_stale_records()

        assert len(removed) == 1
        assert status_db.list_respin_progress(instance_num=0) == []
        assert len(status_db.list_respin_needed(instance_num=0)) == 1

    def test_throttling(self):
        """Check that garbage collection runs at most once per the given interval."""
        dead_pid = _get_dead_pid()
        status_db.create_test_running(instance_num=0, worker_id="gw0", test_id="test_dead")
        _set_pid(table="test_running", pid=dead_pid)
        assert status_db.gc_stale_records(min_interval_sec=3600) != []

        status_db.create_test_running(instance_num=0, worker_id="gw1", test_id="test_dead2")
        _set_pid(table="test_running", pid=dead_pid)
        # Second call within the interval must be throttled
        assert status_db.gc_stale_records(min_interval_sec=3600) == []
        assert len(status_db.list_test_running()) == 1
        # Unthrottled call collects the remaining stale record
        assert status_db.gc_stale_records() != []
        assert status_db.list_test_running() == []

    def test_pid_reuse_guard(self):
        """Check that a record with a mismatched process start time is treated as stale.

        The record has the PID of the current (running) process, but a `pid_start` of
        a different process incarnation, so the PID must have been reused and the
        original writer is gone.
        """
        status_db.create_test_running(instance_num=0, worker_id="gw0", test_id="test_reused")
        status_db._get_conn().execute("UPDATE test_running SET pid_start = 12345")

        removed = status_db.gc_stale_records()

        assert len(removed) == 1
        assert status_db.list_test_running() == []

    def test_pid_reuse_mixed_incarnations(self):
        """Check that only records of the dead PID incarnation are removed.

        Records of a live writer must survive garbage collection even when a stale
        record from a previous process with the same PID exists.
        """
        status_db.create_test_running(instance_num=0, worker_id="gw_live", test_id="test_live")
        status_db.create_resources(
            instance_num=0, worker_id="gw_live", names=["pool1"], mode=status_db.MODE_LOCK
        )
        # Simulate a stale record from a previous incarnation of this PID
        status_db.create_test_running(instance_num=1, worker_id="gw_dead", test_id="test_dead")
        status_db._get_conn().execute(
            "UPDATE test_running SET pid_start = 12345 WHERE worker_id = 'gw_dead'"
        )

        removed = status_db.gc_stale_records()

        assert len(removed) == 1
        assert "gw_dead" in removed[0]
        assert status_db.get_test_names() == ["test_live"]
        assert status_db.get_resource_names(mode=status_db.MODE_LOCK) == ["pool1"]

    def test_pid_start_unknown_kept(self):
        """Check that records without the writer's start time are conservatively kept."""
        status_db.create_test_running(instance_num=0, worker_id="gw0", test_id="test_a")
        status_db._get_conn().execute("UPDATE test_running SET pid_start = 0")

        assert status_db.gc_stale_records() == []
        assert len(status_db.list_test_running()) == 1

    def test_zombie_writer_is_dead(self):
        """Check that an unreaped (zombie) writer process is treated as dead.

        A crashed xdist worker stays as a zombie until the end of the pytest session,
        so the garbage collection must not consider it alive.
        """
        zombie_pid = os.fork()
        if zombie_pid == 0:
            os._exit(0)  # Child exits immediately, parent doesn't reap it yet

        try:
            # Wait until the child becomes a zombie
            for _ in range(100):
                stat = status_db._get_proc_stat(zombie_pid)
                if stat is not None and stat[0] == "Z":
                    break
                time.sleep(0.05)
            else:
                err = f"Process {zombie_pid} didn't become a zombie"
                raise AssertionError(err)

            status_db.create_test_running(instance_num=0, worker_id="gw0", test_id="test_zombie")
            status_db._get_conn().execute("UPDATE test_running SET pid = ?", (zombie_pid,))

            removed = status_db.gc_stale_records()
        finally:
            os.waitpid(zombie_pid, 0)

        assert len(removed) == 1
        assert status_db.list_test_running() == []


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
