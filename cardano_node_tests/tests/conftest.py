# pylint: disable=abstract-class-instantiated
import json
import logging
import os
import pathlib as pl
import shutil
import typing as tp

import pytest
from _pytest.config import Config
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempPathFactory
from cardano_clusterlib import clusterlib
from pytest_metadata.plugin import metadata_key
from xdist import workermanage

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.utils import artifacts
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

# use custom xdist scheduler
pytest_plugins = ("cardano_node_tests.pytest_plugins.xdist_scheduler",)


class LogsError(Exception):
    pass


def pytest_addoption(parser: tp.Any) -> None:
    parser.addoption(
        artifacts.CLI_COVERAGE_ARG,
        action="store",
        type=helpers.check_dir_arg,
        default="",
        help="Path to directory for storing coverage info",
    )
    parser.addoption(
        artifacts.ARTIFACTS_BASE_DIR_ARG,
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


def pytest_configure(config: tp.Any) -> None:
    # don't bother collecting metadata if all tests are skipped
    if config.getvalue("skipall"):
        return

    config.stash[metadata_key]["cardano-node"] = str(VERSIONS.node)
    config.stash[metadata_key]["cardano-node rev"] = VERSIONS.git_rev
    config.stash[metadata_key]["cardano-node ghc"] = VERSIONS.ghc
    config.stash[metadata_key]["cardano-cli"] = str(VERSIONS.cli)
    config.stash[metadata_key]["cardano-cli rev"] = VERSIONS.cli_git_rev
    config.stash[metadata_key]["cardano-cli ghc"] = VERSIONS.cli_ghc
    config.stash[metadata_key]["CLUSTER_ERA"] = configuration.CLUSTER_ERA
    config.stash[metadata_key]["TX_ERA"] = configuration.TX_ERA
    config.stash[metadata_key]["SCRIPTS_DIRNAME"] = configuration.SCRIPTS_DIRNAME
    config.stash[metadata_key]["ENABLE_P2P"] = str(configuration.ENABLE_P2P)
    config.stash[metadata_key]["MIXED_P2P"] = str(configuration.MIXED_P2P)
    config.stash[metadata_key]["NUM_POOLS"] = str(configuration.NUM_POOLS)
    config.stash[metadata_key]["DB_BACKEND"] = configuration.DB_BACKEND
    config.stash[metadata_key]["cardano-node-tests rev"] = helpers.get_current_commit()
    config.stash[metadata_key][
        "cardano-node-tests url"
    ] = f"{helpers.GITHUB_URL}/tree/{helpers.get_current_commit()}"
    config.stash[metadata_key]["CARDANO_NODE_SOCKET_PATH"] = os.environ.get(
        "CARDANO_NODE_SOCKET_PATH"
    )
    config.stash[metadata_key]["cardano-cli exe"] = shutil.which("cardano-cli") or ""
    config.stash[metadata_key]["cardano-node exe"] = shutil.which("cardano-node") or ""
    config.stash[metadata_key]["cardano-submit-api exe"] = shutil.which("cardano-submit-api") or ""

    testrun_name = os.environ.get("CI_TESTRUN_NAME")
    if testrun_name:
        skip_passed = os.environ.get("CI_SKIP_PASSED") == "true"
        config.stash[metadata_key]["CI_TESTRUN_NAME"] = testrun_name
        config.stash[metadata_key]["CI_SKIP_PASSED"] = str(skip_passed)

    network_magic = configuration.NETWORK_MAGIC_LOCAL
    if configuration.BOOTSTRAP_DIR:
        with open(
            pl.Path(configuration.BOOTSTRAP_DIR) / "genesis-shelley.json", encoding="utf-8"
        ) as in_fp:
            genesis = json.load(in_fp)
        network_magic = genesis["networkMagic"]
    config.stash[metadata_key]["network magic"] = network_magic

    config.stash[metadata_key]["HAS_DBSYNC"] = str(configuration.HAS_DBSYNC)
    if configuration.HAS_DBSYNC:
        config.stash[metadata_key]["db-sync"] = str(VERSIONS.dbsync)
        config.stash[metadata_key]["db-sync rev"] = VERSIONS.dbsync_git_rev
        config.stash[metadata_key]["db-sync ghc"] = VERSIONS.dbsync_ghc
        config.stash[metadata_key]["db-sync exe"] = str(configuration.DBSYNC_BIN)

    if "nix/store" not in config.stash[metadata_key]["cardano-cli exe"]:
        LOGGER.warning("WARNING: Not using `cardano-cli` from nix store!")
    if "nix/store" not in config.stash[metadata_key]["cardano-node exe"]:
        LOGGER.warning("WARNING: Not using `cardano-node` from nix store!")


def _skip_all_tests(config: tp.Any, items: list) -> bool:
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


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: tp.Any, items: list) -> None:
    # prevent on slave nodes (xdist)
    if hasattr(config, "slaveinput"):
        return

    if _skip_all_tests(config=config, items=items):
        return

    _mark_needs_dbsync_tests(items=items)


@pytest.fixture(scope="session")
def init_pytest_temp_dirs(tmp_path_factory: TempPathFactory) -> None:
    """Init `PytestTempDirs`."""
    temptools.PytestTempDirs.init(tmp_path_factory=tmp_path_factory)


@pytest.fixture(scope="session")
def change_dir() -> None:
    """Change CWD to temp directory before running tests."""
    tmp_path = temptools.get_pytest_worker_tmp()
    os.chdir(tmp_path)
    LOGGER.info(f"Changed CWD to '{tmp_path}'.")


@pytest.fixture(scope="session")
def close_dbconn() -> tp.Generator[None, None, None]:
    """Close connection to db-sync database at the end of session."""
    yield
    dbsync_conn.close_all()


def _save_all_cluster_instances_artifacts(
    cluster_manager_obj: cluster_management.ClusterManager,
) -> None:
    """Save artifacts of all cluster instances after all tests are finished."""
    cluster_manager_obj.log("running `_save_all_cluster_instances_artifacts`")

    # stop all cluster instances
    with helpers.ignore_interrupt():
        cluster_manager_obj.save_all_clusters_artifacts()


def _stop_all_cluster_instances(cluster_manager_obj: cluster_management.ClusterManager) -> None:
    """Stop all cluster instances after all tests are finished."""
    cluster_manager_obj.log("running `_stop_all_cluster_instances`")

    # stop all cluster instances
    with helpers.ignore_interrupt():
        cluster_manager_obj.stop_all_clusters()


def _testnet_cleanup(pytest_root_tmp: pl.Path) -> None:
    """Perform testnet cleanup at the end of session."""
    if cluster_nodes.get_cluster_type().type != cluster_nodes.ClusterType.TESTNET:
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
    metadata: tp.Dict[str, tp.Any] = pytest_config.stash[metadata_key]  # type: ignore
    with open(alluredir / "environment.properties", "w+", encoding="utf-8") as infile:
        for k, v in metadata.items():
            if isinstance(v, dict):
                continue
            name = k.replace(" ", ".")
            infile.write(f"{name}={v}\n")


@pytest.fixture(scope="session")
def testenv_setup_teardown(
    worker_id: str, request: FixtureRequest
) -> tp.Generator[None, None, None]:
    """Setup and teardown test environment."""
    pytest_root_tmp = temptools.get_pytest_root_tmp()
    running_session_glob = ".running_session"

    with locking.FileLockIfXdist(f"{pytest_root_tmp}/{cluster_management.CLUSTER_LOCK}"):
        # Save environment info for Allure
        if not list(pytest_root_tmp.glob(f"{running_session_glob}_*")):
            _save_env_for_allure(request.config)

        (pytest_root_tmp / f"{running_session_glob}_{worker_id}").touch()

    yield

    with locking.FileLockIfXdist(f"{pytest_root_tmp}/{cluster_management.CLUSTER_LOCK}"):
        # Save CLI coverage to dir specified by `--cli-coverage-dir`
        cluster_manager_obj = cluster_management.ClusterManager(
            worker_id=worker_id, pytest_config=request.config
        )
        cluster_manager_obj.save_worker_cli_coverage()

        # Remove file indicating that testing session on this worker is running
        (pytest_root_tmp / f"{running_session_glob}_{worker_id}").unlink()

        # Perform cleanup if this is the last running pytest worker
        if not list(pytest_root_tmp.glob(f"{running_session_glob}_*")):
            # Perform testnet cleanup
            _testnet_cleanup(pytest_root_tmp=pytest_root_tmp)

            if configuration.DEV_CLUSTER_RUNNING:
                # Save cluster artifacts
                artifacts_base_dir = request.config.getoption(artifacts.ARTIFACTS_BASE_DIR_ARG)
                if artifacts_base_dir:
                    state_dir = cluster_nodes.get_cluster_env().state_dir
                    artifacts.save_cluster_artifacts(save_dir=pytest_root_tmp, state_dir=state_dir)
            elif configuration.KEEP_CLUSTERS_RUNNING:
                # Keep cluster instances running. Stopping them would need to be handled manually.
                _save_all_cluster_instances_artifacts(cluster_manager_obj=cluster_manager_obj)
            else:
                _save_all_cluster_instances_artifacts(cluster_manager_obj=cluster_manager_obj)
                _stop_all_cluster_instances(cluster_manager_obj=cluster_manager_obj)

            # Copy collected artifacts to dir specified by `--artifacts-base-dir`
            artifacts.copy_artifacts(pytest_tmp_dir=pytest_root_tmp, pytest_config=request.config)


@pytest.fixture(scope="session", autouse=True)
def session_autouse(
    init_pytest_temp_dirs: None,  # noqa: ARG001
    change_dir: None,  # noqa: ARG001
    close_dbconn: tp.Any,  # noqa: ARG001
    testenv_setup_teardown: tp.Any,  # noqa: ARG001
) -> None:
    """Autouse session fixtures that are required for session setup and teardown."""
    # pylint: disable=unused-argument,unnecessary-pass
    pass


@pytest.fixture(scope="module")
def testfile_temp_dir() -> pl.Path:
    """Return a temporary dir for storing test artifacts.

    The dir is specific to a single test file.
    """
    # get a dir path based on the test file running
    dir_path = (
        (os.environ.get("PYTEST_CURRENT_TEST") or "unknown")
        .split("::")[0]
        .split("/")[-1]
        .replace(".", "_")
    )

    p = temptools.get_pytest_worker_tmp().joinpath(dir_path).resolve()

    p.mkdir(exist_ok=True, parents=True)

    return p


@pytest.fixture
def cd_testfile_temp_dir(testfile_temp_dir: pl.Path) -> tp.Generator[pl.Path, None, None]:
    """Change to a temporary dir specific to a test file."""
    with helpers.change_cwd(testfile_temp_dir):
        yield testfile_temp_dir


@pytest.fixture(autouse=True)
def function_autouse(
    cd_testfile_temp_dir: tp.Generator[pl.Path, None, None],  # noqa: ARG001
) -> None:
    """Autouse function fixtures that are required for each test setup and teardown."""
    # pylint: disable=unused-argument,unnecessary-pass
    pass


def _raise_logs_error(errors: str) -> None:
    """Report errors found in cluster log files by raising `LogsError` with errors details."""
    if not errors:
        return
    raise LogsError(f"Errors found in cluster log files:\n{errors}") from None


@pytest.fixture
def cluster_manager(
    worker_id: str,
    request: FixtureRequest,
) -> tp.Generator[cluster_management.ClusterManager, None, None]:
    """Return instance of `cluster_management.ClusterManager`."""
    # hide from traceback to make logs errors more readable
    __tracebackhide__ = True  # pylint: disable=unused-variable

    cluster_manager_obj = cluster_management.ClusterManager(
        worker_id=worker_id, pytest_config=request.config
    )

    yield cluster_manager_obj

    errors = cluster_manager_obj.get_logfiles_errors()
    cluster_manager_obj.on_test_stop()
    _raise_logs_error(errors)


@pytest.fixture
def cluster(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Return instance of `clusterlib.ClusterLib`."""
    return cluster_manager.get()


@pytest.fixture
def cluster_singleton(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Lock whole cluster instance and return instance of `clusterlib.ClusterLib`."""
    return cluster_manager.get(lock_resources=[cluster_management.Resources.CLUSTER])


@pytest.fixture
def cluster_lock_pool(
    cluster_manager: cluster_management.ClusterManager,
) -> tp.Tuple[clusterlib.ClusterLib, str]:
    """Lock any pool and return instance of `clusterlib.ClusterLib`."""
    cluster_obj = cluster_manager.get(
        lock_resources=[
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
        ]
    )
    pool_name = cluster_manager.get_locked_resources(
        from_set=cluster_management.Resources.ALL_POOLS
    )[0]
    return cluster_obj, pool_name


@pytest.fixture
def cluster_use_pool(
    cluster_manager: cluster_management.ClusterManager,
) -> tp.Tuple[clusterlib.ClusterLib, str]:
    """Mark any pool as "in use" and return instance of `clusterlib.ClusterLib`."""
    cluster_obj = cluster_manager.get(
        use_resources=[
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
        ]
    )
    pool_name = cluster_manager.get_used_resources(from_set=cluster_management.Resources.ALL_POOLS)[
        0
    ]
    return cluster_obj, pool_name
