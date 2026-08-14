"""Exclusive use of a dev cluster by a single pytest invocation.

With `DEV_CLUSTER_RUNNING`, multiple concurrent pytest invocations (possibly from different
git worktrees) can target the same dev cluster instance. The cluster management locks live
under the invocation-specific pytest temp dir, so they cannot coordinate across invocations.
Instead, each invocation records its identity in a claim file next to the dev cluster state
dir, and refuses to run when the cluster is already claimed by another live invocation.
"""

import json
import logging
import os
import pathlib as pl

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)

SESSION_PREFIX = ".pytest_session"
LOCK_TIMEOUT = 30


def _get_state_dir() -> pl.Path:
    """Return path to the state dir of the dev cluster."""
    return configuration.STARTUP_CARDANO_NODE_SOCKET_PATH.parent


def _get_session_file() -> pl.Path:
    """Return path to the file that records the pytest invocation using the dev cluster.

    The file cannot live in the cluster state dir, as the state dir is recreated when
    the devnet is started. It cannot live under the pytest temp dir (invocation-specific)
    or `TMPDIR` (worktree-specific) either. Use the parent of the state dir (the dev
    cluster work dir) instead - it is shared by all pytest invocations that target the
    same dev cluster instance and it survives devnet restarts.
    """
    state_dir = _get_state_dir()
    return state_dir.parent / f"{SESSION_PREFIX}_{state_dir.name}"


def _get_session_id() -> dict:
    """Return identity of this pytest invocation recorded in the dev session file."""
    return {
        "root_tmp": str(temptools.get_pytest_root_tmp()),
        # All xdist workers of a single pytest invocation are children of the same
        # controller process
        "pid": os.getppid() if configuration.IS_XDIST else os.getpid(),
    }


def _is_pid_running(pid: int) -> bool:
    """Check whether a process with the given PID is running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _get_recorded_pid(session_data: dict) -> int:
    """Return the PID recorded in the session data, or 0 when missing or invalid."""
    try:
        return int(session_data.get("pid") or 0)
    except (TypeError, ValueError):
        return 0


def _load_session_data(session_file: pl.Path) -> dict:
    """Load recorded dev session data, treating a corrupt file as empty."""
    try:
        session_data = json.loads(session_file.read_text())
    # OSError e.g. when the file was deleted after the `exists()` check, ValueError covers
    # both invalid JSON and a torn write that is not valid UTF-8
    except (OSError, ValueError):
        session_data = None

    if not isinstance(session_data, dict):
        LOGGER.warning(f"Corrupt dev cluster session file '{session_file}'.")
        return {}

    return session_data


def claim_session() -> None:
    """Claim exclusive use of the dev cluster for this pytest invocation.

    Raises:
        RuntimeError: When the dev cluster is already used by another live pytest invocation,
            or when the session lock cannot be acquired.
    """
    session_file = _get_session_file()
    session_file.parent.mkdir(exist_ok=True)
    session_id = _get_session_id()
    lock_file = f"{session_file}.lock"

    try:
        # `locking.Timeout` cannot originate from the `with` block body, as there is no other
        # lock acquisition inside - only the acquisition on `with` entry can raise it
        with locking.FileLock(lock_file, timeout=LOCK_TIMEOUT):
            if session_file.exists():
                session_data = _load_session_data(session_file=session_file)

                other_root_tmp = session_data.get("root_tmp")
                other_pid = _get_recorded_pid(session_data=session_data)

                if other_root_tmp == session_id["root_tmp"] and other_pid == session_id["pid"]:
                    # Another xdist worker of this pytest invocation already claimed the cluster
                    return

                # A recorded PID that matches this invocation but with a different `root_tmp`
                # can only be a recycled PID - a live claim by this invocation matches on both
                # fields.
                if other_pid != session_id["pid"] and _is_pid_running(pid=other_pid):
                    msg = (
                        f"The dev cluster in '{_get_state_dir()}' is already used by another "
                        f"pytest invocation (PID {other_pid}, temp dir '{other_root_tmp}'). "
                        f"Wait for it to finish first, or delete '{session_file}' "
                        f"if the record is stale."
                    )
                    raise RuntimeError(msg)

                if other_pid == session_id["pid"]:
                    reason = f"PID {other_pid} was recycled and is now this invocation"
                elif other_pid:
                    reason = f"PID {other_pid} not running"
                else:
                    reason = "no valid PID recorded"

                LOGGER.warning(
                    f"Ignoring stale dev cluster session file '{session_file}' ({reason})."
                )

            session_file.write_text(json.dumps(session_id))
    except locking.Timeout as excp:
        msg = (
            f"Could not acquire the dev cluster session lock '{lock_file}' "
            f"in {LOCK_TIMEOUT}s - another pytest invocation may be stuck."
        )
        raise RuntimeError(msg) from excp


def release_session() -> None:
    """Release the dev cluster claimed by this pytest invocation.

    Best-effort - failures are logged but never raised, so a failed release cannot mask
    cleanup errors in the session teardown.
    """
    session_file: pl.Path | None = None
    try:
        session_file = _get_session_file()
        session_id = _get_session_id()

        with locking.FileLock(f"{session_file}.lock", timeout=LOCK_TIMEOUT):
            if not session_file.exists():
                return

            session_data = _load_session_data(session_file=session_file)
            if (
                session_data.get("root_tmp") == session_id["root_tmp"]
                and _get_recorded_pid(session_data=session_data) == session_id["pid"]
            ):
                session_file.unlink(missing_ok=True)
    except Exception:
        LOGGER.exception(f"Failed to release dev cluster session file '{session_file}'.")
