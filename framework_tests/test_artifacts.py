"""Unit tests for `cardano_node_tests.utils.artifacts`."""

import json
import pathlib as pl
import shutil
import typing as tp

import pytest
from _pytest.config import Config

from cardano_node_tests.utils import artifacts
from framework_tests import stubs


@pytest.fixture
def state_dir(tmp_path: pl.Path) -> pl.Path:
    """Create a state dir populated with typical cluster artifacts."""
    sdir = tmp_path / "state-cluster0"
    sdir.mkdir()
    (sdir / "bft1.stdout").write_text("stdout")
    (sdir / "bft1.stderr").write_text("stderr")
    (sdir / "config.json").write_text("{}")
    nodes_dir = sdir / "nodes"
    nodes_dir.mkdir()
    (nodes_dir / "node.skey").write_text("key")
    shelley_dir = sdir / "shelley"
    shelley_dir.mkdir()
    (shelley_dir / "genesis.json").write_text("{}")
    return sdir


@pytest.fixture
def save_dir(tmp_path: pl.Path) -> pl.Path:
    """Create a directory for saving the artifacts."""
    sdir = tmp_path / "save"
    sdir.mkdir()
    return sdir


def _get_saved_dirs(save_dir: pl.Path) -> list[pl.Path]:
    """Return entries created under the `cluster_artifacts` dir."""
    return sorted((save_dir / "cluster_artifacts").glob("*"))


class TestSaveCliCoverage:
    """Tests for `save_cli_coverage`."""

    def test_saves_coverage(self, tmp_path: pl.Path):
        """Save the collected coverage data to the coverage dir."""
        coverage_dir = tmp_path / "coverage"
        coverage_dir.mkdir()
        pytest_config = tp.cast(Config, stubs.PytestConfigStub(str(coverage_dir)))
        cluster_obj = stubs.ClusterObjStub(cli_coverage={"cardano-cli": {"_count": 1}})

        json_file = artifacts.save_cli_coverage(
            cluster_obj=tp.cast(tp.Any, cluster_obj), pytest_config=pytest_config
        )

        assert json_file is not None
        assert json_file.parent == coverage_dir
        assert json.loads(json_file.read_text()) == {"cardano-cli": {"_count": 1}}

    def test_second_save_is_noop(self, tmp_path: pl.Path):
        """Don't save the same coverage data twice."""
        coverage_dir = tmp_path / "coverage"
        coverage_dir.mkdir()
        pytest_config = tp.cast(Config, stubs.PytestConfigStub(str(coverage_dir)))
        cluster_obj = stubs.ClusterObjStub(cli_coverage={"cardano-cli": {"_count": 1}})

        assert (
            artifacts.save_cli_coverage(
                cluster_obj=tp.cast(tp.Any, cluster_obj), pytest_config=pytest_config
            )
            is not None
        )
        assert not cluster_obj.cli_coverage
        assert (
            artifacts.save_cli_coverage(
                cluster_obj=tp.cast(tp.Any, cluster_obj), pytest_config=pytest_config
            )
            is None
        )
        assert len(list(coverage_dir.glob("*.json"))) == 1

    def test_disabled_coverage(self):
        """Return `None` when CLI coverage collection is not enabled."""
        pytest_config = tp.cast(Config, stubs.PytestConfigStub(""))
        cluster_obj = stubs.ClusterObjStub(cli_coverage={"cardano-cli": {"_count": 1}})

        assert (
            artifacts.save_cli_coverage(
                cluster_obj=tp.cast(tp.Any, cluster_obj), pytest_config=pytest_config
            )
            is None
        )

    def test_no_coverage_data(self, tmp_path: pl.Path):
        """Return `None` when there's no coverage data to save."""
        coverage_dir = tmp_path / "coverage"
        coverage_dir.mkdir()
        pytest_config = tp.cast(Config, stubs.PytestConfigStub(str(coverage_dir)))
        cluster_obj = stubs.ClusterObjStub(cli_coverage={})

        assert (
            artifacts.save_cli_coverage(
                cluster_obj=tp.cast(tp.Any, cluster_obj), pytest_config=pytest_config
            )
            is None
        )
        assert not list(coverage_dir.glob("*.json"))

    def test_save_failure_returns_none(self, tmp_path: pl.Path, caplog: pytest.LogCaptureFixture):
        """Don't raise when saving the coverage data fails.

        The coverage info is saved in `finally` blocks and in fixture teardowns, so an
        exception would mask the original error.
        """
        coverage_dir = tmp_path / "coverage"
        coverage_dir.mkdir()
        pytest_config = tp.cast(Config, stubs.PytestConfigStub(str(coverage_dir)))
        # A value that is not JSON serializable makes `json.dump` raise
        cluster_obj = stubs.ClusterObjStub(cli_coverage={"cardano-cli": object()})

        json_file = artifacts.save_cli_coverage(
            cluster_obj=tp.cast(tp.Any, cluster_obj), pytest_config=pytest_config
        )

        assert json_file is None
        assert "Failed to save coverage file" in caplog.text
        # The incomplete file was removed
        assert not list(coverage_dir.glob("*.json"))
        # The data was not cleared, so it can be saved by a later attempt
        assert cluster_obj.cli_coverage


class TestSaveStartScriptCoverage:
    """Tests for `save_start_script_coverage`."""

    def test_copies_log_file(self, tmp_path: pl.Path):
        """Copy the start script log file to the coverage dir."""
        log_file = tmp_path / "start_cluster.log"
        log_file.write_text("cli commands")
        coverage_dir = tmp_path / "coverage"
        coverage_dir.mkdir()
        pytest_config = tp.cast(Config, stubs.PytestConfigStub(str(coverage_dir)))

        dest_file = artifacts.save_start_script_coverage(
            log_file=log_file, pytest_config=pytest_config
        )

        assert dest_file is not None
        assert dest_file.parent == coverage_dir
        assert dest_file.read_text() == "cli commands"

    def test_disabled_coverage(self, tmp_path: pl.Path):
        """Return `None` when CLI coverage collection is not enabled."""
        log_file = tmp_path / "start_cluster.log"
        log_file.write_text("cli commands")
        pytest_config = tp.cast(Config, stubs.PytestConfigStub(""))

        assert (
            artifacts.save_start_script_coverage(log_file=log_file, pytest_config=pytest_config)
            is None
        )

    @pytest.mark.parametrize("err_type", (OSError, RuntimeError))
    def test_copy_failure_returns_none(
        self,
        err_type: type[Exception],
        tmp_path: pl.Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """Log a warning and return `None` when the copy fails."""
        log_file = tmp_path / "start_cluster.log"
        log_file.write_text("cli commands")
        coverage_dir = tmp_path / "coverage"
        coverage_dir.mkdir()
        pytest_config = tp.cast(Config, stubs.PytestConfigStub(str(coverage_dir)))

        def _failing_copy(*_args: object, **_kwargs: object) -> str:
            err = "Simulated copy failure"
            raise err_type(err)

        monkeypatch.setattr(artifacts.shutil, "copy", _failing_copy)

        dest_file = artifacts.save_start_script_coverage(
            log_file=log_file, pytest_config=pytest_config
        )

        assert dest_file is None
        assert "Failed to copy" in caplog.text


class TestSaveClusterArtifacts:
    """Tests for `save_cluster_artifacts`."""

    def test_save_files_and_dirs(self, save_dir: pl.Path, state_dir: pl.Path):
        """Copy matching files and known subdirectories to the destination dir."""
        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=state_dir)

        saved_dirs = _get_saved_dirs(save_dir)
        assert len(saved_dirs) == 1
        destdir = saved_dirs[0]
        assert (destdir / "bft1.stdout").read_text() == "stdout"
        assert (destdir / "bft1.stderr").read_text() == "stderr"
        assert (destdir / "config.json").read_text() == "{}"
        assert (destdir / "nodes" / "node.skey").read_text() == "key"
        assert (destdir / "shelley" / "genesis.json").read_text() == "{}"

    def test_instance_id_in_dir_name(self, save_dir: pl.Path, state_dir: pl.Path):
        """Use the cluster instance id from the state dir in the destination dir name."""
        instance_id_file = state_dir / artifacts.CLUSTER_INSTANCE_ID_FILENAME
        instance_id_file.write_text("abcdefgh")

        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=state_dir)

        saved_dirs = _get_saved_dirs(save_dir)
        assert len(saved_dirs) == 1
        assert saved_dirs[0].name == f"{state_dir.name}_abcdefgh"

    def test_dangling_symlink_skipped(
        self, save_dir: pl.Path, state_dir: pl.Path, caplog: pytest.LogCaptureFixture
    ):
        """Skip a dangling symlink and still save the remaining artifacts."""
        (state_dir / "broken.log").symlink_to(state_dir / "missing.log")

        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=state_dir)

        saved_dirs = _get_saved_dirs(save_dir)
        assert len(saved_dirs) == 1
        destdir = saved_dirs[0]
        assert not (destdir / "broken.log").exists()
        assert (destdir / "bft1.stdout").exists()
        assert "broken.log" in caplog.text

    def test_dir_matching_glob_skipped(
        self, save_dir: pl.Path, state_dir: pl.Path, caplog: pytest.LogCaptureFixture
    ):
        """Skip a directory whose name matches the file glob patterns."""
        (state_dir / "subdir.log").mkdir()

        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=state_dir)

        saved_dirs = _get_saved_dirs(save_dir)
        assert len(saved_dirs) == 1
        assert not (saved_dirs[0] / "subdir.log").exists()
        assert "subdir.log" in caplog.text

    def test_copy_failure_tolerated(
        self,
        save_dir: pl.Path,
        state_dir: pl.Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """Keep saving the remaining artifacts when copying one file fails."""
        real_copy = shutil.copy

        def _failing_copy(src: str, dst: str) -> str:
            if pl.Path(src).name == "bft1.stderr":
                err = "Simulated copy failure"
                raise OSError(err)
            return str(real_copy(src, dst))

        monkeypatch.setattr(artifacts.shutil, "copy", _failing_copy)

        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=state_dir)

        saved_dirs = _get_saved_dirs(save_dir)
        assert len(saved_dirs) == 1
        destdir = saved_dirs[0]
        assert (destdir / "bft1.stdout").exists()
        assert not (destdir / "bft1.stderr").exists()
        assert "Failed to copy" in caplog.text

    def test_dir_copy_failure_tolerated(
        self,
        save_dir: pl.Path,
        state_dir: pl.Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """Keep the file artifacts when copying a subdirectory fails."""

        def _failing_copytree(*_args: object, **_kwargs: object) -> str:
            err = "Simulated copytree failure"
            raise OSError(err)

        monkeypatch.setattr(artifacts.shutil, "copytree", _failing_copytree)

        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=state_dir)

        saved_dirs = _get_saved_dirs(save_dir)
        assert len(saved_dirs) == 1
        destdir = saved_dirs[0]
        assert (destdir / "bft1.stdout").exists()
        assert not (destdir / "nodes").exists()
        assert "Failed to copy" in caplog.text

    def test_all_copies_failed(
        self,
        save_dir: pl.Path,
        state_dir: pl.Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """Log an error and remove the empty destination dir when every copy fails."""

        def _failing_copy(*_args: object, **_kwargs: object) -> str:
            err = "Simulated copy failure"
            raise OSError(err)

        monkeypatch.setattr(artifacts.shutil, "copy", _failing_copy)
        monkeypatch.setattr(artifacts.shutil, "copytree", _failing_copy)

        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=state_dir)

        assert not _get_saved_dirs(save_dir)
        assert "Failed to save any cluster artifacts" in caplog.text

    def test_setup_failure_tolerated(
        self, save_dir: pl.Path, state_dir: pl.Path, caplog: pytest.LogCaptureFixture
    ):
        """Log the failure instead of raising when the setup I/O fails."""
        # A directory in place of the instance id file makes `open()` raise
        # `IsADirectoryError` before any file copy starts.
        (state_dir / artifacts.CLUSTER_INSTANCE_ID_FILENAME).mkdir()

        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=state_dir)

        assert not _get_saved_dirs(save_dir)
        assert "Failed to save cluster artifacts" in caplog.text

    def test_non_oserror_tolerated(
        self, save_dir: pl.Path, state_dir: pl.Path, caplog: pytest.LogCaptureFixture
    ):
        """Log the failure instead of raising when the setup fails with a non-`OSError`."""
        # Invalid UTF-8 content of the instance id file makes `read()` raise
        # `UnicodeDecodeError` before any file copy starts.
        (state_dir / artifacts.CLUSTER_INSTANCE_ID_FILENAME).write_bytes(b"\xff")

        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=state_dir)

        assert not _get_saved_dirs(save_dir)
        assert "Failed to save cluster artifacts" in caplog.text

    def test_empty_state_dir(
        self, save_dir: pl.Path, tmp_path: pl.Path, caplog: pytest.LogCaptureFixture
    ):
        """Remove the empty destination dir when there was nothing to save."""
        empty_state_dir = tmp_path / "state-cluster-empty"
        empty_state_dir.mkdir()

        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=empty_state_dir)

        assert not _get_saved_dirs(save_dir)
        assert "No cluster artifacts found" in caplog.text

    def test_existing_destdir_gets_suffix(
        self, save_dir: pl.Path, state_dir: pl.Path, caplog: pytest.LogCaptureFixture
    ):
        """Save to a new dir with a random suffix when the destination dir already exists."""
        instance_id_file = state_dir / artifacts.CLUSTER_INSTANCE_ID_FILENAME
        instance_id_file.write_text("abcdefgh")

        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=state_dir)
        artifacts.save_cluster_artifacts(save_dir=save_dir, state_dir=state_dir)

        saved_dirs = _get_saved_dirs(save_dir)
        assert len(saved_dirs) == 2
        base_name = f"{state_dir.name}_abcdefgh"
        assert saved_dirs[0].name == base_name
        assert saved_dirs[1].name.startswith(f"{base_name}_")
        assert "already exists" in caplog.text
