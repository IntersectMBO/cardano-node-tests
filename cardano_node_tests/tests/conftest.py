# pylint: disable=abstract-class-instantiated
import distutils.spawn
import logging
import os
import shutil
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Generator
from typing import Optional

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

    config._metadata["HAS_SMASH"] = str(configuration.HAS_SMASH)
    if configuration.HAS_SMASH:
        config._metadata["smash"] = str(VERSIONS.smash)
        config._metadata["smash rev"] = VERSIONS.smash_git_rev
        config._metadata["smash ghc"] = VERSIONS.smash_ghc
        config._metadata["smash exe"] = str(configuration.SMASH_BIN)

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


def _mark_needs_smash_tests(items: list) -> bool:
    """Mark 'needs_smash' tests with additional markers."""
    skip_marker = pytest.mark.skip(reason="smash not available")
    smash_marker = pytest.mark.smash

    for item in items:
        if "needs_smash" not in item.keywords:
            continue

        # all tests marked with 'needs_smash' are smash tests, and should be marked
        # with the 'smash' marker as well
        if "smash" not in item.keywords:
            item.add_marker(smash_marker)

        # skip all tests that require smash when smash is not available
        if not configuration.HAS_SMASH:
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
    _mark_needs_smash_tests(items=items)


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


def _copy_artifacts(pytest_tmp_dir: Path, artifacts_dir: Path) -> Optional[Path]:
    """Copy collected tests and cluster artifacts to artifacts dir."""
    pytest_tmp_dir = pytest_tmp_dir.resolve()
    if not pytest_tmp_dir.is_dir():
        return None

    destdir = artifacts_dir / f"{pytest_tmp_dir.stem}-{helpers.get_rand_str(8)}"
    if destdir.resolve().is_dir():
        shutil.rmtree(destdir)
    shutil.copytree(pytest_tmp_dir, destdir, symlinks=True, ignore_dangling_symlinks=True)

    return destdir


def _save_artifacts(pytest_tmp_dir: Path, pytest_config: Config) -> None:
    """Save tests and cluster artifacts."""
    artifacts_base_dir = pytest_config.getoption("--artifacts-base-dir")
    if not artifacts_base_dir:
        return

    artifacts_dir = Path(artifacts_base_dir)
    _copy_artifacts(pytest_tmp_dir, artifacts_dir)
    LOGGER.info(f"Collected artifacts saved to '{artifacts_dir}'.")


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

    # save artifacts
    _save_artifacts(pytest_tmp_dir=pytest_tmp_dir, pytest_config=pytest_config)


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
    pytest_root_tmp = temptools.get_pytest_root_tmp(tmp_path_factory)

    # save environment info for Allure
    _save_env_for_allure(request.config)

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
        _testnet_cleanup(pytest_root_tmp=pytest_root_tmp)
        _stop_all_cluster_instances(
            tmp_path_factory=tmp_path_factory,
            worker_id=worker_id,
            pytest_config=request.config,
            pytest_tmp_dir=pytest_root_tmp,
        )
        return

    # pylint: disable=consider-using-with
    open(pytest_root_tmp / f".started_session_{worker_id}", "a", encoding="utf-8").close()

    yield

    with locking.FileLockIfXdist(f"{pytest_root_tmp}/{cluster_management.CLUSTER_LOCK}"):
        cluster_manager_obj = cluster_management.ClusterManager(
            tmp_path_factory=tmp_path_factory, worker_id=worker_id, pytest_config=request.config
        )
        cluster_manager_obj.save_worker_cli_coverage()

        os.remove(pytest_root_tmp / f".started_session_{worker_id}")
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
