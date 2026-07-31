"""Unit tests for `cardano_node_tests.utils.artifacts`."""

import pathlib as pl
import shutil

import pytest

from cardano_node_tests.utils import artifacts


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
