# pylint: disable=abstract-class-instantiated
import logging
import os
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Generator

import distutils.spawn
import pytest
from _pytest.config import Config
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib
from xdist import workermanage

from cardano_node_tests.utils import artifacts
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_conn
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools
from cardano_node_tests.utils import testnet_cleanup
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
    config._metadata["cardano-node"] = str(VERSIONS.node)
    config._metadata["cardano-node rev"] = VERSIONS.git_rev
    config._metadata["ghc"] = VERSIONS.ghc
    config._metadata["cardano-node-tests rev"] = helpers.get_current_commit()
    config._metadata[
        "cardano-node-tests url"
    ] = f"{helpers.GITHUB_URL}/tree/{helpers.get_current_commit()}"
    config._metadata["CARDANO_NODE_SOCKET_PATH"] = os.environ.get("CARDANO_NODE_SOCKET_PATH")
    config._metadata["cardano-cli exe"] = distutils.spawn.find_executable("cardano-cli") or ""

    config._metadata["HAS_DBSYNC"] = str(configuration.HAS_DBSYNC)
    if configuration.HAS_DBSYNC:
        config._metadata["db-sync"] = str(VERSIONS.dbsync)
        config._metadata["db-sync rev"] = VERSIONS.dbsync_git_rev
        config._metadata["db-sync ghc"] = VERSIONS.dbsync_ghc
        config._metadata["db-sync exe"] = str(configuration.DBSYNC_BIN)

    if "nix/store" not in config._metadata["cardano-cli exe"]:
        LOGGER.warning("WARNING: Not using `cardano-cli` from nix!")


def _skip_all_tests(config: Any, items: list) -> bool:
    """Skip all tests if specified on command line.

    Can be used for collecting all tests and having "skipped" result before running them for real.
    This is useful when a test run is interrupted (for any reason) and needs to be restarted with
    just tests that were not run yet.
    """
    if not config.getvalue("skipall"):
        return False

    marker = pytest.mark.skip(reason="collected, not run")

    for item in items:
        item.add_marker(marker)

    return True


def _mark_needs_dbsync_tests(items: list) -> bool:
    """Mark 'needs_dbsync' tests with additional markers."""
    skip_marker = pytest.mark.skip(reason="db-sync not available")
    dbsync_marker = pytest.mark.dbsync

    for item in items:
        if "needs_dbsync" not in item.keywords:
            continue

        # all tests marked with 'needs_dbsync' are db-sync tests, and should be marked
        # with the 'dbsync' marker as well
        if "dbsync" not in item.keywords:
            item.add_marker(dbsync_marker)

        # skip all tests that require db-sync when db-sync is not available
        if not configuration.HAS_DBSYNC:
            item.add_marker(skip_marker)

    return True


@pytest.mark.tryfirst
def pytest_collection_modifyitems(config: Any, items: list) -> None:
    # prevent on slave nodes (xdist)
    if hasattr(config, "slaveinput"):
        return

    if _skip_all_tests(config=config, items=items):
        return

    _mark_needs_dbsync_tests(items=items)


@pytest.fixture(scope="session")
def change_dir(tmp_path_factory: TempdirFactory) -> None:
    """Change CWD to temp directory before running tests."""
    tmp_path = temptools.get_pytest_worker_tmp(tmp_path_factory)
    os.chdir(tmp_path)
    LOGGER.info(f"Changed CWD to '{tmp_path}'.")


@pytest.fixture(scope="session")
def close_dbconn() -> Generator[None, None, None]:
    """Close connection to db-sync database at the end of session."""
    yield
    dbsync_conn.close_all()


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

    # copy collected artifacts to dir specified by `--artifacts-base-dir`
    artifacts.copy_artifacts(pytest_tmp_dir=pytest_tmp_dir, pytest_config=pytest_config)


def _testnet_cleanup(pytest_root_tmp: Path) -> None:
    """Perform testnet cleanup at the end of session."""
    if cluster_nodes.get_cluster_type().type != cluster_nodes.ClusterType.TESTNET_NOPOOLS:
        return

    # there's only one cluster instance for testnets, so we don't need to use cluster manager
    cluster_obj = cluster_nodes.get_cluster_type().get_cluster_obj()

    destdir = pytest_root_tmp.parent / f"cleanup-{pytest_root_tmp.stem}-{helpers.get_rand_str(8)}"
    destdir.mkdir(parents=True, exist_ok=True)

    with helpers.change_cwd(dir_path=destdir):
        testnet_cleanup.cleanup(cluster_obj=cluster_obj, location=pytest_root_tmp)


def _save_env_for_allure(pytest_config: Config) -> None:
    """Save environment info in a format for Allure."""
    alluredir = pytest_config.getoption("--alluredir")

    if not alluredir:
        return

    alluredir = configuration.LAUNCH_PATH / alluredir
    metadata: Dict[str, Any] = pytest_config._metadata  # type: ignore
    with open(alluredir / "environment.properties", "w+", encoding="utf-8") as infile:
        for k, v in metadata.items():
            if isinstance(v, dict):
                continue
            name = k.replace(" ", ".")
            infile.write(f"{name}={v}\n")


@pytest.fixture(scope="session")
def testenv_setup_teardown(
    tmp_path_factory: TempdirFactory, worker_id: str, request: FixtureRequest
) -> Generator[None, None, None]:
    # save environment info for Allure
    _save_env_for_allure(request.config)

    pytest_root_tmp = temptools.get_pytest_root_tmp(tmp_path_factory)

    # if cluster was started outside of test framework, do nothing
    if configuration.DEV_CLUSTER_RUNNING:
        # TODO: check that socket is open and print error if not
        yield
        _testnet_cleanup(pytest_root_tmp=pytest_root_tmp)
        return

    helpers.touch(pytest_root_tmp / f".started_session_{worker_id}")

    yield

    with locking.FileLockIfXdist(f"{pytest_root_tmp}/{cluster_management.CLUSTER_LOCK}"):
        cluster_manager_obj = cluster_management.ClusterManager(
            tmp_path_factory=tmp_path_factory, worker_id=worker_id, pytest_config=request.config
        )
        cluster_manager_obj.save_worker_cli_coverage()

        (pytest_root_tmp / f".started_session_{worker_id}").unlink()
        if not list(pytest_root_tmp.glob(".started_session_*")):
            _testnet_cleanup(pytest_root_tmp=pytest_root_tmp)
            _stop_all_cluster_instances(
                tmp_path_factory=tmp_path_factory,
                worker_id=worker_id,
                pytest_config=request.config,
                pytest_tmp_dir=pytest_root_tmp,
            )


@pytest.fixture(scope="session", autouse=True)
def session_autouse(change_dir: Any, close_dbconn: Any, testenv_setup_teardown: Any) -> None:
    """Autouse session fixtures that are required for session setup and teardown."""
    # pylint: disable=unused-argument,unnecessary-pass
    pass


@pytest.fixture(scope="module")
def testfile_temp_dir(tmp_path_factory: TempdirFactory) -> Path:
    """Return a temporary dir for storing test artifacts.

    The dir is specific to a single test file.
    """
    # get a dir path based on the test file running
    dir_path = (
        (os.getenv("PYTEST_CURRENT_TEST") or "unknown")
        .split("::")[0]
        .split("/")[-1]
        .replace(".", "_")
    )

    p = temptools.get_pytest_worker_tmp(tmp_path_factory).joinpath(dir_path).resolve()

    p.mkdir(exist_ok=True, parents=True)

    return p


@pytest.fixture
def cd_testfile_temp_dir(testfile_temp_dir: Path) -> Generator[Path, None, None]:
    """Change to a temporary dir specific to a test file."""
    with helpers.change_cwd(testfile_temp_dir):
        yield testfile_temp_dir


@pytest.fixture(autouse=True)
def function_autouse(cd_testfile_temp_dir: Generator[Path, None, None]) -> None:
    """Autouse function fixtures that are required for each test setup and teardown."""
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
