import logging
import os
from pathlib import Path
from typing import Any
from typing import Generator

import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory
from filelock import FileLock

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)
LOCK_FILE = "pytest_cluster.lock"
RUNNING_FILE = ".cluster_running"
MARK_FIRST_STARTED = ".mark_first_started"


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--cli-coverage-dir",
        action="store",
        type=helpers.check_dir_arg,
        default="",
        help="Path to directory for storing coverage info",
    )


def pytest_html_report_title(report: Any) -> None:
    cardano_version = helpers.get_cardano_version()
    report.title = (
        f"cardano-node {cardano_version['cardano-node']} (git rev {cardano_version['git_rev']})"
    )


def pytest_configure(config: Any) -> None:
    cardano_version = helpers.get_cardano_version()
    config._metadata["cardano-node"] = cardano_version["cardano-node"]
    config._metadata["cardano-node rev"] = cardano_version["git_rev"]
    config._metadata["ghc"] = cardano_version["ghc"]


def _fresh_cluster(
    change_dir: Path, tmp_path_factory: TempdirFactory, worker_id: str, request: FixtureRequest
) -> Generator[clusterlib.ClusterLib, None, None]:
    """Run fresh cluster for each test marked with the "first" marker."""
    # pylint: disable=unused-argument
    # not executing with multiple workers
    if not worker_id or worker_id == "master":
        helpers.stop_cluster()
        cluster_obj = helpers.start_cluster()
        tmp_path = Path(tmp_path_factory.mktemp("addrs_data"))
        helpers.setup_test_addrs(cluster_obj, tmp_path)

        try:
            yield cluster_obj
        finally:
            helpers.save_cli_coverage(cluster_obj, request)
            helpers.stop_cluster()
        return

    # executing in parallel with multiple workers
    lock_dir = Path(tmp_path_factory.getbasetemp()).parent
    # as the tests marked with "first" need fresh cluster, no other tests on
    # any other worker can run until the ones that requested this fixture are
    # finished
    with FileLock(f"{lock_dir}/.fresh_{LOCK_FILE}"):
        # indicate that tests marked as "first" were launched on this worker
        open(lock_dir / MARK_FIRST_STARTED, "a").close()
        # indicate that a tests marked as "first" is running on this worker
        open(lock_dir / f"{RUNNING_FILE}_fresh_{worker_id}", "a").close()

        # start and setup the cluster
        helpers.stop_cluster()
        cluster_obj = helpers.start_cluster()
        tmp_path = Path(tmp_path_factory.mktemp("addrs_data"))
        helpers.setup_test_addrs(cluster_obj, tmp_path)
        try:
            yield cluster_obj
        finally:
            # indicate the tests are no longer running on this worker
            os.remove(lock_dir / f"{RUNNING_FILE}_fresh_{worker_id}")
            # save CLI coverage
            helpers.save_cli_coverage(cluster_obj, request)
            # stop the cluster
            helpers.stop_cluster()


def _wait_for_fresh(lock_dir: Path) -> None:
    """Wait until all tests marked as "first" are finished."""
    while True:
        # if the status files exists, the tests are still in progress
        if list(lock_dir.glob(f"{RUNNING_FILE}_fresh_*")):
            helpers.wait_for(
                lambda: not list(lock_dir.glob(f"{RUNNING_FILE}_fresh_*")), delay=5, num_sec=20_000,
            )
        # other session-scoped cluster tests are already running, i.e. the "first"
        # tests are already finished
        elif list(lock_dir.glob(f"{RUNNING_FILE}_session_*")):
            break
        # wait for a while to see if new status files are created
        else:
            still_running = helpers.wait_for(
                lambda: list(lock_dir.glob(f"{RUNNING_FILE}_fresh_*")),
                delay=2,
                num_sec=10,
                silent=True,
            )
            if not still_running:
                break


# session scoped fixtures


@pytest.fixture(scope="session")
def change_dir(tmp_path_factory: TempdirFactory) -> None:
    """Change CWD to temp directory before running tests."""
    tmp_path = tmp_path_factory.getbasetemp()
    os.chdir(tmp_path)
    LOGGER.info(f"Changed CWD to '{tmp_path}'.")


@pytest.yield_fixture(scope="session")
def cluster_session(
    change_dir: Path, tmp_path_factory: TempdirFactory, worker_id: str, request: FixtureRequest
) -> Generator[clusterlib.ClusterLib, None, None]:
    # pylint: disable=unused-argument
    # not executing with multiple workers
    if not worker_id or worker_id == "master":
        helpers.stop_cluster()
        cluster_obj = helpers.start_cluster()
        tmp_path = Path(tmp_path_factory.mktemp("addrs_data"))
        helpers.setup_test_addrs(cluster_obj, tmp_path)

        try:
            yield cluster_obj
        finally:
            helpers.save_cli_coverage(cluster_obj, request)
            helpers.stop_cluster()
        return

    # executing in parallel with multiple workers
    lock_dir = Path(tmp_path_factory.getbasetemp()).parent
    mark_first_started = lock_dir / MARK_FIRST_STARTED
    # make sure this code is executed on single worker at a time
    with FileLock(f"{lock_dir}/.session_{LOCK_FILE}"):
        # make sure tests marked as "first" had enought time to start
        if not mark_first_started.exists():
            helpers.wait_for(mark_first_started.exists, delay=2, num_sec=10, silent=True)

        # wait until all tests marked as "first" are finished
        _wait_for_fresh(lock_dir)

        if list(lock_dir.glob(f"{RUNNING_FILE}_session_*")):
            # indicate that tests are running on this worker
            open(lock_dir / f"{RUNNING_FILE}_session_{worker_id}", "a").close()
            # reuse existing cluster
            cluster_env = helpers.get_cluster_env()
            cluster_obj = clusterlib.ClusterLib(cluster_env["state_dir"])
        else:
            # indicate that tests are running on this worker
            open(lock_dir / f"{RUNNING_FILE}_session_{worker_id}", "a").close()
            # start and setup the cluster
            helpers.stop_cluster()
            cluster_obj = helpers.start_cluster()
            tmp_path = Path(tmp_path_factory.mktemp("addrs_data"))
            helpers.setup_test_addrs(cluster_obj, tmp_path)

    try:
        yield cluster_obj
    finally:
        with FileLock(f"{lock_dir}/.session_stop_{LOCK_FILE}"):
            # indicate that tests are no longer running on this worker
            os.remove(lock_dir / f"{RUNNING_FILE}_session_{worker_id}")
            # save CLI coverage
            helpers.save_cli_coverage(cluster_obj, request)
            # stop cluster if this is a last worker that was running tests
            if not list(lock_dir.glob(f"{RUNNING_FILE}_session_*")):
                helpers.stop_cluster()


@pytest.fixture(scope="session")
def addrs_data_session(cluster_session: clusterlib.ClusterLib) -> dict:
    # pylint: disable=unused-argument
    return helpers.load_addrs_data()


# class scoped fixtures


cluster_class = pytest.yield_fixture(_fresh_cluster, scope="class")


@pytest.fixture(scope="class")
def addrs_data_class(cluster_class: clusterlib.ClusterLib) -> dict:
    # pylint: disable=unused-argument
    return helpers.load_addrs_data()


# function scoped fixtures


cluster_func = pytest.yield_fixture(_fresh_cluster)


@pytest.fixture
def addrs_data_func(cluster_func: clusterlib.ClusterLib) -> dict:
    # pylint: disable=unused-argument
    return helpers.load_addrs_data()
