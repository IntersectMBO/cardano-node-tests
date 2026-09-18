"""Unit tests for `cardano_node_tests.tests.common`."""

import json
import pathlib as pl
import typing as tp

import pytest
from _pytest.config import Config

from cardano_node_tests.tests import common
from framework_tests import stubs


class _ClusterTypeStub:
    """Minimal stub of `ClusterType` that provides only `get_cluster_obj`."""

    def get_cluster_obj(self, *, command_era: str = "") -> tp.Any:
        """Return a stub of a `ClusterLib` instance."""
        return stubs.ClusterObjStub(
            cli_coverage={"cardano-cli": {"_count": 1}}, command_era=command_era
        )


class TestGetFixtureClusterObj:
    """Tests for `get_fixture_cluster_obj`."""

    def test_coverage_saved_on_teardown(self, tmp_path: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Save CLI coverage of the created instance when the registered finalizer runs."""
        coverage_dir = tmp_path / "coverage"
        coverage_dir.mkdir()
        pytest_config = tp.cast(Config, stubs.PytestConfigStub(str(coverage_dir)))
        request = stubs.FixtureRequestStub(config=pytest_config)
        monkeypatch.setattr(common.cluster_nodes, "get_cluster_type", _ClusterTypeStub)

        cluster_obj = common.get_fixture_cluster_obj(
            request=tp.cast(tp.Any, request), command_era="conway"
        )

        assert cluster_obj.command_era == "conway"
        assert not list(coverage_dir.glob("*.json"))

        for finalizer in request.finalizers:
            finalizer()

        coverage_files = list(coverage_dir.glob("cli_coverage_*.json"))
        assert len(coverage_files) == 1
        assert json.loads(coverage_files[0].read_text()) == {"cardano-cli": {"_count": 1}}
