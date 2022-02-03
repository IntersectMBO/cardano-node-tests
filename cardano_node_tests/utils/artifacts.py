"""Functionality for collecting testing artifacts."""
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from _pytest.config import Config
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


def save_cli_coverage(cluster_obj: clusterlib.ClusterLib, pytest_config: Config) -> Optional[Path]:
    """Save CLI coverage info."""
    cli_coverage_dir = pytest_config.getoption("--cli-coverage-dir")
    if not (cli_coverage_dir and cluster_obj.cli_coverage):
        return None

    json_file = Path(cli_coverage_dir) / f"cli_coverage_{helpers.get_timestamped_rand_str()}.json"
    with open(json_file, "w", encoding="utf-8") as out_json:
        json.dump(cluster_obj.cli_coverage, out_json, indent=4)
    LOGGER.info(f"Coverage file saved to '{cli_coverage_dir}'.")
    return json_file


def save_start_script_coverage(log_file: Path, pytest_config: Config) -> Optional[Path]:
    """Save info about CLI commands executed by cluster start script."""
    cli_coverage_dir = pytest_config.getoption("--cli-coverage-dir")
    if not (cli_coverage_dir and log_file.exists()):
        return None

    dest_file = (
        Path(cli_coverage_dir) / f"cli_coverage_script_{helpers.get_timestamped_rand_str()}.log"
    )
    shutil.copy(log_file, dest_file)
    LOGGER.info(f"Start script coverage log file saved to '{dest_file}'.")
    return dest_file


def save_cluster_artifacts(save_dir: Path, state_dir: Path) -> None:
    """Save cluster artifacts (logs, certs, etc.)."""
    destdir = save_dir / "cluster_artifacts" / f"{state_dir.name}_{helpers.get_rand_str(8)}"
    destdir.mkdir(parents=True)

    files_list = [*state_dir.glob("*.std*"), *state_dir.glob("*.json"), *state_dir.glob("*.log")]
    dirs_to_copy = ("nodes", "shelley")

    for fpath in files_list:
        shutil.copy(fpath, destdir)
    for dname in dirs_to_copy:
        src_dir = state_dir / dname
        if not src_dir.exists():
            continue
        shutil.copytree(src_dir, destdir / dname, symlinks=True, ignore_dangling_symlinks=True)

    if not os.listdir(destdir):
        destdir.rmdir()
        return

    LOGGER.info(f"Cluster artifacts saved to '{destdir}'.")
    shutil.rmtree(state_dir, ignore_errors=True)


def copy_artifacts(pytest_tmp_dir: Path, pytest_config: Config) -> None:
    """Copy collected tests and cluster artifacts to artifacts dir."""
    artifacts_base_dir = pytest_config.getoption("--artifacts-base-dir")
    if not artifacts_base_dir:
        return

    artifacts_dir = Path(artifacts_base_dir)

    pytest_tmp_dir = pytest_tmp_dir.resolve()
    if not pytest_tmp_dir.is_dir():
        return

    destdir = artifacts_dir / f"{pytest_tmp_dir.name}-{helpers.get_rand_str(8)}"
    if destdir.resolve().is_dir():
        shutil.rmtree(destdir)

    shutil.copytree(pytest_tmp_dir, destdir, symlinks=True, ignore_dangling_symlinks=True)
    LOGGER.info(f"Collected artifacts copied to '{artifacts_dir}'.")
