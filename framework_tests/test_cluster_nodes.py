"""Unit tests for `cardano_node_tests.utils.cluster_nodes`.

The tests must not depend on external binaries or a running cluster -
`run_command` and the cluster environment lookups are monkeypatched.
"""

import json
import os
import pathlib as pl
import types
import typing as tp

import pytest

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import helpers


class TestStartCluster:
    """Tests for `start_cluster`."""

    def test_argv_passthrough(self, monkeypatch: pytest.MonkeyPatch):
        """Pass the start script and its args to run_command as an argv list."""
        recorded: dict[str, tp.Any] = {}

        def _fake_run_command(command: list, **kwargs: tp.Any) -> bytes:
            recorded["command"] = command
            recorded["workdir"] = kwargs.get("workdir")
            recorded["merge_stderr"] = kwargs.get("merge_stderr")
            return b""

        cluster_obj = object()
        fake_env = types.SimpleNamespace(work_dir=pl.Path("/work/dir"))
        fake_type = types.SimpleNamespace(get_cluster_obj=lambda: cluster_obj)

        monkeypatch.setattr(helpers, "run_command", _fake_run_command)
        monkeypatch.setattr(cluster_nodes, "get_cluster_env", lambda: fake_env)
        monkeypatch.setattr(cluster_nodes, "get_cluster_type", lambda: fake_type)

        ret = cluster_nodes.start_cluster(cmd="start-script", args=["arg with space", "b'c"])

        assert recorded["command"] == ["start-script", "arg with space", "b'c"]
        assert recorded["workdir"] == pl.Path("/work/dir")
        assert recorded["merge_stderr"] is True
        assert ret is cluster_obj


def _status(
    name: str, status: str = "RUNNING", uptime: str | None = "5:00:00", message: str = ""
) -> cluster_nodes.ServiceStatus:
    """Return a `ServiceStatus` of a service that has been running for a while by default."""
    return cluster_nodes.ServiceStatus(
        name=name, status=status, pid=None, uptime=uptime, message=message
    )


class TestGetUptimeSec:
    """Tests for `_get_uptime_sec`."""

    @pytest.mark.parametrize(
        ("uptime", "message", "expected"),
        [
            ("0:04:05", "", 245.0),
            ("1", "day, 0:00:10", 86_410.0),
            ("2", "days, 1:00:00", 2 * 86_400 + 3_600.0),
            (None, "Not started", None),
            ("garbage", "", None),
        ],
    )
    def test_parse(self, uptime: str | None, message: str, expected: float | None):
        """Parse the uptime as reported by supervisor."""
        status = _status("nodes:pool1", uptime=uptime, message=message)
        assert cluster_nodes._get_uptime_sec(status) == expected


class TestGetStalledNodes:
    """Tests for `get_stalled_nodes`."""

    # k=4, f=0.05, slotLength=1 -> forecast horizon 3k/f = 240 seconds
    HORIZON_SEC = 240
    SYSTEM_START = 1_000_000.0

    def _make_state_dir(self, tmp_path: pl.Path) -> pl.Path:
        shelley_dir = tmp_path / "shelley"
        shelley_dir.mkdir()
        genesis = {
            "securityParam": 4,
            "activeSlotsCoeff": 0.05,
            "slotLength": 1,
            "systemStart": "1970-01-12T13:46:40Z",  # SYSTEM_START
        }
        (shelley_dir / "genesis.json").write_text(json.dumps(genesis), encoding="utf-8")
        return tmp_path

    def _add_block(self, state_dir: pl.Path, node_name: str, mtime: float) -> pl.Path:
        volatile_dir = state_dir / f"db-{node_name}" / "volatile"
        volatile_dir.mkdir(parents=True, exist_ok=True)
        blocks_file = volatile_dir / "blocks-0.dat"
        blocks_file.write_bytes(b"block")
        os.utime(blocks_file, (mtime, mtime))
        os.utime(volatile_dir, (mtime, mtime))
        return blocks_file

    def test_fresh_and_stalled(self, tmp_path: pl.Path):
        """Report only the node whose last block is older than the forecast horizon."""
        state_dir = self._make_state_dir(tmp_path)
        now = self.SYSTEM_START + 100_000
        self._add_block(state_dir, "pool1", mtime=now - 10)
        self._add_block(state_dir, "pool3", mtime=now - self.HORIZON_SEC - 1)

        stalled = cluster_nodes.get_stalled_nodes(
            [_status("nodes:pool1"), _status("nodes:pool3")], state_dir=state_dir, now=now
        )

        assert stalled == ["pool3"]

    def test_within_horizon(self, tmp_path: pl.Path):
        """Don't report a node that is quiet for less than the forecast horizon."""
        state_dir = self._make_state_dir(tmp_path)
        now = self.SYSTEM_START + 100_000
        self._add_block(state_dir, "pool1", mtime=now - self.HORIZON_SEC + 1)

        assert not cluster_nodes.get_stalled_nodes(
            [_status("nodes:pool1")], state_dir=state_dir, now=now
        )

    def test_only_running_nodes(self, tmp_path: pl.Path):
        """Check only running node services, a stopped node is not stalled."""
        state_dir = self._make_state_dir(tmp_path)
        now = self.SYSTEM_START + 100_000
        for node_name in ("pool1", "pool2", "submit_api"):
            self._add_block(state_dir, node_name, mtime=0.0)

        stalled = cluster_nodes.get_stalled_nodes(
            [
                _status("nodes:pool1"),
                _status("nodes:pool2", status="STOPPED", uptime=None),
                _status("submit_api"),
            ],
            state_dir=state_dir,
            now=now,
        )

        assert stalled == ["pool1"]

    def test_restarted_node_grace(self, tmp_path: pl.Path):
        """Measure a node restarted after a long stop from the start of its process."""
        state_dir = self._make_state_dir(tmp_path)
        now = self.SYSTEM_START + 100_000
        self._add_block(state_dir, "pool1", mtime=now - 10_000)

        assert not cluster_nodes.get_stalled_nodes(
            [_status("nodes:pool1", uptime="0:03:00")], state_dir=state_dir, now=now
        )
        assert cluster_nodes.get_stalled_nodes(
            [_status("nodes:pool1", uptime="0:04:01")], state_dir=state_dir, now=now
        ) == ["pool1"]

    def test_newest_write_wins(self, tmp_path: pl.Path):
        """Use the newest write among all blocks files of the volatile DB."""
        state_dir = self._make_state_dir(tmp_path)
        now = self.SYSTEM_START + 100_000
        self._add_block(state_dir, "pool1", mtime=now - 1_000)
        new_file = state_dir / "db-pool1" / "volatile" / "blocks-1.dat"
        new_file.write_bytes(b"block")
        os.utime(new_file, (now - 5, now - 5))
        # Creating the file refreshed the dir mtime, only the file mtime should count
        os.utime(new_file.parent, (now - 1_000, now - 1_000))

        assert not cluster_nodes.get_stalled_nodes(
            [_status("nodes:pool1")], state_dir=state_dir, now=now
        )

    def test_file_removed_during_scan(self, tmp_path: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Skip a blocks file removed by the DB garbage collection and check the rest."""
        state_dir = self._make_state_dir(tmp_path)
        now = self.SYSTEM_START + 100_000
        stale = now - self.HORIZON_SEC - 1
        self._add_block(state_dir, "pool1", mtime=stale)
        removed_file = state_dir / "db-pool1" / "volatile" / "blocks-1.dat"
        removed_file.write_bytes(b"block")
        os.utime(removed_file.parent, (stale, stale))

        orig_stat = pl.Path.stat

        def _stat(self: pl.Path, **kwargs: tp.Any) -> os.stat_result:
            if self == removed_file:
                raise FileNotFoundError(self)
            return orig_stat(self, **kwargs)

        monkeypatch.setattr(pl.Path, "stat", _stat)

        # The node is reported only if the scan went on past the removed file
        assert cluster_nodes.get_stalled_nodes(
            [_status("nodes:pool1")], state_dir=state_dir, now=now
        ) == ["pool1"]

    @pytest.mark.parametrize("failing", ["dir", "file"])
    def test_unreadable_db(self, tmp_path: pl.Path, monkeypatch: pytest.MonkeyPatch, failing: str):
        """Don't report a node whose volatile DB can't be read."""
        state_dir = self._make_state_dir(tmp_path)
        blocks_file = self._add_block(state_dir, "pool1", mtime=0.0)
        failing_path = blocks_file.parent if failing == "dir" else blocks_file

        orig_stat = pl.Path.stat

        def _stat(self: pl.Path, **kwargs: tp.Any) -> os.stat_result:
            if self == failing_path:
                raise PermissionError(self)
            return orig_stat(self, **kwargs)

        monkeypatch.setattr(pl.Path, "stat", _stat)

        assert not cluster_nodes.get_stalled_nodes(
            [_status("nodes:pool1")], state_dir=state_dir, now=self.SYSTEM_START + 100_000
        )

    def test_db_not_found(self, tmp_path: pl.Path):
        """Don't report a node whose volatile DB is not in the expected location."""
        state_dir = self._make_state_dir(tmp_path)

        assert not cluster_nodes.get_stalled_nodes(
            [_status("nodes:pool1", uptime="100:00:00")],
            state_dir=state_dir,
            now=self.SYSTEM_START + 100_000,
        )

    def test_empty_db(self, tmp_path: pl.Path):
        """Measure a node with an empty volatile DB from the creation of the DB dir."""
        state_dir = self._make_state_dir(tmp_path)
        now = self.SYSTEM_START + 100_000
        volatile_dir = state_dir / "db-pool1" / "volatile"
        volatile_dir.mkdir(parents=True)
        statuses = [_status("nodes:pool1", uptime="100:00:00")]

        os.utime(volatile_dir, (now - self.HORIZON_SEC + 1,) * 2)
        assert not cluster_nodes.get_stalled_nodes(statuses, state_dir=state_dir, now=now)

        os.utime(volatile_dir, (now - self.HORIZON_SEC - 1,) * 2)
        assert cluster_nodes.get_stalled_nodes(statuses, state_dir=state_dir, now=now) == ["pool1"]

    def test_future_system_start(self, tmp_path: pl.Path):
        """Don't report a node of an instance whose chain didn't start yet."""
        state_dir = self._make_state_dir(tmp_path)
        self._add_block(state_dir, "pool1", mtime=0.0)

        assert not cluster_nodes.get_stalled_nodes(
            [_status("nodes:pool1", uptime="100:00:00")],
            state_dir=state_dir,
            now=self.SYSTEM_START - 60,
        )

    def test_missing_genesis(self, tmp_path: pl.Path):
        """Report no stalled nodes when there is no genesis file."""
        self._add_block(tmp_path, "pool1", mtime=0.0)

        assert not cluster_nodes.get_stalled_nodes(
            [_status("nodes:pool1")], state_dir=tmp_path, now=1e9
        )

    def test_invalid_genesis(self, tmp_path: pl.Path):
        """Report no stalled nodes when the genesis file is invalid."""
        state_dir = self._make_state_dir(tmp_path)
        (state_dir / "shelley" / "genesis.json").write_text("{}", encoding="utf-8")
        self._add_block(state_dir, "pool1", mtime=0.0)

        assert not cluster_nodes.get_stalled_nodes(
            [_status("nodes:pool1")], state_dir=state_dir, now=1e9
        )
