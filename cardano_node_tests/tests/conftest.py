import logging
import os

import pytest

from cardano_node_tests.utils.helpers import run_shell_command
from cardano_node_tests.utils.helpers import setup_cluster
from cardano_node_tests.utils.helpers import setup_test_addrs

LOGGER = logging.getLogger(__name__)


def pytest_configure(config):
    config.addinivalue_line("markers", "clean_cluster: mark that the test needs clean cluster.")


@pytest.fixture(scope="session")
def change_dir(tmp_path_factory):
    """Change CWD to temp directory before running tests."""
    tmp_path = tmp_path_factory.mktemp("artifacts")
    os.chdir(tmp_path)
    LOGGER.info(f"Changed CWD to '{tmp_path}'.")


@pytest.fixture(scope="session")
def cluster_session(change_dir):
    try:
        cluster_obj = setup_cluster()
        yield cluster_obj
    finally:
        LOGGER.info("Stopping cluster.")
        run_shell_command("stop-cluster", workdir=cluster_obj._cluster_data["work_dir"])


@pytest.fixture
def cluster(change_dir):
    try:
        cluster_obj = setup_cluster()
        yield cluster_obj
    finally:
        LOGGER.info("Stopping cluster.")
        run_shell_command("stop-cluster", workdir=cluster_obj._cluster_data["work_dir"])


@pytest.fixture(scope="session")
def addrs_data_session(cluster_session, tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("addrs_data")
    return setup_test_addrs(cluster_session, tmp_path)


@pytest.fixture
def addrs_data(cluster, tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("addrs_data")
    return setup_test_addrs(cluster, tmp_path)
