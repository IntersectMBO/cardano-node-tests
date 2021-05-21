import distutils.spawn
import logging
import os
from pathlib import Path
from typing import Any
from typing import Generator

import pytest
from _pytest.config import Config
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib
from xdist import workermanage

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_conn
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

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
    # for detecting if code is running in pytest, or imported while e.g. building documentation
    configuration._called_from_test = True  # type: ignore
    config._metadata["cardano-node"] = str(VERSIONS.node)
    config._metadata["cardano-node rev"] = VERSIONS.git_rev
    config._metadata["ghc"] = VERSIONS.ghc
    config._metadata["cardano-node-tests rev"] = helpers.get_current_commit()
    config._metadata[
        "cardano-node-tests url"
    ] = f"{helpers.GITHUB_URL}/tree/{helpers.get_current_commit()}"
    config._metadata["HAS_DBSYNC"] = str(configuration.HAS_DBSYNC)
    config._metadata["CARDANO_NODE_SOCKET_PATH"] = os.environ.get("CARDANO_NODE_SOCKET_PATH")
    config._metadata["cardano-cli exe"] = distutils.spawn.find_executable("cardano-cli") or ""

    if "nix/store" not in config._metadata["cardano-cli exe"]:
        LOGGER.warning("WARNING: Not using `cardano-cli` from nix!")


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


@pytest.fixture(scope="session")
def close_dbconn() -> Generator[None, None, None]:
    """Close connection to db-sync database at the end of session."""
    yield
    dbsync_conn.DBSync.close_all()


def _stop_all_cluster_instances(
    tmp_path_factory: TempdirFactory, worker_id: str, pytest_config: Config, pytest_tmp_dir: Path
) -> None:
    """Stop all cluster instances after all tests are finished."""
    cluster_manager_obj = cluster_management.ClusterManager(
        tmp_path_factory=tmp_path_factory, worker_id=worker_id, pytest_config=pytest_config
    )
    cluster_manager_obj._log("running `_stop_all_cluster_instances`")

    # stop all cluster instances
    with helpers.ignore_interrupt():
        cluster_manager_obj.stop_all_clusters()
    # save environment info for Allure
    helpers.save_env_for_allure(pytest_config)
    # save artifacts
    cluster_nodes.save_artifacts(pytest_tmp_dir=pytest_tmp_dir, pytest_config=pytest_config)


@pytest.fixture(scope="session")
def cluster_cleanup(
    tmp_path_factory: TempdirFactory, worker_id: str, request: FixtureRequest
) -> Generator[None, None, None]:
    pytest_tmp_dir = Path(tmp_path_factory.getbasetemp())

    if not worker_id or worker_id == "master":
        # if cluster was started outside of test framework, do nothing
        if cluster_management.DEV_CLUSTER_RUNNING:
            # TODO: check that socket is open and print error if not
            yield
            return

        yield

        cluster_manager_obj = cluster_management.ClusterManager(
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

    with helpers.FileLockIfXdist(f"{lock_dir}/{cluster_management.CLUSTER_LOCK}"):
        cluster_manager_obj = cluster_management.ClusterManager(
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
def session_autouse(change_dir: Any, close_dbconn: Any, cluster_cleanup: Any) -> None:
    """Autouse session fixtures that are required for session setup and teardown."""
    # pylint: disable=unused-argument,unnecessary-pass
    pass


@pytest.fixture
def cluster_manager(
    tmp_path_factory: TempdirFactory,
    worker_id: str,
    request: FixtureRequest,
) -> Generator[cluster_management.ClusterManager, None, None]:
    """Return instance of `cluster_management.ClusterManager`."""
    cluster_manager_obj = cluster_management.ClusterManager(
        tmp_path_factory=tmp_path_factory, worker_id=worker_id, pytest_config=request.config
    )
    yield cluster_manager_obj
    cluster_manager_obj.on_test_stop()


@pytest.fixture
def cluster(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Return instance of `clusterlib.ClusterLib`."""
    return cluster_manager.get()
