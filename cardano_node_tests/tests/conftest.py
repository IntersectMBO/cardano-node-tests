import logging
import os
from pathlib import Path
from typing import Any
from typing import Generator

import pytest
from _pytest.config import Config
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory
from xdist import workermanage

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import devops_cluster
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import parallel_run

LOGGER = logging.getLogger(__name__)

# make sure there's enough time to stop all cluster instances at the end of session
workermanage.NodeManager.EXIT_TIMEOUT = 30


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
    parser.addoption(
        "--skipall",
        action="store_true",
        default=False,
        help="Skip all tests",
    )


def pytest_configure(config: Any) -> None:
    config._metadata["cardano-node"] = helpers.CARDANO_VERSION["cardano-node"]
    config._metadata["cardano-node rev"] = helpers.CARDANO_VERSION["git_rev"]
    config._metadata["ghc"] = helpers.CARDANO_VERSION["ghc"]
    config._metadata["cardano-node-tests rev"] = helpers.CURRENT_COMMIT
    config._metadata["cardano-node-tests url"] = helpers.GITHUB_TREE_URL


def _skip_all_tests(config: Any, items: list) -> None:
    """Skip all tests if specified on command line.

    Can be used for collecting all tests and having "skipped" result before running them for real.
    This is useful when a test run is interrupted (for any reason) and needs to be restarted with
    just tests that were not run yet.
    """
    # prevent on slave nodes (xdist)
    if hasattr(config, "slaveinput"):
        return

    if not config.getvalue("skipall"):
        return

    marker = pytest.mark.skip(reason="collected, not run")

    for item in items:
        item.add_marker(marker)


@pytest.mark.tryfirst
def pytest_collection_modifyitems(config: Any, items: list) -> None:
    _skip_all_tests(config=config, items=items)


@pytest.fixture(scope="session")
def change_dir(tmp_path_factory: TempdirFactory) -> None:
    """Change CWD to temp directory before running tests."""
    tmp_path = tmp_path_factory.getbasetemp()
    os.chdir(tmp_path)
    LOGGER.info(f"Changed CWD to '{tmp_path}'.")


def _stop_all_cluster_instances(
    tmp_path_factory: TempdirFactory, worker_id: str, pytest_config: Config, pytest_tmp_dir: Path
) -> None:
    """Stop all cluster instances after all tests are finished."""
    cluster_manager_obj = parallel_run.ClusterManager(
        tmp_path_factory=tmp_path_factory, worker_id=worker_id, pytest_config=pytest_config
    )
    cluster_manager_obj._log("running `_stop_all_cluster_instances`")

    # stop all cluster instances
    with helpers.ignore_interrupt():
        cluster_manager_obj.stop_all_clusters()
    # save environment info for Allure
    helpers.save_env_for_allure(pytest_config)
    # save artifacts
    devops_cluster.save_artifacts(pytest_tmp_dir=pytest_tmp_dir, pytest_config=pytest_config)


@pytest.fixture(scope="session")
def cluster_cleanup(
    tmp_path_factory: TempdirFactory, worker_id: str, request: FixtureRequest
) -> Generator[None, None, None]:
    pytest_tmp_dir = Path(tmp_path_factory.getbasetemp())

    if not worker_id or worker_id == "master":
        # cluster was started outside of test framework, do nothing
        if parallel_run.DEV_CLUSTER_RUNNING:
            # TODO: check if socket is open and print error of not
            yield
            return

        yield

        cluster_manager_obj = parallel_run.ClusterManager(
            tmp_path_factory=tmp_path_factory, worker_id=worker_id, pytest_config=request.config
        )
        cluster_manager_obj.save_worker_cli_coverage()
        _stop_all_cluster_instances(
            tmp_path_factory=tmp_path_factory,
            worker_id=worker_id,
            pytest_config=request.config,
            pytest_tmp_dir=pytest_tmp_dir,
        )
        return

    lock_dir = pytest_tmp_dir = pytest_tmp_dir.parent

    open(lock_dir / f".started_session_{worker_id}", "a").close()

    yield

    with helpers.FileLockIfXdist(f"{lock_dir}/{parallel_run.CLUSTER_LOCK}"):
        cluster_manager_obj = parallel_run.ClusterManager(
            tmp_path_factory=tmp_path_factory, worker_id=worker_id, pytest_config=request.config
        )
        cluster_manager_obj.save_worker_cli_coverage()

        os.remove(lock_dir / f".started_session_{worker_id}")
        if not list(lock_dir.glob(".started_session_*")):
            _stop_all_cluster_instances(
                tmp_path_factory=tmp_path_factory,
                worker_id=worker_id,
                pytest_config=request.config,
                pytest_tmp_dir=pytest_tmp_dir,
            )


@pytest.fixture(scope="session", autouse=True)
def session_autouse(change_dir: Any, cluster_cleanup: Any) -> None:
    """Autouse required session fixtures."""
    # pylint: disable=unused-argument,unnecessary-pass
    pass


@pytest.fixture
def cluster_manager(
    tmp_path_factory: TempdirFactory,
    worker_id: str,
    request: FixtureRequest,
) -> Generator[parallel_run.ClusterManager, None, None]:
    cluster_manager_obj = parallel_run.ClusterManager(
        tmp_path_factory=tmp_path_factory, worker_id=worker_id, pytest_config=request.config
    )
    yield cluster_manager_obj
    cluster_manager_obj.on_test_stop()


@pytest.fixture
def cluster(
    cluster_manager: parallel_run.ClusterManager,
) -> clusterlib.ClusterLib:
    return cluster_manager.get()
