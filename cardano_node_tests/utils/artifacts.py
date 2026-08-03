"""Functionality for collecting testing artifacts."""

import json
import logging
import pathlib as pl
import shutil

from _pytest.config import Config
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

CLI_COVERAGE_ARG = "--cli-coverage-dir"
ARTIFACTS_BASE_DIR_ARG = "--artifacts-base-dir"
CLUSTER_INSTANCE_ID_FILENAME = "cluster_instance_id.log"


def save_cli_coverage(
    *, cluster_obj: clusterlib.ClusterLib, pytest_config: Config
) -> pl.Path | None:
    """Save CLI coverage info."""
    cli_coverage_dir = pytest_config.getoption(CLI_COVERAGE_ARG)
    if not (cli_coverage_dir and hasattr(cluster_obj, "cli_coverage") and cluster_obj.cli_coverage):  # pyright: ignore [reportAttributeAccessIssue]
        return None

    json_file = (
        pl.Path(cli_coverage_dir) / f"cli_coverage_{helpers.get_timestamped_rand_str()}.json"
    )
    with open(json_file, "w", encoding="utf-8") as out_json:
        json.dump(cluster_obj.cli_coverage, out_json, indent=4)  # pyright: ignore [reportAttributeAccessIssue]
    LOGGER.info(f"Coverage file saved to '{json_file}'.")
    return json_file


def save_start_script_coverage(*, log_file: pl.Path, pytest_config: Config) -> pl.Path | None:
    """Save info about CLI commands executed by cluster start script."""
    cli_coverage_dir = pytest_config.getoption(CLI_COVERAGE_ARG)
    if not (cli_coverage_dir and log_file.exists()):
        return None

    dest_file = (
        pl.Path(cli_coverage_dir) / f"cli_coverage_script_{helpers.get_timestamped_rand_str()}.log"
    )
    try:
        shutil.copy(log_file, dest_file)
    except OSError as err:
        # The log file may disappear between the check above and the copy.
        LOGGER.warning(f"Failed to copy '{log_file}': {err}")
        return None
    LOGGER.info(f"Start script coverage log file saved to '{dest_file}'.")
    return dest_file


def _copy_state_dir_content(*, state_dir: pl.Path, destdir: pl.Path) -> int:
    """Copy artifact files and dirs from the state dir and return the number of failures."""
    files_list = [
        *state_dir.glob("*.stdout"),
        *state_dir.glob("*.stderr"),
        *state_dir.glob("*.stdout.[0-9]*"),
        *state_dir.glob("*.stderr.[0-9]*"),
        *state_dir.glob("*.json"),
        *state_dir.glob("*.log"),
    ]
    dirs_to_copy = ("nodes", "shelley")

    copy_failures = 0

    for fpath in files_list:
        # Skip dangling symlinks, directories and special files that `shutil.copy`
        # would fail or hang on.
        if not fpath.is_file():
            LOGGER.warning(f"Skipping non-regular file '{fpath}'.")
            continue
        try:
            shutil.copy(fpath, destdir)
        except OSError as err:
            # The cluster may still be running and rotate or delete the file between
            # the check above and the copy. Don't let one file abort the whole save.
            LOGGER.warning(f"Failed to copy '{fpath}': {err}")
            copy_failures += 1
    for dname in dirs_to_copy:
        src_dir = state_dir / dname
        if not src_dir.exists():
            continue
        try:
            shutil.copytree(src_dir, destdir / dname, symlinks=True)
        except OSError as err:
            # Same race as with the files above - the cluster may still be running and
            # modifying the directory content.
            LOGGER.warning(f"Failed to copy '{src_dir}': {err}")
            copy_failures += 1

    return copy_failures


def save_cluster_artifacts(*, save_dir: pl.Path, state_dir: pl.Path) -> None:
    """Save cluster artifacts (logs, certs, etc.)."""
    dir_rand_str = ""
    cluster_instance_id_log = state_dir / CLUSTER_INSTANCE_ID_FILENAME
    if cluster_instance_id_log.exists():
        with open(cluster_instance_id_log, encoding="utf-8") as fp_in:
            dir_rand_str = fp_in.read().strip()
    dir_rand_str = dir_rand_str or helpers.get_rand_str(8)

    destdir = save_dir / "cluster_artifacts" / f"{state_dir.name}_{dir_rand_str}"
    if destdir.exists():
        # Artifacts for this cluster instance were already saved. Append a random
        # suffix so the new save doesn't clash with the existing directory.
        destdir = destdir.with_name(f"{destdir.name}_{helpers.get_rand_str(8)}")
        LOGGER.warning(f"Cluster artifacts dir already exists, saving to '{destdir}' instead.")
    destdir.mkdir(parents=True)

    copy_failures = _copy_state_dir_content(state_dir=state_dir, destdir=destdir)

    if not any(destdir.iterdir()):
        if copy_failures:
            LOGGER.error(f"Failed to save any cluster artifacts from '{state_dir}'.")
        else:
            LOGGER.warning(f"No cluster artifacts found in '{state_dir}', nothing saved.")
        destdir.rmdir()
        return

    LOGGER.info(f"Cluster artifacts saved to '{destdir}'.")


def copy_artifacts(*, pytest_tmp_dir: pl.Path, pytest_config: Config) -> None:
    """Copy collected tests and cluster artifacts to artifacts dir."""
    artifacts_base_dir = pytest_config.getoption(ARTIFACTS_BASE_DIR_ARG)
    if not artifacts_base_dir:
        return

    artifacts_dir = pl.Path(artifacts_base_dir)

    pytest_tmp_dir = pytest_tmp_dir.resolve()
    if not pytest_tmp_dir.is_dir():
        return

    destdir = artifacts_dir / f"{pytest_tmp_dir.name}-{helpers.get_rand_str(8)}"
    shutil.copytree(pytest_tmp_dir, destdir, symlinks=True)
    LOGGER.info(f"Collected artifacts copied to '{destdir}'.")
