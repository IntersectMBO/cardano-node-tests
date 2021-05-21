"""Functionality for parallel execution of tests on multiple cluster instances."""
import contextlib
import dataclasses
import datetime
import hashlib
import inspect
import logging
import os
import random
import time
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Iterator
from typing import Optional

import pytest
from _pytest.config import Config
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cli_coverage
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import cluster_scripts
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils.types import UnpackableSequence

LOGGER = logging.getLogger(__name__)


class Resources:
    """Resources that can be used for `lock_resources` or `use_resources`."""

    POOL1 = "node-pool1"
    POOL2 = "node-pool2"
    POOL3 = "node-pool3"
    RESERVES = "reserves"
    TREASURY = "treasury"


CLUSTER_LOCK = ".cluster.lock"
LOG_LOCK = ".manager_log.lock"
RUN_LOG_FILE = ".cluster_manager.log"
TEST_SINGLETON_FILE = ".test_singleton"
RESOURCE_LOCKED_GLOB = ".resource_locked"
RESOURCE_IN_USE_GLOB = ".resource_in_use"
RESTART_NEEDED_GLOB = ".needs_restart"
RESTART_IN_PROGRESS_GLOB = ".restart_in_progress"
RESTART_AFTER_MARK_GLOB = ".restart_after_mark"
TEST_RUNNING_GLOB = ".test_running"
TEST_CURR_MARK_GLOB = ".curr_test_mark"
TEST_MARK_STARTING_GLOB = ".starting_marked_tests"

WORKERS_COUNT = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT") or 1)
CLUSTERS_COUNT = int(configuration.CLUSTERS_COUNT or (WORKERS_COUNT if WORKERS_COUNT <= 9 else 9))
CLUSTER_DIR_TEMPLATE = "cluster"
CLUSTER_RUNNING_FILE = ".cluster_running"
CLUSTER_STOPPED_FILE = ".cluster_stopped"
CLUSTER_DEAD_FILE = ".cluster_dead"

DEV_CLUSTER_RUNNING = bool(os.environ.get("DEV_CLUSTER_RUNNING"))
FORBID_RESTART = bool(os.environ.get("FORBID_RESTART"))

if CLUSTERS_COUNT > 1 and DEV_CLUSTER_RUNNING:
    raise RuntimeError("Cannot run multiple cluster instances when 'DEV_CLUSTER_RUNNING' is set.")

CLUSTER_START_CMDS_LOG = "start_cluster_cmds.log"


def _kill_supervisor(instance_num: int) -> None:
    """Kill supervisor process."""
    port_num = (
        cluster_nodes.get_cluster_type().cluster_scripts.get_instance_ports(instance_num).supervisor
    )
    port_str = f":{port_num}"
    netstat = helpers.run_command("netstat -plnt").decode().splitlines()
    for line in netstat:
        if port_str not in line:
            continue
        line = line.replace("  ", " ").strip()
        pid = line.split(" ")[-1].split("/")[0]
        os.kill(int(pid), 15)
        return


def _get_fixture_hash() -> int:
    """Get hash of fixture, using hash of `filename#lineno`."""
    # get past `cache_fixture` and `contextmanager` to the fixture
    calling_frame = inspect.currentframe().f_back.f_back.f_back  # type: ignore
    lineno = calling_frame.f_lineno  # type: ignore
    fpath = calling_frame.f_globals["__file__"]  # type: ignore
    hash_str = f"{fpath}#L{lineno}"
    hash_num = int(hashlib.sha1(hash_str.encode("utf-8")).hexdigest(), 16)
    return hash_num


@dataclasses.dataclass
class ClusterManagerCache:
    """Cache for a single cluster instance."""

    cluster_obj: Optional[clusterlib.ClusterLib] = None
    test_data: dict = dataclasses.field(default_factory=dict)
    addrs_data: dict = dataclasses.field(default_factory=dict)
    last_checksum: str = ""


@dataclasses.dataclass
class FixtureCache:
    """Cache for a fixture."""

    value: Any


@dataclasses.dataclass
class MarkedTestsStatus:
    last_seen_mark: str = ""
    no_marked_tests_iter: int = 0


class ClusterManager:
    manager_cache: Dict[int, ClusterManagerCache] = {}

    @classmethod
    def get_cache(cls) -> Dict[int, ClusterManagerCache]:
        return cls.manager_cache

    def __init__(
        self, tmp_path_factory: TempdirFactory, worker_id: str, pytest_config: Config
    ) -> None:
        self.cluster_obj: Optional[clusterlib.ClusterLib] = None
        self.worker_id = worker_id
        self.pytest_config = pytest_config
        self.tmp_path_factory = tmp_path_factory
        self.pytest_tmp_dir = Path(tmp_path_factory.getbasetemp())

        self.is_xdist = helpers.IS_XDIST
        if self.is_xdist:
            self.lock_dir = self.pytest_tmp_dir.parent
            self.range_num = 5
            self.num_of_instances = CLUSTERS_COUNT
        else:
            self.lock_dir = self.pytest_tmp_dir
            self.range_num = 1
            self.num_of_instances = 1

        self.cluster_lock = f"{self.lock_dir}/{CLUSTER_LOCK}"
        self.log_lock = f"{self.lock_dir}/{LOG_LOCK}"
        self.manager_log = self._init_log()

        self._cluster_instance_num = -1

    @property
    def cluster_instance_num(self) -> int:
        if self._cluster_instance_num == -1:
            raise RuntimeError("Cluster instance not set.")
        return self._cluster_instance_num

    @property
    def cache(self) -> ClusterManagerCache:
        _cache = self.get_cache()
        instance_cache = _cache.get(self.cluster_instance_num)
        if not instance_cache:
            instance_cache = ClusterManagerCache()
            _cache[self.cluster_instance_num] = instance_cache
        return instance_cache

    @property
    def instance_dir(self) -> Path:
        instance_dir = self.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{self.cluster_instance_num}"
        return instance_dir

    @property
    def ports(self) -> cluster_scripts.InstancePorts:
        """Return port mappings for current cluster instance."""
        return cluster_nodes.get_cluster_type().cluster_scripts.get_instance_ports(
            self.cluster_instance_num
        )

    def _init_log(self) -> Path:
        """Return path to run log file."""
        env_log = os.environ.get("SCHEDULING_LOG")
        if env_log:
            run_log = Path(env_log).expanduser()
            if not run_log.is_absolute():
                # the path is relative to LAUNCH_PATH (current path can differ)
                run_log = helpers.LAUNCH_PATH / run_log
            # create the log file if it doesn't exist
            open(run_log, "a").close()
        else:
            run_log = self.lock_dir / RUN_LOG_FILE

        return run_log.resolve()

    def _log(self, msg: str) -> None:
        """Log message."""
        if not self.manager_log.is_file():
            return
        with helpers.FileLockIfXdist(self.log_lock):
            with open(self.manager_log, "a") as logfile:
                logfile.write(f"{datetime.datetime.now()} on {self.worker_id}: {msg}\n")

    def _create_startup_files_dir(self, instance_num: int) -> Path:
        instance_dir = self.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
        rand_str = clusterlib.get_rand_str(8)
        startup_files_dir = instance_dir / "startup_files" / rand_str
        startup_files_dir.mkdir(exist_ok=True, parents=True)
        return startup_files_dir

    def save_worker_cli_coverage(self) -> None:
        """Save CLI coverage info collected by this pytest worker.

        Must be done when session of the worker is about to finish, because there's no other job to
        call `_reload_cluster_obj` and thus save CLI coverage of the old `cluster_obj` instance.
        """
        self._log("called `save_worker_cli_coverage`")
        worker_cache = self.get_cache()
        for cache_instance in worker_cache.values():
            cluster_obj = cache_instance.cluster_obj
            if not cluster_obj:
                continue

            cli_coverage.save_cli_coverage(
                cluster_obj=cluster_obj, pytest_config=self.pytest_config
            )

    def stop_all_clusters(self) -> None:
        """Stop all cluster instances."""
        self._log("called `stop_all_clusters`")
        for instance_num in range(self.num_of_instances):
            instance_dir = self.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
            if (
                not (instance_dir / CLUSTER_RUNNING_FILE).exists()
                or (instance_dir / CLUSTER_STOPPED_FILE).exists()
            ):
                self._log(f"cluster instance {instance_num} not running")
                continue

            startup_files = cluster_nodes.get_cluster_type().cluster_scripts.prepare_scripts_files(
                destdir=self._create_startup_files_dir(instance_num),
                instance_num=instance_num,
            )
            cluster_nodes.set_cluster_env(instance_num)
            self._log(
                f"stopping cluster instance {instance_num} with `{startup_files.stop_script}`"
            )

            state_dir = cluster_nodes.get_cluster_env().state_dir

            try:
                cluster_nodes.stop_cluster(cmd=str(startup_files.stop_script))
            except Exception as exc:
                LOGGER.error(f"While stopping cluster: {exc}")

            cli_coverage.save_start_script_coverage(
                log_file=state_dir / CLUSTER_START_CMDS_LOG,
                pytest_config=self.pytest_config,
            )
            cluster_nodes.save_cluster_artifacts(artifacts_dir=self.pytest_tmp_dir, clean=True)
            open(instance_dir / CLUSTER_STOPPED_FILE, "a").close()
            self._log(f"stopped cluster instance {instance_num}")

    def set_needs_restart(self) -> None:
        """Indicate that the cluster needs restart."""
        with helpers.FileLockIfXdist(self.cluster_lock):
            self._log(f"c{self.cluster_instance_num}: called `set_needs_restart`")
            open(self.instance_dir / f"{RESTART_NEEDED_GLOB}_{self.worker_id}", "a").close()

    @contextlib.contextmanager
    def restart_on_failure(self) -> Iterator[None]:
        """Indicate that the cluster needs restart if command failed - context manager."""
        try:
            yield
        except Exception:
            self.set_needs_restart()
            raise

    @contextlib.contextmanager
    def cache_fixture(self) -> Iterator[FixtureCache]:
        """Cache fixture value - context manager."""
        curline_hash = _get_fixture_hash()
        cached_value = self.cache.test_data.get(curline_hash)
        container = FixtureCache(value=cached_value)

        yield container

        if container.value != cached_value:
            self.cache.test_data[curline_hash] = container.value

    def on_test_stop(self) -> None:
        """Perform actions after the test finished."""
        if self._cluster_instance_num == -1:
            return

        with helpers.FileLockIfXdist(self.cluster_lock):
            self._log(f"c{self.cluster_instance_num}: called `on_test_stop`")

            # remove resource locking files created by the worker
            resource_locking_files = list(
                self.instance_dir.glob(f"{RESOURCE_LOCKED_GLOB}_*_{self.worker_id}")
            )
            for f in resource_locking_files:
                os.remove(f)

            # remove "resource in use" files created by the worker
            resource_in_use_files = list(
                self.instance_dir.glob(f"{RESOURCE_IN_USE_GLOB}_*_{self.worker_id}")
            )
            for f in resource_in_use_files:
                os.remove(f)

            # remove file that indicates that a test is running on the worker
            try:
                os.remove(self.instance_dir / f"{TEST_RUNNING_GLOB}_{self.worker_id}")
            except FileNotFoundError:
                pass

            # remove file that indicates the test was singleton
            try:
                os.remove(self.instance_dir / TEST_SINGLETON_FILE)
            except FileNotFoundError:
                pass

            # search for errors in cluster logfiles
            errors = logfiles.search_cluster_artifacts()
            if errors:
                logfiles.report_artifacts_errors(errors)

    def get(
        self,
        singleton: bool = False,
        mark: str = "",
        lock_resources: UnpackableSequence = (),
        use_resources: UnpackableSequence = (),
        cleanup: bool = False,
        start_cmd: str = "",
    ) -> clusterlib.ClusterLib:
        """Wrap a call to `_ClusterGetter.get`."""
        return _ClusterGetter(self).get(
            singleton=singleton,
            mark=mark,
            lock_resources=lock_resources,
            use_resources=use_resources,
            cleanup=cleanup,
            start_cmd=start_cmd,
        )


class _ClusterGetter:
    """Internal class that encapsulate functionality for getting a cluster instance."""

    def __init__(self, cluster_manager: ClusterManager) -> None:
        self.cm = cluster_manager  # pylint: disable=invalid-name

    def _restart_save_cluster_artifacts(self, clean: bool = False) -> None:
        """Save cluster artifacts (logs, certs, etc.) to pytest temp dir before cluster restart."""
        cluster_obj = self.cm.cache.cluster_obj
        if not cluster_obj:
            return

        cluster_nodes.save_cluster_artifacts(artifacts_dir=self.cm.pytest_tmp_dir, clean=clean)

    def _restart(self, start_cmd: str = "", stop_cmd: str = "") -> bool:  # noqa: C901
        """Restart cluster.

        Not called under global lock!
        """
        # pylint: disable=too-many-branches
        cluster_running_file = self.cm.instance_dir / CLUSTER_RUNNING_FILE

        # don't restart cluster if it was started outside of test framework
        if DEV_CLUSTER_RUNNING:
            if cluster_running_file.exists():
                LOGGER.warning(
                    "Ignoring requested cluster restart as 'DEV_CLUSTER_RUNNING' is set."
                )
            else:
                open(cluster_running_file, "a").close()
            return True

        # fail if cluster restart is forbidden and it was already started
        if FORBID_RESTART and cluster_running_file.exists():
            raise RuntimeError("Cannot restart cluster when 'FORBID_RESTART' is set.")

        self.cm._log(
            f"c{self.cm.cluster_instance_num}: called `_restart`, start_cmd='{start_cmd}', "
            f"stop_cmd='{stop_cmd}'"
        )

        startup_files = cluster_nodes.get_cluster_type().cluster_scripts.prepare_scripts_files(
            destdir=self.cm._create_startup_files_dir(self.cm.cluster_instance_num),
            instance_num=self.cm.cluster_instance_num,
            start_script=start_cmd,
            stop_script=stop_cmd,
        )

        state_dir = cluster_nodes.get_cluster_env().state_dir

        self.cm._log(
            f"c{self.cm.cluster_instance_num}: in `_restart`, new files "
            f"start_cmd='{startup_files.start_script}', "
            f"stop_cmd='{startup_files.stop_script}'"
        )

        excp: Optional[Exception] = None
        for i in range(2):
            if i > 0:
                self.cm._log(
                    f"c{self.cm.cluster_instance_num}: failed to start cluster:\n{excp}\nretrying"
                )
                time.sleep(0.2)

            try:
                cluster_nodes.stop_cluster(cmd=str(startup_files.stop_script))
            except Exception as err:
                self.cm._log(f"c{self.cm.cluster_instance_num}: failed to stop cluster:\n{err}")

            # save artifacts only when produced during this test run
            if cluster_running_file.exists():
                cli_coverage.save_start_script_coverage(
                    log_file=state_dir / CLUSTER_START_CMDS_LOG,
                    pytest_config=self.cm.pytest_config,
                )
                self._restart_save_cluster_artifacts(clean=True)

            try:
                _kill_supervisor(self.cm.cluster_instance_num)
            except Exception:
                pass

            try:
                cluster_obj = cluster_nodes.start_cluster(
                    cmd=str(startup_files.start_script), args=startup_files.start_script_args
                )
            except Exception as err:
                LOGGER.error(f"Failed to start cluster: {err}")
                excp = err
            else:
                break
        else:
            self.cm._log(
                f"c{self.cm.cluster_instance_num}: failed to start cluster:\n{excp}\ncluster dead"
            )
            if not helpers.IS_XDIST:
                pytest.exit(msg=f"Failed to start cluster, exception: {excp}", returncode=1)
            open(self.cm.instance_dir / CLUSTER_DEAD_FILE, "a").close()
            return False

        # setup faucet addresses
        tmp_path = Path(self.cm.tmp_path_factory.mktemp("addrs_data"))
        cluster_nodes.setup_test_addrs(cluster_obj, tmp_path)

        # create file that indicates that the cluster is running
        if not cluster_running_file.exists():
            open(cluster_running_file, "a").close()

        return True

    def _is_restart_needed(self, instance_num: int) -> bool:
        """Check if it is necessary to restart cluster."""
        instance_dir = self.cm.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
        if not (instance_dir / CLUSTER_RUNNING_FILE).exists():
            return True
        if list(instance_dir.glob(f"{RESTART_NEEDED_GLOB}_*")):
            return True
        return False

    def _on_marked_test_stop(self, instance_num: int) -> None:
        """Perform actions after marked tests are finished."""
        self.cm._log(f"c{instance_num}: in `_on_marked_test_stop`")
        instance_dir = self.cm.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"

        # set cluster to be restarted if needed
        restart_after_mark_files = list(instance_dir.glob(f"{RESTART_AFTER_MARK_GLOB}_*"))
        if restart_after_mark_files:
            for f in restart_after_mark_files:
                os.remove(f)
            self.cm._log(
                f"c{instance_num}: in `_on_marked_test_stop`, " "creating 'restart needed' file"
            )
            open(instance_dir / f"{RESTART_NEEDED_GLOB}_{self.cm.worker_id}", "a").close()

        # remove file that indicates that tests with the mark are running
        marked_running = list(instance_dir.glob(f"{TEST_CURR_MARK_GLOB}_*"))
        if marked_running:
            os.remove(marked_running[0])

    def _get_marked_tests_status(
        self, cache: Dict[int, MarkedTestsStatus], instance_num: int
    ) -> MarkedTestsStatus:
        """Return marked tests status for cluster instance."""
        if instance_num not in cache:
            cache[instance_num] = MarkedTestsStatus()
        marked_tests_status: MarkedTestsStatus = cache[instance_num]
        return marked_tests_status

    def _update_marked_tests(
        self,
        marked_tests_status: MarkedTestsStatus,
        active_mark_name: str,
        started_tests: list,
        instance_num: int,
    ) -> None:
        if started_tests or marked_tests_status.last_seen_mark != active_mark_name:
            marked_tests_status.no_marked_tests_iter = 0
        else:
            marked_tests_status.no_marked_tests_iter += 1

        # check if there is a stale mark status file
        if marked_tests_status.no_marked_tests_iter >= 10:
            self.cm._log(
                f"c{instance_num}: no marked tests running for a while, "
                "cleaning the mark status file"
            )
            self._on_marked_test_stop(instance_num)

        marked_tests_status.last_seen_mark = active_mark_name

    def _are_resources_usable(
        self, resources: UnpackableSequence, instance_dir: Path, instance_num: int
    ) -> bool:
        """Check if resources are locked or in use."""
        for res in resources:
            res_locked = list(instance_dir.glob(f"{RESOURCE_LOCKED_GLOB}_{res}_*"))
            if res_locked:
                self.cm._log(f"c{instance_num}: resource '{res}' locked, cannot start")
                break
            res_used = list(instance_dir.glob(f"{RESOURCE_IN_USE_GLOB}_{res}_*"))
            if res_used:
                self.cm._log(f"c{instance_num}: resource '{res}' in use, " "cannot lock and start")
                break
        else:
            self.cm._log(
                f"c{instance_num}: none of the resources in '{resources}' "
                "locked or in use, can start and lock"
            )
            return True
        return False

    def _are_resources_locked(
        self, resources: UnpackableSequence, instance_dir: Path, instance_num: int
    ) -> bool:
        """Check if resources are locked."""
        res_locked = []
        for res in resources:
            res_locked = list(instance_dir.glob(f"{RESOURCE_LOCKED_GLOB}_{res}_*"))
            if res_locked:
                self.cm._log(f"c{instance_num}: resource '{res}' locked, cannot start")
                break

        if not res_locked:
            self.cm._log(
                f"c{instance_num}: none of the resources in '{resources}' locked, " "can start"
            )
        return bool(res_locked)

    def _save_cli_coverage(self) -> None:
        """Save CLI coverage info collected by this `cluster_obj` instance."""
        self.cm._log("called `_save_cli_coverage`")
        cluster_obj = self.cm.cache.cluster_obj
        if not cluster_obj:
            return

        cli_coverage.save_cli_coverage(cluster_obj=cluster_obj, pytest_config=self.cm.pytest_config)

    def _reload_cluster_obj(self, state_dir: Path) -> None:
        """Reload cluster data if necessary."""
        addrs_data_checksum = helpers.checksum(state_dir / cluster_nodes.ADDRS_DATA)
        if addrs_data_checksum == self.cm.cache.last_checksum:
            return

        # save CLI coverage collected by the old `cluster_obj` instance
        self._save_cli_coverage()
        # replace the old `cluster_obj` instance and reload data
        self.cm.cache.cluster_obj = cluster_nodes.get_cluster_type().get_cluster_obj()
        self.cm.cache.test_data = {}
        self.cm.cache.addrs_data = cluster_nodes.load_addrs_data()
        self.cm.cache.last_checksum = addrs_data_checksum

    def _reuse_dev_cluster(self) -> clusterlib.ClusterLib:
        """Reuse cluster that was already started outside of test framework."""
        instance_num = 0
        self.cm._cluster_instance_num = instance_num
        cluster_nodes.set_cluster_env(instance_num)
        state_dir = cluster_nodes.get_cluster_env().state_dir

        # make sure instance dir exists
        instance_dir = self.cm.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
        instance_dir.mkdir(exist_ok=True, parents=True)

        cluster_obj = self.cm.cache.cluster_obj
        if not cluster_obj:
            cluster_obj = cluster_nodes.get_cluster_type().get_cluster_obj()

        # setup faucet addresses
        if not (state_dir / cluster_nodes.ADDRS_DATA).exists():
            tmp_path = state_dir / "addrs_data"
            tmp_path.mkdir(exist_ok=True, parents=True)
            cluster_nodes.setup_test_addrs(cluster_obj, tmp_path)

        # check if it is necessary to reload data
        self._reload_cluster_obj(state_dir=state_dir)

        return cluster_obj

    def get(  # noqa: C901
        self,
        singleton: bool = False,
        mark: str = "",
        lock_resources: UnpackableSequence = (),
        use_resources: UnpackableSequence = (),
        cleanup: bool = False,
        start_cmd: str = "",
    ) -> clusterlib.ClusterLib:
        """Return the `clusterlib.ClusterLib` instance once we can start the test.

        It checks current conditions and waits if the conditions don't allow to start the test
        right away.
        """
        # pylint: disable=too-many-statements,too-many-branches,too-many-locals

        # don't start new cluster if it was already started outside of test framework
        if DEV_CLUSTER_RUNNING:
            if start_cmd:
                LOGGER.warning(
                    f"Ignoring the '{start_cmd}' cluster start command as "
                    "'DEV_CLUSTER_RUNNING' is set."
                )
            return self._reuse_dev_cluster()

        if FORBID_RESTART and start_cmd:
            raise RuntimeError("Cannot use custom start command when 'FORBID_RESTART' is set.")

        selected_instance = -1
        restart_here = False
        restart_ready = False
        first_iteration = True
        sleep_delay = 1
        marked_tests_cache: Dict[int, MarkedTestsStatus] = {}

        if start_cmd:
            if not (singleton or mark):
                raise AssertionError(
                    "Custom start command can be used only together with `singleton` or `mark`"
                )
            # always clean after test(s) that started cluster with custom configuration
            cleanup = True

        # iterate until it is possible to start the test
        while True:
            if restart_ready:
                self._restart(start_cmd=start_cmd)

            if not first_iteration:
                helpers.xdist_sleep(random.random() * sleep_delay)

            # nothing time consuming can go under this lock as it will block all other workers
            with helpers.FileLockIfXdist(self.cm.cluster_lock):
                test_on_worker = list(
                    self.cm.lock_dir.glob(
                        f"{CLUSTER_DIR_TEMPLATE}*/{TEST_RUNNING_GLOB}_{self.cm.worker_id}"
                    )
                )

                # test is already running, nothing to set up
                if (
                    first_iteration
                    and test_on_worker
                    and self.cm._cluster_instance_num != -1
                    and self.cm.cache.cluster_obj
                ):
                    self.cm._log(f"{test_on_worker[0]} already exists")
                    return self.cm.cache.cluster_obj

                first_iteration = False  # needs to be set here, before the first `continue`
                self.cm._cluster_instance_num = -1

                # try all existing cluster instances
                for instance_num in range(self.cm.num_of_instances):
                    # if instance to run the test on was already decided, skip all other instances
                    # pylint: disable=consider-using-in
                    if selected_instance != -1 and instance_num != selected_instance:
                        continue

                    instance_dir = self.cm.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
                    instance_dir.mkdir(exist_ok=True)

                    # if the selected instance failed to start, move on to other instance
                    if (instance_dir / CLUSTER_DEAD_FILE).exists():
                        selected_instance = -1
                        restart_here = False
                        restart_ready = False
                        # remove status files that are checked by other workers
                        for sf in (
                            *instance_dir.glob(f"{TEST_CURR_MARK_GLOB}_*"),
                            *instance_dir.glob(f"{TEST_MARK_STARTING_GLOB}_*"),
                        ):
                            os.remove(sf)

                        dead_clusters = list(
                            self.cm.lock_dir.glob(f"{CLUSTER_DIR_TEMPLATE}*/{CLUSTER_DEAD_FILE}")
                        )
                        if len(dead_clusters) == self.cm.num_of_instances:
                            raise RuntimeError("All clusters are dead, cannot run.")
                        continue

                    # singleton test is running, so no other test can be started
                    if (instance_dir / TEST_SINGLETON_FILE).exists():
                        self.cm._log(f"c{instance_num}: singleton test in progress, cannot run")
                        sleep_delay = 5
                        continue

                    restart_in_progress = list(instance_dir.glob(f"{RESTART_IN_PROGRESS_GLOB}_*"))
                    # cluster restart planned, no new tests can start
                    if not restart_here and restart_in_progress:
                        # no log message here, it would be too many of them
                        sleep_delay = 5
                        continue

                    started_tests = list(instance_dir.glob(f"{TEST_RUNNING_GLOB}_*"))

                    # "marked tests" = group of tests marked with a specific mark.
                    # While these tests are running, no unmarked test can start.
                    marked_starting = list(instance_dir.glob(f"{TEST_MARK_STARTING_GLOB}_*"))
                    marked_running = list(instance_dir.glob(f"{TEST_CURR_MARK_GLOB}_*"))

                    if mark:
                        marked_running_my = (
                            instance_dir / f"{TEST_CURR_MARK_GLOB}_{mark}"
                        ).exists()
                        marked_starting_my = list(
                            instance_dir.glob(f"{TEST_MARK_STARTING_GLOB}_{mark}_*")
                        )

                        marked_running_my_anywhere = list(
                            self.cm.lock_dir.glob(
                                f"{CLUSTER_DIR_TEMPLATE}*/{TEST_CURR_MARK_GLOB}_{mark}"
                            )
                        )
                        # check if tests with my mark are running on some other cluster instance
                        if not marked_running_my and marked_running_my_anywhere:
                            self.cm._log(
                                f"c{instance_num}: tests marked with my mark '{mark}' "
                                "already running on other cluster instance, cannot run"
                            )
                            continue

                        marked_starting_my_anywhere = list(
                            self.cm.lock_dir.glob(
                                f"{CLUSTER_DIR_TEMPLATE}*/{TEST_MARK_STARTING_GLOB}_{mark}_*"
                            )
                        )
                        # check if tests with my mark are starting on some other cluster instance
                        if not marked_starting_my and marked_starting_my_anywhere:
                            self.cm._log(
                                f"c{instance_num}: tests marked with my mark '{mark}' starting "
                                "on other cluster instance, cannot run"
                            )
                            continue

                        # check if this test has the same mark as currently running marked tests
                        if marked_running_my or marked_starting_my:
                            # lock to this cluster instance
                            selected_instance = instance_num
                        elif marked_running or marked_starting:
                            self.cm._log(
                                f"c{instance_num}: tests marked with other mark starting "
                                f"or running, I have different mark '{mark}'"
                            )
                            continue

                        # check if needs to wait until marked tests can run
                        if marked_starting_my and started_tests:
                            self.cm._log(
                                f"c{instance_num}: unmarked tests running, wants to start '{mark}'"
                            )
                            sleep_delay = 2
                            continue

                    # no unmarked test can run while marked tests are starting or running
                    elif marked_running or marked_starting:
                        self.cm._log(
                            f"c{instance_num}: marked tests starting or running, "
                            f"I don't have mark"
                        )
                        sleep_delay = 5
                        continue

                    # is this the first marked test that wants to run?
                    initial_marked_test = bool(mark and not marked_running)

                    # indicate that it is planned to start marked tests as soon as
                    # all currently running tests are finished or the cluster is restarted
                    if initial_marked_test:
                        # lock to this cluster instance
                        selected_instance = instance_num
                        mark_starting_file = (
                            instance_dir / f"{TEST_MARK_STARTING_GLOB}_{mark}_{self.cm.worker_id}"
                        )
                        if not mark_starting_file.exists():
                            open(
                                mark_starting_file,
                                "a",
                            ).close()
                        if started_tests:
                            self.cm._log(
                                f"c{instance_num}: unmarked tests running, wants to start '{mark}'"
                            )
                            sleep_delay = 3
                            continue

                    # get marked tests status
                    marked_tests_status = self._get_marked_tests_status(
                        cache=marked_tests_cache, instance_num=instance_num
                    )

                    # marked tests are already running
                    if marked_running:
                        active_mark_file = marked_running[0].name

                        # update marked tests status
                        self._update_marked_tests(
                            marked_tests_status=marked_tests_status,
                            active_mark_name=active_mark_file,
                            started_tests=started_tests,
                            instance_num=instance_num,
                        )

                        self.cm._log(
                            f"c{instance_num}: in marked tests branch, "
                            f"I have required mark '{mark}'"
                        )

                    # reset counter of cycles with no marked test running
                    marked_tests_status.no_marked_tests_iter = 0

                    # this test is a singleton - no other test can run while this one is running
                    if singleton and started_tests:
                        self.cm._log(f"c{instance_num}: tests are running, cannot start singleton")
                        sleep_delay = 5
                        continue

                    # this test wants to lock some resources, check if these are not
                    # locked or in use
                    if lock_resources:
                        res_usable = self._are_resources_usable(
                            resources=lock_resources,
                            instance_dir=instance_dir,
                            instance_num=instance_num,
                        )
                        if not res_usable:
                            sleep_delay = 5
                            continue

                    # filter out `lock_resources` from the list of `use_resources`
                    if use_resources and lock_resources:
                        use_resources = list(set(use_resources) - set(lock_resources))

                    # this test wants to use some resources, check if these are not locked
                    if use_resources:
                        res_locked = self._are_resources_locked(
                            resources=use_resources,
                            instance_dir=instance_dir,
                            instance_num=instance_num,
                        )
                        if res_locked:
                            sleep_delay = 5
                            continue

                    # indicate that the cluster will be restarted
                    new_cmd_restart = bool(start_cmd and (initial_marked_test or singleton))
                    if not restart_here and (
                        new_cmd_restart or self._is_restart_needed(instance_num)
                    ):
                        if started_tests:
                            self.cm._log(f"c{instance_num}: tests are running, cannot restart")
                            continue

                        # Cluster restart will be performed by this worker.
                        # By setting `restart_here`, we make sure this worker continue on
                        # this cluster instance after restart. It is important because
                        # the `start_cmd` used for starting the cluster might be speciffic
                        # to the test.
                        restart_here = True
                        self.cm._log(f"c{instance_num}: setting to restart cluster")
                        selected_instance = instance_num
                        restart_in_progress_file = (
                            instance_dir / f"{RESTART_IN_PROGRESS_GLOB}_{self.cm.worker_id}"
                        )
                        if not restart_in_progress_file.exists():
                            open(restart_in_progress_file, "a").close()

                    # we've found suitable cluster instance
                    selected_instance = instance_num
                    self.cm._cluster_instance_num = instance_num
                    cluster_nodes.set_cluster_env(instance_num)

                    if restart_here:
                        if restart_ready:
                            # The cluster was already restarted if we are here and
                            # `restart_ready` is still True.
                            restart_ready = False

                            # Remove status files that are no longer valid after restart.
                            for f in instance_dir.glob(f"{RESTART_IN_PROGRESS_GLOB}_*"):
                                os.remove(f)
                            for f in instance_dir.glob(f"{RESTART_NEEDED_GLOB}_*"):
                                os.remove(f)
                        else:
                            self.cm._log(f"c{instance_num}: calling restart")
                            # the actual `_restart` function will be called outside
                            # of global lock
                            restart_ready = True
                            continue

                    # from this point on, all conditions needed to start the test are met

                    # this test is a singleton
                    if singleton:
                        self.cm._log(f"c{instance_num}: starting singleton")
                        open(self.cm.instance_dir / TEST_SINGLETON_FILE, "a").close()

                    # this test is a first marked test
                    if initial_marked_test:
                        self.cm._log(f"c{instance_num}: starting '{mark}' tests")
                        open(self.cm.instance_dir / f"{TEST_CURR_MARK_GLOB}_{mark}", "a").close()
                        for sf in marked_starting:
                            os.remove(sf)

                    # create status file for each in-use resource
                    _ = [
                        open(
                            self.cm.instance_dir
                            / f"{RESOURCE_IN_USE_GLOB}_{r}_{self.cm.worker_id}",
                            "a",
                        ).close()
                        for r in use_resources
                    ]

                    # create status file for each locked resource
                    _ = [
                        open(
                            self.cm.instance_dir
                            / f"{RESOURCE_LOCKED_GLOB}_{r}_{self.cm.worker_id}",
                            "a",
                        ).close()
                        for r in lock_resources
                    ]

                    # cleanup = cluster restart after test (group of tests) is finished
                    if cleanup:
                        # cleanup after group of test that are marked with a marker
                        if mark:
                            self.cm._log(f"c{instance_num}: cleanup and mark")
                            open(
                                self.cm.instance_dir
                                / f"{RESTART_AFTER_MARK_GLOB}_{self.cm.worker_id}",
                                "a",
                            ).close()
                        # cleanup after single test (e.g. singleton)
                        else:
                            self.cm._log(f"c{instance_num}: cleanup and not mark")
                            open(
                                self.cm.instance_dir / f"{RESTART_NEEDED_GLOB}_{self.cm.worker_id}",
                                "a",
                            ).close()

                    break
                else:
                    # if the test cannot start on any instance, return to top-level loop
                    continue

                test_running_file = (
                    self.cm.instance_dir / f"{TEST_RUNNING_GLOB}_{self.cm.worker_id}"
                )
                self.cm._log(f"c{self.cm.cluster_instance_num}: creating {test_running_file}")
                open(test_running_file, "a").close()

                # check if it is necessary to reload data
                state_dir = cluster_nodes.get_cluster_env().state_dir
                self._reload_cluster_obj(state_dir=state_dir)

                cluster_obj = self.cm.cache.cluster_obj
                if not cluster_obj:
                    cluster_obj = cluster_nodes.get_cluster_type().get_cluster_obj()

                # `cluster_obj` is ready, we can start the test
                break

        return cluster_obj
