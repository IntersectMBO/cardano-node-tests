"""Functionality for CLI coverage data collected by the `clusterlib`."""
import json
import logging
from pathlib import Path
from typing import Optional

from _pytest.config import Config

from cardano_node_tests.utils import clusterlib
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
    LOGGER.info(f"Coverage files saved to '{cli_coverage_dir}'.")
    return json_file
