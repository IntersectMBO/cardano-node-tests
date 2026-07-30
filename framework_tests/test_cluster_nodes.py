"""Unit tests for `cardano_node_tests.utils.cluster_nodes`.

The tests must not depend on external binaries or a running cluster -
`run_command` and the cluster environment lookups are monkeypatched.
"""

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
