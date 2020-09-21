import logging
import os
from pathlib import Path
from typing import Any
from typing import Generator

import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import parallel_run

LOGGER = logging.getLogger(__name__)


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--cli-coverage-dir",
        action="store",
        type=helpers.check_dir_arg,
        default="",
        help="Path to directory for storing coverage info",
    )
    parser.addoption(
        "--artifacts-base-dir",
        action="store",
        type=helpers.check_dir_arg,
        default="",
        help="Path to directory for storing artifacts",
    )


def pytest_configure(config: Any) -> None:
    cardano_version = helpers.get_cardano_version()
    config._metadata["cardano-node"] = cardano_version["cardano-node"]
    config._metadata["cardano-node rev"] = cardano_version["git_rev"]
    config._metadata["ghc"] = cardano_version["ghc"]


@pytest.fixture(scope="session")
def change_dir(tmp_path_factory: TempdirFactory) -> None:
    """Change CWD to temp directory before running tests."""
    tmp_path = tmp_path_factory.getbasetemp()
    os.chdir(tmp_path)
    LOGGER.info(f"Changed CWD to '{tmp_path}'.")


def _run_cluster_cleanup(
    tmp_path_factory: TempdirFactory, worker_id: str, request: FixtureRequest, pytest_tmp_dir: Path
) -> None:
    cluster_manager_obj = parallel_run.ClusterManager(
        tmp_path_factory=tmp_path_factory, worker_id=worker_id, request=request
    )
    cluster_manager_obj._log("running cluster cleanup")
    # stop cluster
    cluster_manager_obj.stop()
    # process artifacts
    helpers.process_artifacts(pytest_tmp_dir=pytest_tmp_dir, request=request)


@pytest.yield_fixture(scope="session")
def cluster_cleanup(
    tmp_path_factory: TempdirFactory, worker_id: str, request: FixtureRequest
) -> Generator:
    pytest_tmp_dir = Path(tmp_path_factory.getbasetemp())

    if not worker_id or worker_id == "master":
        yield
        _run_cluster_cleanup(
            tmp_path_factory=tmp_path_factory,
            worker_id=worker_id,
            request=request,
            pytest_tmp_dir=pytest_tmp_dir,
        )
        return

    lock_dir = pytest_tmp_dir = pytest_tmp_dir.parent

    with helpers.FileLockIfXdist(f"{lock_dir}/.cluster_cleanup.lock"):
        open(lock_dir / f".started_session_{worker_id}", "a").close()

    yield

    with helpers.FileLockIfXdist(f"{lock_dir}/.cluster_cleanup.lock"):
        os.remove(lock_dir / f".started_session_{worker_id}")
        if lock_dir.glob(".started_session_*"):
            return

    with helpers.FileLockIfXdist(f"{lock_dir}/{parallel_run.CLUSTER_LOCK}"):
        _run_cluster_cleanup(
            tmp_path_factory=tmp_path_factory,
            worker_id=worker_id,
            request=request,
            pytest_tmp_dir=pytest_tmp_dir,
        )


@pytest.fixture(scope="session", autouse=True)
def session_autouse(change_dir: Any, cluster_cleanup: Any) -> None:
    """Autouse required session fixtures."""
    # pylint: disable=unused-argument,unnecessary-pass
    pass


@pytest.yield_fixture
def cluster_manager(
    tmp_path_factory: TempdirFactory,
    worker_id: str,
    request: FixtureRequest,
) -> Generator[parallel_run.ClusterManager, None, None]:
    cluster_manager_obj = parallel_run.ClusterManager(
        tmp_path_factory=tmp_path_factory, worker_id=worker_id, request=request
    )
    yield cluster_manager_obj
    cluster_manager_obj.on_test_stop()


@pytest.fixture
def cluster(
    cluster_manager: parallel_run.ClusterManager,
) -> clusterlib.ClusterLib:
    return cluster_manager.get()
