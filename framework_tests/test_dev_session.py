"""Unit tests for `cardano_node_tests.utils.dev_session`.

The session file location, the invocation identity and the PID-liveness check are
monkeypatched, so the tests don't depend on a configured cluster environment, on pytest
temp dirs, or on the set of processes running on the machine.
"""

import json
import os
import pathlib as pl

import pytest

from cardano_node_tests.utils import dev_session

LIVE_PIDS = {111, 222}


@pytest.fixture
def session_file(tmp_path: pl.Path, monkeypatch: pytest.MonkeyPatch) -> pl.Path:
    """Return a session file path in a temp work dir; patch its lookup and PID liveness."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    s_file = work_dir / f"{dev_session.SESSION_PREFIX}_state-cluster0"
    monkeypatch.setattr(dev_session, "_get_session_file", lambda: s_file)
    monkeypatch.setattr(dev_session, "_is_pid_running", lambda pid: pid in LIVE_PIDS)
    return s_file


def _set_invocation(monkeypatch: pytest.MonkeyPatch, root_tmp: str, pid: int) -> None:
    """Patch the identity of the "current" pytest invocation."""
    monkeypatch.setattr(dev_session, "_get_session_id", lambda: {"root_tmp": root_tmp, "pid": pid})


class TestClaimRelease:
    """Tests for `claim_session` and `release_session`."""

    def test_claim_and_release(self, session_file: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Claim the cluster, re-claim from another worker, release it."""
        _set_invocation(monkeypatch, root_tmp="run1", pid=111)
        dev_session.claim_session()
        assert json.loads(session_file.read_text()) == {"root_tmp": "run1", "pid": 111}

        # Another xdist worker of the same invocation re-claims without error
        dev_session.claim_session()

        dev_session.release_session()
        assert not session_file.exists()

    def test_second_invocation_blocked(
        self, session_file: pl.Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Refuse to claim the cluster used by another live invocation."""
        _set_invocation(monkeypatch, root_tmp="run1", pid=111)
        dev_session.claim_session()

        _set_invocation(monkeypatch, root_tmp="run2", pid=222)
        with pytest.raises(RuntimeError, match="already used by another"):
            dev_session.claim_session()

        # The rejected claim must not modify the original owner's record
        assert json.loads(session_file.read_text()) == {"root_tmp": "run1", "pid": 111}

    def test_release_by_non_owner(self, session_file: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Keep the session file when released by an invocation that doesn't own it."""
        _set_invocation(monkeypatch, root_tmp="run1", pid=111)
        dev_session.claim_session()

        _set_invocation(monkeypatch, root_tmp="run2", pid=222)
        dev_session.release_session()
        assert session_file.exists()

    def test_release_without_session_file(
        self, session_file: pl.Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Do nothing when there is no session file to release."""
        _set_invocation(monkeypatch, root_tmp="run1", pid=111)
        dev_session.release_session()
        assert not session_file.exists()

    def test_claim_lock_timeout(self, session_file: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Raise a helpful error when the session lock cannot be acquired in time."""
        _set_invocation(monkeypatch, root_tmp="run1", pid=111)
        monkeypatch.setattr(dev_session, "LOCK_TIMEOUT", 0.1)

        with (
            dev_session.locking.FileLock(f"{session_file}.lock"),
            pytest.raises(RuntimeError, match="Could not acquire"),
        ):
            dev_session.claim_session()

    def test_release_never_raises(self, session_file: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Log and swallow unexpected errors during release instead of raising."""
        _set_invocation(monkeypatch, root_tmp="run1", pid=111)
        dev_session.claim_session()

        def _broken_lock(*_args: object, **_kwargs: object) -> None:
            err = "Lock is broken"
            raise RuntimeError(err)

        monkeypatch.setattr(dev_session.locking, "FileLock", _broken_lock)
        dev_session.release_session()
        # The claim is left behind when release fails
        assert session_file.exists()


class TestStaleClaims:
    """Tests for handling stale or corrupt session files."""

    def test_dead_pid_takeover(self, session_file: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Take over a session file that records a PID that is no longer running."""
        session_file.write_text(json.dumps({"root_tmp": "run1", "pid": 999}))
        _set_invocation(monkeypatch, root_tmp="run2", pid=222)
        dev_session.claim_session()
        assert json.loads(session_file.read_text()) == {"root_tmp": "run2", "pid": 222}

    def test_recycled_pid_takeover(self, session_file: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Treat a recorded PID that matches this invocation but different temp dir as stale."""
        session_file.write_text(json.dumps({"root_tmp": "runX", "pid": 222}))
        _set_invocation(monkeypatch, root_tmp="run2", pid=222)
        dev_session.claim_session()
        assert json.loads(session_file.read_text()) == {"root_tmp": "run2", "pid": 222}

    @pytest.mark.parametrize(
        "content",
        ["{not json", "42", json.dumps({"root_tmp": "runX", "pid": "bogus"})],
        ids=["garbage", "non_dict", "bogus_pid"],
    )
    def test_corrupt_file_takeover(
        self, session_file: pl.Path, monkeypatch: pytest.MonkeyPatch, content: str
    ):
        """Take over a session file with corrupt or invalid content."""
        session_file.write_text(content)
        _set_invocation(monkeypatch, root_tmp="run1", pid=111)
        dev_session.claim_session()
        assert json.loads(session_file.read_text()) == {"root_tmp": "run1", "pid": 111}

    def test_corrupt_file_release(self, session_file: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Never crash on release when the session file is corrupt."""
        session_file.write_text("{not json")
        _set_invocation(monkeypatch, root_tmp="run1", pid=111)
        dev_session.release_session()
        assert session_file.exists()


class TestHelpers:
    """Tests for the module helpers."""

    def test_is_pid_running(self):
        """Report PID liveness, rejecting non-positive PIDs."""
        assert dev_session._is_pid_running(os.getpid())
        assert not dev_session._is_pid_running(0)
        assert not dev_session._is_pid_running(-1)
        assert not dev_session._is_pid_running(2**22 + 10000)  # Above default pid_max

    def test_session_file_location(self, monkeypatch: pytest.MonkeyPatch):
        """Derive the session file path from the startup socket path."""
        monkeypatch.setattr(
            dev_session.configuration,
            "STARTUP_CARDANO_NODE_SOCKET_PATH",
            pl.Path("/workdir/state-cluster0/bft1.socket"),
        )
        assert dev_session._get_session_file() == pl.Path(
            f"/workdir/{dev_session.SESSION_PREFIX}_state-cluster0"
        )

    def test_session_id(self, monkeypatch: pytest.MonkeyPatch):
        """Record the pytest root temp dir and the controller PID."""
        monkeypatch.setattr(
            dev_session.temptools, "get_pytest_root_tmp", lambda: pl.Path("/tmp/pytest-run")
        )
        monkeypatch.setattr(dev_session.configuration, "IS_XDIST", False)
        assert dev_session._get_session_id() == {
            "root_tmp": "/tmp/pytest-run",
            "pid": os.getpid(),
        }

        monkeypatch.setattr(dev_session.configuration, "IS_XDIST", True)
        assert dev_session._get_session_id()["pid"] == os.getppid()
