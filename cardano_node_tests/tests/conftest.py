import logging
import os

import pytest

from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


def pytest_addoption(parser):
    parser.addoption(
        "--cli-coverage-dir",
        action="store",
        type=helpers.check_dir_arg,
        default="",
        help="Path to directory for storing coverage info",
    )


def pytest_html_report_title(report):
    cardano_version = helpers.get_cardano_version()
    report.title = (
        f"cardano-node {cardano_version['cardano-node']} (git rev {cardano_version['git_rev']})"
    )


def pytest_configure(config):
    cardano_version = helpers.get_cardano_version()
    config._metadata["cardano-node"] = cardano_version["cardano-node"]
    config._metadata["cardano-node rev"] = cardano_version["git_rev"]
    config._metadata["ghc"] = cardano_version["ghc"]


# session scoped fixtures


@pytest.fixture(scope="session")
def change_dir(tmp_path_factory):
    """Change CWD to temp directory before running tests."""
    tmp_path = tmp_path_factory.getbasetemp()
    os.chdir(tmp_path)
    LOGGER.info(f"Changed CWD to '{tmp_path}'.")


@pytest.fixture(scope="session")
def cluster_session(change_dir, request):
    # pylint: disable=unused-argument
    return helpers.start_stop_cluster(request)


@pytest.fixture(scope="session")
def addrs_data_session(cluster_session, tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("addrs_data")
    return helpers.setup_test_addrs(cluster_session, tmp_path)


# class scoped fixtures


@pytest.fixture(scope="class")
def cluster_class(change_dir, request):
    # pylint: disable=unused-argument
    return helpers.start_stop_cluster(request)


@pytest.fixture(scope="class")
def addrs_data_class(cluster_class, tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("addrs_data")
    return helpers.setup_test_addrs(cluster_class, tmp_path)


# function scoped fixtures


@pytest.fixture
def cluster(change_dir, request):
    # pylint: disable=unused-argument
    return helpers.start_stop_cluster(request)


@pytest.fixture
def addrs_data(cluster, tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("addrs_data")
    return helpers.setup_test_addrs(cluster, tmp_path)
