"""SQLite-backed store for cluster instances status.

The status records are used for communication and synchronization between pytest workers.

The SQLite database file is created in the single temp directory shared by all workers
(the directory returned by `temptools.get_pytest_root_tmp()`). This allows all workers
to see status records created by other workers.

The database is a pure state store. Mutual exclusion between workers is provided by the
global cluster file lock (see `cluster_management.common.CLUSTER_LOCK`), the same way it
was when the state was kept in status files. The database is still configured defensively
(WAL journal, busy timeout) so concurrent readers - e.g. humans inspecting the database -
never block the workers.

Filter arguments semantics used throughout this module:

* `instance_num`: `None` matches any cluster instance, an int matches exactly.
* `worker_id`: `"*"` matches any pytest worker, any other string matches exactly.
* `mark`: `None` matches any record (with or without mark), `"*"` matches any non-empty
  mark, `""` matches only records without mark, any other string matches exactly.

The current status can be inspected with the stock `sqlite3` CLI. The `overview` view
combines all status records into one human-readable table:

    db=/tmp/pytest-of-$USER/pytest-0/cm-status.db
    sqlite3 -readonly -header "$db" 'SELECT * FROM overview ORDER BY instance_num, kind'

The underlying tables (`test_running`, `resources`, `flags`) can be queried directly
the same way.
"""

import contextlib
import dataclasses
import os
import sqlite3
import time
import typing as tp

from cardano_node_tests.utils import temptools

DB_NAME = "cm-status.db"

# Flag types stored in the `flags` table
GC_LAST_RUN = "gc_last_run"
RESPIN_NEEDED = "respin_needed"
RESPIN_IN_PROGRESS = "respin_in_progress"
RESPIN_AFTER_MARK = "respin_after_mark"
CURR_MARK = "curr_mark"
PRIO_IN_PROGRESS = "prio_in_progress"
CLUSTER_RUNNING = "cluster_running"
CLUSTER_STOPPED = "cluster_stopped"
CLUSTER_DEAD = "cluster_dead"

# Resource modes stored in the `resources` table
MODE_LOCK = "lock"
MODE_USE = "use"

# Value of `instance_num` for records that are not tied to any cluster instance
NO_INSTANCE = -1

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS flags (
    type          TEXT    NOT NULL,
    instance_num  INTEGER NOT NULL DEFAULT -1,
    worker_id     TEXT    NOT NULL DEFAULT '',
    mark          TEXT    NOT NULL DEFAULT '',
    pid           INTEGER NOT NULL DEFAULT 0,
    pid_start     INTEGER NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (type, instance_num, worker_id, mark)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS test_running (
    instance_num  INTEGER NOT NULL,
    worker_id     TEXT    NOT NULL,
    test_id       TEXT    NOT NULL,
    mark          TEXT    NOT NULL DEFAULT '',
    pid           INTEGER NOT NULL DEFAULT 0,
    pid_start     INTEGER NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (instance_num, worker_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS resources (
    instance_num  INTEGER NOT NULL,
    worker_id     TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    mode          TEXT    NOT NULL CHECK (mode IN ('lock', 'use')),
    mark          TEXT    NOT NULL DEFAULT '',
    pid           INTEGER NOT NULL DEFAULT 0,
    pid_start     INTEGER NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (instance_num, mode, name, worker_id)
) WITHOUT ROWID;

-- Read-only convenience view for humans inspecting the current usage
CREATE VIEW IF NOT EXISTS overview AS
SELECT
    'test' AS kind,
    instance_num,
    worker_id,
    test_id AS item,
    mark,
    CAST(strftime('%s', 'now') - created_at AS INTEGER) AS age_sec
FROM test_running
UNION ALL
SELECT
    'resource:' || mode,
    instance_num,
    worker_id,
    name,
    mark,
    CAST(strftime('%s', 'now') - created_at AS INTEGER)
FROM resources
UNION ALL
SELECT
    'flag:' || type,
    instance_num,
    worker_id,
    type,
    mark,
    CAST(strftime('%s', 'now') - created_at AS INTEGER)
FROM flags
WHERE type != '{GC_LAST_RUN}';
"""

_conn: sqlite3.Connection | None = None
_conn_pid: int = -1

# Incremented on every write done by this process. Used by `StatusSnapshot` to detect
# that its data is stale.
_write_generation: int = 0


@dataclasses.dataclass(frozen=True)
class StatusRow:
    """Single status record.

    The `test_id` field is set only for "test running" records and the `name` field is set
    only for resource records.
    """

    instance_num: int
    worker_id: str
    mark: str
    test_id: str = ""
    name: str = ""
    created_at: float = 0.0


def get_db_file() -> "os.PathLike[str]":
    """Return path to the status database file."""
    return temptools.get_pytest_root_tmp() / DB_NAME


def _get_conn() -> sqlite3.Connection:
    """Return SQLite connection for the current process.

    The connection is a lazily created per-process singleton. The PID check makes it safe
    with forked processes - a forked child gets a new connection instead of reusing the
    parent's one.
    """
    global _conn, _conn_pid  # noqa: PLW0603

    if _conn is None or _conn_pid != os.getpid():
        if sqlite3.sqlite_version_info < (3, 35):
            err = (
                "SQLite >= 3.35 is needed for the `RETURNING` clause, "
                f"got {sqlite3.sqlite_version}."
            )
            raise RuntimeError(err)
        conn = sqlite3.connect(get_db_file(), timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        # Switching to WAL needs an exclusive lock and the busy timeout does not reliably
        # apply to the journal mode change, so the pragma can fail right away with
        # "database is locked" when multiple processes create their connections
        # concurrently. Retry in that case. The journal mode is persistent, so on all
        # connections except the very first one the pragma is a no-op anyway.
        last_retry = 19
        for retry in range(last_retry + 1):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError:
                if retry == last_retry:
                    raise
                time.sleep(0.1)
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        _conn = conn
        _conn_pid = os.getpid()

    return _conn


@contextlib.contextmanager
def _transaction(conn: sqlite3.Connection) -> tp.Generator[None]:
    """Run multiple statements atomically - context manager."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _build_where(
    instance_num: int | None, worker_id: str, mark: str | None
) -> tuple[str, list[tp.Any]]:
    """Build SQL WHERE clause for the common filter arguments."""
    clauses = []
    params: list[tp.Any] = []

    if instance_num is not None:
        clauses.append("instance_num = ?")
        params.append(instance_num)

    if worker_id != "*":
        clauses.append("worker_id = ?")
        params.append(worker_id)

    if mark == "*":
        clauses.append("mark != ''")
    elif mark is not None:
        clauses.append("mark = ?")
        params.append(mark)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def _match_filters(
    row: sqlite3.Row, instance_num: int | None, worker_id: str, mark: str | None
) -> bool:
    """Check if a record matches the common filter arguments (same semantics as SQL)."""
    if instance_num is not None and row["instance_num"] != instance_num:
        return False
    if worker_id != "*" and row["worker_id"] != worker_id:
        return False
    if mark == "*":
        return bool(row["mark"])
    return mark is None or row["mark"] == mark


def _row_to_status(row: sqlite3.Row) -> StatusRow:
    keys = row.keys()
    return StatusRow(
        instance_num=row["instance_num"],
        worker_id=row["worker_id"],
        mark=row["mark"],
        test_id=row["test_id"] if "test_id" in keys else "",
        name=row["name"] if "name" in keys else "",
        created_at=row["created_at"],
    )


def _bump_write_generation() -> None:
    """Record that this process modified the database."""
    global _write_generation  # noqa: PLW0603
    _write_generation += 1


def _get_proc_stat(pid: int) -> tuple[str, int] | None:
    """Return process state and start time from `/proc`.

    The start time is in clock ticks since boot - together with the PID it uniquely
    identifies a process incarnation on a running system.

    Returns `None` when the process stat cannot be read (e.g. the process is gone,
    or the platform has no `/proc`).
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fp:
            stat = fp.read()
        # The second field (process name) can contain spaces and parentheses, fields are
        # therefore parsed from the last closing parenthesis. The process state is the
        # 3rd field and the process start time in clock ticks since boot is the 22nd field.
        fields = stat[stat.rindex(")") + 2 :].split()
        return fields[0], int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def _get_own_pid_start() -> int:
    """Return start time (in clock ticks since boot) of the current process.

    Returns 0 when the start time cannot be determined - records with `pid_start` of 0
    are exempt from the process identity check during garbage collection.
    """
    stat = _get_proc_stat(os.getpid())
    return stat[1] if stat is not None else 0


def _create_flag(ftype: str, instance_num: int, worker_id: str, mark: str = "") -> None:
    """Create (or refresh) a flag record."""
    _bump_write_generation()
    _get_conn().execute(
        "INSERT OR REPLACE INTO flags "
        "(type, instance_num, worker_id, mark, pid, pid_start, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ftype, instance_num, worker_id, mark, os.getpid(), _get_own_pid_start(), time.time()),
    )


def _list_flags(
    ftype: str, instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[StatusRow]:
    """List flag records matching the given filters."""
    where, params = _build_where(instance_num=instance_num, worker_id=worker_id, mark=mark)
    where = f"{where} AND type = ?" if where else " WHERE type = ?"
    rows = _get_conn().execute(
        f"SELECT * FROM flags{where} ORDER BY instance_num, worker_id, mark",
        [*params, ftype],
    )
    return [_row_to_status(r) for r in rows]


def _rm_flags(
    ftype: str, instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[StatusRow]:
    """Delete flag records matching the given filters and return the deleted records."""
    _bump_write_generation()
    where, params = _build_where(instance_num=instance_num, worker_id=worker_id, mark=mark)
    where = f"{where} AND type = ?" if where else " WHERE type = ?"
    rows = _get_conn().execute(f"DELETE FROM flags{where} RETURNING *", [*params, ftype]).fetchall()
    return [_row_to_status(r) for r in rows]


def _flag_exists(ftype: str, instance_num: int) -> bool:
    row = (
        _get_conn()
        .execute(
            "SELECT 1 FROM flags WHERE type = ? AND instance_num = ? LIMIT 1",
            (ftype, instance_num),
        )
        .fetchone()
    )
    return row is not None


# Cluster instance state


def set_cluster_running(instance_num: int) -> None:
    """Indicate that the cluster instance is running."""
    _create_flag(ftype=CLUSTER_RUNNING, instance_num=instance_num, worker_id="")


def set_cluster_stopped(instance_num: int) -> None:
    """Indicate that the cluster instance is stopped."""
    _create_flag(ftype=CLUSTER_STOPPED, instance_num=instance_num, worker_id="")


def set_cluster_dead(instance_num: int) -> None:
    """Indicate that the cluster instance is in broken state."""
    _create_flag(ftype=CLUSTER_DEAD, instance_num=instance_num, worker_id="")


def is_cluster_running(instance_num: int) -> bool:
    """Check if the cluster instance is running."""
    return _flag_exists(ftype=CLUSTER_RUNNING, instance_num=instance_num)


def is_cluster_stopped(instance_num: int) -> bool:
    """Check if the cluster instance is stopped."""
    return _flag_exists(ftype=CLUSTER_STOPPED, instance_num=instance_num)


def is_cluster_dead(instance_num: int) -> bool:
    """Check if the cluster instance is in broken state."""
    return _flag_exists(ftype=CLUSTER_DEAD, instance_num=instance_num)


def list_cluster_dead(instance_num: int | None = None) -> list[StatusRow]:
    """List all "cluster dead" records."""
    return _list_flags(ftype=CLUSTER_DEAD, instance_num=instance_num)


# Respin flags


def create_respin_needed(instance_num: int, worker_id: str) -> None:
    """Indicate that the cluster instance needs respin."""
    _create_flag(ftype=RESPIN_NEEDED, instance_num=instance_num, worker_id=worker_id)


def list_respin_needed(instance_num: int | None = None, worker_id: str = "*") -> list[StatusRow]:
    """List all "needs respin" records."""
    return _list_flags(ftype=RESPIN_NEEDED, instance_num=instance_num, worker_id=worker_id)


def rm_respin_needed(instance_num: int | None = None, worker_id: str = "*") -> list[StatusRow]:
    """Delete all "needs respin" records."""
    return _rm_flags(ftype=RESPIN_NEEDED, instance_num=instance_num, worker_id=worker_id)


def create_respin_progress(instance_num: int, worker_id: str) -> None:
    """Indicate that respin of the cluster instance is in progress."""
    _create_flag(ftype=RESPIN_IN_PROGRESS, instance_num=instance_num, worker_id=worker_id)


def list_respin_progress(instance_num: int | None = None, worker_id: str = "*") -> list[StatusRow]:
    """List all "respin in progress" records."""
    return _list_flags(ftype=RESPIN_IN_PROGRESS, instance_num=instance_num, worker_id=worker_id)


def rm_respin_progress(instance_num: int | None = None, worker_id: str = "*") -> list[StatusRow]:
    """Delete all "respin in progress" records."""
    return _rm_flags(ftype=RESPIN_IN_PROGRESS, instance_num=instance_num, worker_id=worker_id)


def create_respin_after_mark(instance_num: int, worker_id: str, mark: str) -> None:
    """Indicate that the cluster instance needs respin after marked tests are finished."""
    _create_flag(ftype=RESPIN_AFTER_MARK, instance_num=instance_num, worker_id=worker_id, mark=mark)


def list_respin_after_mark(
    instance_num: int | None = None, worker_id: str = "*", mark: str = "*"
) -> list[StatusRow]:
    """List all "respin after mark" records."""
    return _list_flags(
        ftype=RESPIN_AFTER_MARK, instance_num=instance_num, worker_id=worker_id, mark=mark
    )


def rm_respin_after_mark(
    instance_num: int | None = None, worker_id: str = "*", mark: str = "*"
) -> list[StatusRow]:
    """Delete all "respin after mark" records."""
    return _rm_flags(
        ftype=RESPIN_AFTER_MARK, instance_num=instance_num, worker_id=worker_id, mark=mark
    )


# Marked tests


def create_curr_mark(instance_num: int, worker_id: str, mark: str) -> None:
    """Indicate presence of marked test on a pytest worker."""
    _create_flag(ftype=CURR_MARK, instance_num=instance_num, worker_id=worker_id, mark=mark)


def list_curr_mark(
    instance_num: int | None = None, worker_id: str = "*", mark: str = "*"
) -> list[StatusRow]:
    """List all "current mark" records."""
    return _list_flags(ftype=CURR_MARK, instance_num=instance_num, worker_id=worker_id, mark=mark)


def rm_curr_mark(
    instance_num: int | None = None, worker_id: str = "*", mark: str = "*"
) -> list[StatusRow]:
    """Delete all "current mark" records."""
    return _rm_flags(ftype=CURR_MARK, instance_num=instance_num, worker_id=worker_id, mark=mark)


def refresh_curr_mark(instance_num: int, mark: str) -> None:
    """Update creation time of "current mark" records to the current time.

    Called whenever a test with the given mark is seen running or finishes, so the age
    of a "current mark" record tells for how long no marked test was running. All the
    mark's records (one per worker) are refreshed together, as the mark is also cleaned
    up as a whole.
    """
    cur = _get_conn().execute(
        "UPDATE flags SET created_at = ? WHERE type = ? AND instance_num = ? AND mark = ?",
        (time.time(), CURR_MARK, instance_num, mark),
    )
    if cur.rowcount > 0:
        _bump_write_generation()


# Priority tests


def create_prio_in_progress(worker_id: str) -> None:
    """Indicate that priority test is in progress."""
    _create_flag(ftype=PRIO_IN_PROGRESS, instance_num=NO_INSTANCE, worker_id=worker_id)


def list_prio_in_progress(worker_id: str = "*") -> list[StatusRow]:
    """List all "priority test in progress" records."""
    return _list_flags(ftype=PRIO_IN_PROGRESS, worker_id=worker_id)


def rm_prio_in_progress(worker_id: str = "*") -> list[StatusRow]:
    """Delete all "priority test in progress" records."""
    return _rm_flags(ftype=PRIO_IN_PROGRESS, worker_id=worker_id)


# Running tests


def create_test_running(instance_num: int, worker_id: str, test_id: str, mark: str = "") -> None:
    """Indicate that a test is running on a pytest worker."""
    _bump_write_generation()
    _get_conn().execute(
        "INSERT OR REPLACE INTO test_running "
        "(instance_num, worker_id, test_id, mark, pid, pid_start, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (instance_num, worker_id, test_id, mark, os.getpid(), _get_own_pid_start(), time.time()),
    )


def list_test_running(
    instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[StatusRow]:
    """List all "test running" records."""
    where, params = _build_where(instance_num=instance_num, worker_id=worker_id, mark=mark)
    rows = _get_conn().execute(
        f"SELECT * FROM test_running{where} ORDER BY instance_num, worker_id",
        params,
    )
    return [_row_to_status(r) for r in rows]


def rm_test_running(
    instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[StatusRow]:
    """Delete all "test running" records."""
    _bump_write_generation()
    where, params = _build_where(instance_num=instance_num, worker_id=worker_id, mark=mark)
    rows = _get_conn().execute(f"DELETE FROM test_running{where} RETURNING *", params).fetchall()
    return [_row_to_status(r) for r in rows]


def get_test_names(
    instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[str]:
    """Return list of test names that are currently running."""
    return [
        r.test_id
        for r in list_test_running(instance_num=instance_num, worker_id=worker_id, mark=mark)
    ]


def get_marks_in_progress(instance_num: int | None = None, worker_id: str = "*") -> list[str]:
    """Return list of marks of currently running tests."""
    return [
        r.mark for r in list_test_running(instance_num=instance_num, worker_id=worker_id, mark="*")
    ]


# Resources


def create_resources(
    instance_num: int, worker_id: str, names: tp.Iterable[str], mode: str, mark: str = ""
) -> None:
    """Create records that indicate that the given resources are locked or in use.

    Args:
        instance_num: Cluster instance number.
        worker_id: Pytest worker ID.
        names: Names of the resources.
        mode: Either `MODE_LOCK` or `MODE_USE`.
        mark: Test mark, empty string for no mark.
    """
    _bump_write_generation()
    conn = _get_conn()
    pid = os.getpid()
    pid_start = _get_own_pid_start()
    created_at = time.time()
    with _transaction(conn):
        for name in names:
            conn.execute(
                "INSERT OR REPLACE INTO resources "
                "(instance_num, worker_id, name, mode, mark, pid, pid_start, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (instance_num, worker_id, name, mode, mark, pid, pid_start, created_at),
            )


def list_resources(
    mode: str, instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[StatusRow]:
    """List all resource records for the given mode."""
    where, params = _build_where(instance_num=instance_num, worker_id=worker_id, mark=mark)
    where = f"{where} AND mode = ?" if where else " WHERE mode = ?"
    rows = _get_conn().execute(
        f"SELECT * FROM resources{where} ORDER BY instance_num, name, worker_id",
        [*params, mode],
    )
    return [_row_to_status(r) for r in rows]


def rm_resources(
    mode: str, instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[StatusRow]:
    """Delete all resource records for the given mode."""
    _bump_write_generation()
    where, params = _build_where(instance_num=instance_num, worker_id=worker_id, mark=mark)
    where = f"{where} AND mode = ?" if where else " WHERE mode = ?"
    rows = (
        _get_conn().execute(f"DELETE FROM resources{where} RETURNING *", [*params, mode]).fetchall()
    )
    return [_row_to_status(r) for r in rows]


def get_resource_names(
    mode: str, instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[str]:
    """Return names of resources that are locked or in use."""
    return [
        r.name
        for r in list_resources(
            mode=mode, instance_num=instance_num, worker_id=worker_id, mark=mark
        )
    ]


# Garbage collection of records left by crashed pytest workers


def _is_writer_alive(pid: int, pid_start: int) -> bool:
    """Check if the process that created status records is still running.

    The writer is dead when its process no longer exists, when it is a zombie (a crashed
    xdist worker stays unreaped until the end of the pytest session), or when the PID was
    reused by a different process - the process start time (in clock ticks since boot)
    doesn't match the one recorded when the records were created.

    When the process state cannot be inspected, or when the records don't carry the
    writer's start time, the writer is conservatively considered alive - stale records
    are less harmful than records deleted while their writer is still running.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    stat = _get_proc_stat(pid)
    if stat is None:
        # The process disappeared between the checks, or `/proc` is not available
        return True

    state, start_ticks = stat
    if state == "Z":
        return False

    return not (pid_start and start_ticks != pid_start)


def gc_stale_records(min_interval_sec: float = 0.0) -> list[str]:
    """Remove status records left by pytest workers that are no longer running.

    Must be called under the global cluster lock.

    When `min_interval_sec` is set, the garbage collection runs at most once per that
    interval across all workers - the last-run timestamp is kept in the database.
    Throttled calls return an empty list without scanning.

    Only records whose validity depends on the writer being alive are removed:

    * "test running" records - the test is not running anymore.
    * Resource records without mark - the test that held them is gone. Marked resource
      records belong to the whole group of marked tests and are cleaned up by the mark
      staleness handling, or removed together with the marks when the cluster instance
      is respun or failed to start.
    * "priority test in progress" flags - would otherwise block all other workers forever.
    * "respin in progress" flags - the cluster instance was left in an unknown state, so
      a "needs respin" flag is created in its place.

    The "needs respin" and "respin after mark" flags are kept even when their writer died,
    as the cluster instance still needs the respin. The "current mark" flags are kept too -
    once the ghost "test running" records are removed, the existing mark staleness handling
    cleans them up.

    Returns descriptions of the removed records, for logging.
    """
    conn = _get_conn()

    if min_interval_sec > 0:
        last_run = conn.execute(
            "SELECT created_at FROM flags WHERE type = ? LIMIT 1", (GC_LAST_RUN,)
        ).fetchone()
        if last_run is not None and time.time() - last_run["created_at"] < min_interval_sec:
            return []
    _create_flag(ftype=GC_LAST_RUN, instance_num=NO_INSTANCE, worker_id="")

    # A writer is identified by the (pid, pid_start) pair. Rows sharing a PID can come
    # from different process incarnations (PID reuse), so staleness is decided per
    # identity, never for a bare PID.
    writer_rows = conn.execute(
        "SELECT DISTINCT pid, pid_start FROM ("
        " SELECT pid, pid_start FROM test_running"
        " UNION ALL SELECT pid, pid_start FROM resources WHERE mark = ''"
        " UNION ALL SELECT pid, pid_start FROM flags WHERE type IN (?, ?)"
        ") WHERE pid > 0",
        (PRIO_IN_PROGRESS, RESPIN_IN_PROGRESS),
    ).fetchall()

    dead_writers = [
        (r["pid"], r["pid_start"])
        for r in writer_rows
        if not _is_writer_alive(pid=r["pid"], pid_start=r["pid_start"])
    ]
    if not dead_writers:
        return []

    removed: list[str] = []
    _bump_write_generation()
    with _transaction(conn):
        for pid, pid_start in dead_writers:
            removed.extend(
                f"'test running' of dead worker {r['worker_id']} (pid {r['pid']}) "
                f"on c{r['instance_num']}: {r['test_id']}"
                for r in conn.execute(
                    "DELETE FROM test_running WHERE pid = ? AND pid_start = ? RETURNING *",
                    (pid, pid_start),
                ).fetchall()
            )

            removed.extend(
                f"resource '{r['name']}' ({r['mode']}) of dead worker {r['worker_id']} "
                f"(pid {r['pid']}) on c{r['instance_num']}"
                for r in conn.execute(
                    "DELETE FROM resources WHERE mark = '' AND pid = ? AND pid_start = ? "
                    "RETURNING *",
                    (pid, pid_start),
                ).fetchall()
            )

            removed.extend(
                f"'prio in progress' of dead worker {r['worker_id']} (pid {r['pid']})"
                for r in conn.execute(
                    "DELETE FROM flags WHERE type = ? AND pid = ? AND pid_start = ? RETURNING *",
                    (PRIO_IN_PROGRESS, pid, pid_start),
                ).fetchall()
            )

            # A worker that died in the middle of a respin left the cluster instance in an
            # unknown state. Replace its "respin in progress" flag with "needs respin" so
            # another worker respins the instance.
            respin_rows = conn.execute(
                "DELETE FROM flags WHERE type = ? AND pid = ? AND pid_start = ? RETURNING *",
                (RESPIN_IN_PROGRESS, pid, pid_start),
            ).fetchall()
            for r in respin_rows:
                conn.execute(
                    "INSERT OR REPLACE INTO flags "
                    "(type, instance_num, worker_id, mark, pid, pid_start, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        RESPIN_NEEDED,
                        r["instance_num"],
                        r["worker_id"],
                        "",
                        os.getpid(),
                        _get_own_pid_start(),
                        time.time(),
                    ),
                )
                removed.append(
                    f"'respin in progress' of dead worker {r['worker_id']} (pid {r['pid']}) "
                    f"on c{r['instance_num']}, created 'needs respin'"
                )

    return removed


# Snapshot


class StatusSnapshot:
    """In-memory view of all status records.

    The snapshot is loaded with one query per table and its read accessors mirror the
    module-level query functions, including the filter argument semantics. Reads from
    the snapshot are plain list traversals, so e.g. the scheduler can evaluate all
    cluster instances without doing repeated database queries.

    The snapshot refreshes itself when this process modifies the database (tracked by
    the module-level write generation counter). Writes done by other processes are NOT
    visible until `refresh` is called explicitly. Users must therefore hold the global
    cluster lock while using the snapshot and must call `refresh` right after acquiring
    the lock.
    """

    def __init__(self) -> None:
        self._generation = -1
        self._flags: list[sqlite3.Row] = []
        self._test_running: list[sqlite3.Row] = []
        self._resources: list[sqlite3.Row] = []
        self.refresh()

    def refresh(self) -> None:
        """Reload all status records from the database."""
        conn = _get_conn()
        self._flags = conn.execute(
            "SELECT * FROM flags ORDER BY instance_num, worker_id, mark"
        ).fetchall()
        self._test_running = conn.execute(
            "SELECT * FROM test_running ORDER BY instance_num, worker_id"
        ).fetchall()
        self._resources = conn.execute(
            "SELECT * FROM resources ORDER BY instance_num, name, worker_id"
        ).fetchall()
        self._generation = _write_generation

    def _maybe_refresh(self) -> None:
        """Refresh the snapshot if this process modified the database in the meantime."""
        if self._generation != _write_generation:
            self.refresh()

    def _list_flags(
        self,
        ftype: str,
        instance_num: int | None = None,
        worker_id: str = "*",
        mark: str | None = None,
    ) -> list[StatusRow]:
        self._maybe_refresh()
        return [
            _row_to_status(r)
            for r in self._flags
            if r["type"] == ftype
            and _match_filters(r, instance_num=instance_num, worker_id=worker_id, mark=mark)
        ]

    # Cluster instance state

    def is_cluster_running(self, instance_num: int) -> bool:
        """Check if the cluster instance is running."""
        return bool(self._list_flags(ftype=CLUSTER_RUNNING, instance_num=instance_num))

    def is_cluster_stopped(self, instance_num: int) -> bool:
        """Check if the cluster instance is stopped."""
        return bool(self._list_flags(ftype=CLUSTER_STOPPED, instance_num=instance_num))

    def is_cluster_dead(self, instance_num: int) -> bool:
        """Check if the cluster instance is in broken state."""
        return bool(self._list_flags(ftype=CLUSTER_DEAD, instance_num=instance_num))

    def list_cluster_dead(self, instance_num: int | None = None) -> list[StatusRow]:
        """List all "cluster dead" records."""
        return self._list_flags(ftype=CLUSTER_DEAD, instance_num=instance_num)

    # Respin flags

    def list_respin_needed(
        self, instance_num: int | None = None, worker_id: str = "*"
    ) -> list[StatusRow]:
        """List all "needs respin" records."""
        return self._list_flags(ftype=RESPIN_NEEDED, instance_num=instance_num, worker_id=worker_id)

    def list_respin_progress(
        self, instance_num: int | None = None, worker_id: str = "*"
    ) -> list[StatusRow]:
        """List all "respin in progress" records."""
        return self._list_flags(
            ftype=RESPIN_IN_PROGRESS, instance_num=instance_num, worker_id=worker_id
        )

    # Marked tests

    def list_curr_mark(
        self, instance_num: int | None = None, worker_id: str = "*", mark: str = "*"
    ) -> list[StatusRow]:
        """List all "current mark" records."""
        return self._list_flags(
            ftype=CURR_MARK, instance_num=instance_num, worker_id=worker_id, mark=mark
        )

    # Priority tests

    def list_prio_in_progress(self, worker_id: str = "*") -> list[StatusRow]:
        """List all "priority test in progress" records."""
        return self._list_flags(ftype=PRIO_IN_PROGRESS, worker_id=worker_id)

    # Running tests

    def list_test_running(
        self, instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
    ) -> list[StatusRow]:
        """List all "test running" records."""
        self._maybe_refresh()
        return [
            _row_to_status(r)
            for r in self._test_running
            if _match_filters(r, instance_num=instance_num, worker_id=worker_id, mark=mark)
        ]

    def get_marks_in_progress(
        self, instance_num: int | None = None, worker_id: str = "*"
    ) -> list[str]:
        """Return list of marks of currently running tests."""
        return [
            r.mark
            for r in self.list_test_running(
                instance_num=instance_num, worker_id=worker_id, mark="*"
            )
        ]

    # Resources

    def get_resource_names(
        self,
        mode: str,
        instance_num: int | None = None,
        worker_id: str = "*",
        mark: str | None = None,
    ) -> list[str]:
        """Return names of resources that are locked or in use."""
        self._maybe_refresh()
        return [
            r["name"]
            for r in self._resources
            if r["mode"] == mode
            and _match_filters(r, instance_num=instance_num, worker_id=worker_id, mark=mark)
        ]
