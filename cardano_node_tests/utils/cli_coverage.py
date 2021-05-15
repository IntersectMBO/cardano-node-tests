"""Functionality for CLI coverage data collected by the `clusterlib`."""
import json
import logging
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
    with open(json_file, "w") as out_json:
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
