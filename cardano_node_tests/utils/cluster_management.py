"""Functionality for parallel execution of tests on multiple cluster instances."""
# pylint: disable=abstract-class-instantiated
import contextlib
import dataclasses
import datetime
import hashlib
import inspect
import logging
import os
import random
import shutil
import time
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Iterable
from typing import Iterator
from typing import Optional

import pytest
from _pytest.config import Config
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import artifacts
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import cluster_scripts
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)

if configuration.IS_XDIST:
    xdist_sleep = time.sleep
else:

    def xdist_sleep(secs: float) -> None:
        """No need to sleep if tests are running on a single pytest worker."""
        # pylint: disable=unused-argument,unnecessary-pass
        pass


class Resources:
    """Resources that can be used for `lock_resources` or `use_resources`."""

    # Whole cluster instance - this resource is used by every test.
    # It can be locked, so only single test will run.
    CLUSTER = "cluster"
    POOL1 = "node-pool1"
    POOL2 = "node-pool2"
    POOL3 = "node-pool3"
    RESERVES = "reserves"
    TREASURY = "treasury"


CLUSTER_LOCK = ".cluster.lock"
LOG_LOCK = ".manager_log.lock"

RESOURCE_LOCKED_GLOB = ".resource_locked"
RESOURCE_IN_USE_GLOB = ".resource_in_use"
RESTART_NEEDED_GLOB = ".needs_restart"
RESTART_IN_PROGRESS_GLOB = ".restart_in_progress"
RESTART_AFTER_MARK_GLOB = ".restart_after_mark"
TEST_RUNNING_GLOB = ".test_running"
TEST_CURR_MARK_GLOB = ".curr_test_mark"
TEST_MARK_STARTING_GLOB = ".starting_marked_tests"

CLUSTER_DIR_TEMPLATE = "cluster"
CLUSTER_RUNNING_FILE = ".cluster_running"
CLUSTER_STOPPED_FILE = ".cluster_stopped"
CLUSTER_DEAD_FILE = ".cluster_dead"

if configuration.CLUSTERS_COUNT > 1 and configuration.DEV_CLUSTER_RUNNING:
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
        pid = line.split()[-1].split("/")[0]
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
    no_marked_tests_iter: int = 0


@dataclasses.dataclass
class ClusterGetStatus:
    """Intermediate status while trying to `get` suitable cluster instance."""

    mark: str
    lock_resources: Iterable[str]
    use_resources: Iterable[str]
    cleanup: bool
    start_cmd: str
    current_test: str
    selected_instance: int = -1
    instance_num: int = -1
    sleep_delay: int = 1
    restart_here: bool = False
    restart_ready: bool = False
    first_iteration: bool = True
    instance_dir: Path = Path("/nonexistent")
    # status files
    started_tests_sfiles: Iterable[Path] = ()
    marked_starting_sfiles: Iterable[Path] = ()
    marked_running_sfiles: Iterable[Path] = ()


class ClusterManager:
    """Set of management methods for cluster instances."""

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
        self.pytest_tmp_dir = temptools.get_pytest_root_tmp(tmp_path_factory)

        self.is_xdist = configuration.IS_XDIST
        if self.is_xdist:
            self.range_num = 5
            self.num_of_instances = configuration.CLUSTERS_COUNT
        else:
            self.range_num = 1
            self.num_of_instances = 1

        self.cluster_lock = f"{self.pytest_tmp_dir}/{CLUSTER_LOCK}"
        self.log_lock = f"{self.pytest_tmp_dir}/{LOG_LOCK}"

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
        instance_dir = self.pytest_tmp_dir / f"{CLUSTER_DIR_TEMPLATE}{self.cluster_instance_num}"
        return instance_dir

    @property
    def ports(self) -> cluster_scripts.InstancePorts:
        """Return port mappings for current cluster instance."""
        return cluster_nodes.get_cluster_type().cluster_scripts.get_instance_ports(
            self.cluster_instance_num
        )

    def _log(self, msg: str) -> None:
        """Log message."""
        if not configuration.SCHEDULING_LOG:
            return

        with locking.FileLockIfXdist(self.log_lock), open(
            configuration.SCHEDULING_LOG, "a", encoding="utf-8"
        ) as logfile:
            logfile.write(f"{datetime.datetime.now()} on {self.worker_id}: {msg}\n")

    def _create_startup_files_dir(self, instance_num: int) -> Path:
        instance_dir = self.pytest_tmp_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
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

            artifacts.save_cli_coverage(cluster_obj=cluster_obj, pytest_config=self.pytest_config)

    def stop_all_clusters(self) -> None:
        """Stop all cluster instances."""
        self._log("called `stop_all_clusters`")

        # don't stop cluster if it was started outside of test framework
        if configuration.DEV_CLUSTER_RUNNING:
            LOGGER.warning("Ignoring request to stop clusters as 'DEV_CLUSTER_RUNNING' is set.")
            return

        work_dir = cluster_nodes.get_cluster_env().work_dir

        for instance_num in range(self.num_of_instances):
            instance_dir = self.pytest_tmp_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
            if (
                not (instance_dir / CLUSTER_RUNNING_FILE).exists()
                or (instance_dir / CLUSTER_STOPPED_FILE).exists()
            ):
                self._log(f"c{instance_num}: cluster instance not running")
                continue

            state_dir = work_dir / f"{cluster_nodes.STATE_CLUSTER}{instance_num}"

            stop_script = state_dir / cluster_scripts.STOP_SCRIPT
            if not stop_script.exists():
                self._log(f"c{instance_num}: stop script doesn't exist!")
                continue

            self._log(f"c{instance_num}: stopping cluster instance with `{stop_script}`")
            try:
                helpers.run_command(str(stop_script))
            except Exception as err:
                self._log(f"c{instance_num}: failed to stop cluster:\n{err}")

            artifacts.save_start_script_coverage(
                log_file=state_dir / CLUSTER_START_CMDS_LOG,
                pytest_config=self.pytest_config,
            )
            artifacts.save_cluster_artifacts(save_dir=self.pytest_tmp_dir, state_dir=state_dir)

            shutil.rmtree(state_dir, ignore_errors=True)

            helpers.touch(instance_dir / CLUSTER_STOPPED_FILE)
            self._log(f"c{instance_num}: stopped cluster instance")

    def set_needs_restart(self) -> None:
        """Indicate that the cluster instance needs restart."""
        with locking.FileLockIfXdist(self.cluster_lock):
            self._log(f"c{self.cluster_instance_num}: called `set_needs_restart`")
            helpers.touch(self.instance_dir / f"{RESTART_NEEDED_GLOB}_{self.worker_id}")

    @contextlib.contextmanager
    def restart_on_failure(self) -> Iterator[None]:
        """Indicate that the cluster instance needs restart if command failed - context manager."""
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
        """Perform actions after a test is finished."""
        if self._cluster_instance_num == -1:
            return

        self._log(f"c{self._cluster_instance_num}: called `on_test_stop`")

        # search for errors in cluster logfiles
        errors = logfiles.search_cluster_artifacts()

        with locking.FileLockIfXdist(self.cluster_lock):
            # There's only one test running on a worker at a time. Deleting the coresponding rules
            # file right after a test is finished is therefore safe. The effect is that the rules
            # apply only from the time they were added (by `logfiles.add_ignore_rule`) until the end
            # of the test.
            # However sometimes we don't want to remove the rules file. Imagine situation when test
            # failed and cluster instance needs to be restarted. The failed test already finished,
            # but other tests are still running and need to finish first before restart can happen.
            # If the ignored error continues to get printed into log file, tests that are still
            # running on the cluster instance would report that error. Therefore if the cluster
            # instance is scheduled for restart, don't delete the rules file.
            if not list(self.instance_dir.glob(f"{RESTART_NEEDED_GLOB}_*")):
                logfiles.clean_ignore_rules(ignore_file_id=self.worker_id)

            # remove resource locking files created by the worker
            resource_locking_files = list(
                self.instance_dir.glob(f"{RESOURCE_LOCKED_GLOB}_*_{self.worker_id}")
            )
            for f in resource_locking_files:
                f.unlink()

            # remove "resource in use" files created by the worker
            resource_in_use_files = list(
                self.instance_dir.glob(f"{RESOURCE_IN_USE_GLOB}_*_{self.worker_id}")
            )
            for f in resource_in_use_files:
                f.unlink()

            # remove file that indicates that a test is running on the worker
            (self.instance_dir / f"{TEST_RUNNING_GLOB}_{self.worker_id}").unlink(missing_ok=True)

        if errors:
            logfiles.report_artifacts_errors(errors)

    def get(
        self,
        mark: str = "",
        lock_resources: Iterable[str] = (),
        use_resources: Iterable[str] = (),
        cleanup: bool = False,
        start_cmd: str = "",
    ) -> clusterlib.ClusterLib:
        """Wrap a call to `_ClusterGetter.get`."""
        return _ClusterGetter(self).get(
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

    def _restart(self, start_cmd: str = "", stop_cmd: str = "") -> bool:  # noqa: C901
        """Restart cluster.

        Not called under global lock!
        """
        # pylint: disable=too-many-branches
        cluster_running_file = self.cm.instance_dir / CLUSTER_RUNNING_FILE

        # don't restart cluster if it was started outside of test framework
        if configuration.DEV_CLUSTER_RUNNING:
            self.cm._log(
                f"c{self.cm.cluster_instance_num}: ignoring restart, dev cluster is running"
            )
            if cluster_running_file.exists():
                LOGGER.warning(
                    "Ignoring requested cluster restart as 'DEV_CLUSTER_RUNNING' is set."
                )
            else:
                helpers.touch(cluster_running_file)
            return True

        # fail if cluster restart is forbidden and it was already started
        if configuration.FORBID_RESTART and cluster_running_file.exists():
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
                LOGGER.info(f"Stopping cluster with `{startup_files.stop_script}`.")
                helpers.run_command(str(startup_files.stop_script))
            except Exception as err:
                self.cm._log(f"c{self.cm.cluster_instance_num}: failed to stop cluster:\n{err}")

            # save artifacts only when produced during this test run
            if cluster_running_file.exists():
                artifacts.save_start_script_coverage(
                    log_file=state_dir / CLUSTER_START_CMDS_LOG,
                    pytest_config=self.cm.pytest_config,
                )
                artifacts.save_cluster_artifacts(
                    save_dir=self.cm.pytest_tmp_dir, state_dir=state_dir
                )

            shutil.rmtree(state_dir, ignore_errors=True)

            with contextlib.suppress(Exception):
                _kill_supervisor(self.cm.cluster_instance_num)

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
            if not configuration.IS_XDIST:
                pytest.exit(msg=f"Failed to start cluster, exception: {excp}", returncode=1)
            helpers.touch(self.cm.instance_dir / CLUSTER_DEAD_FILE)
            return False

        # Create temp dir for faucet addresses data.
        # Pytest's mktemp adds number to the end of the dir name, so keep the trailing '_'
        # as separator. Resulting dir name is e.g. 'addrs_data_ci3_0'.
        tmp_path = Path(
            self.cm.tmp_path_factory.mktemp(f"addrs_data_ci{self.cm.cluster_instance_num}_")
        )
        # setup faucet addresses
        cluster_nodes.setup_test_addrs(cluster_obj=cluster_obj, destination_dir=tmp_path)

        # create file that indicates that the cluster is running
        if not cluster_running_file.exists():
            helpers.touch(cluster_running_file)

        return True

    def _is_dev_cluster_ready(self) -> bool:
        """Check if development cluster instance is ready to be used."""
        work_dir = cluster_nodes.get_cluster_env().work_dir
        state_dir = work_dir / f"{cluster_nodes.STATE_CLUSTER}0"
        if (state_dir / cluster_nodes.ADDRS_DATA).exists():
            return True
        return False

    def _setup_dev_cluster(self) -> None:
        """Set up cluster instance that was already started outside of test framework."""
        work_dir = cluster_nodes.get_cluster_env().work_dir
        state_dir = work_dir / f"{cluster_nodes.STATE_CLUSTER}0"
        if (state_dir / cluster_nodes.ADDRS_DATA).exists():
            return

        self.cm._log("c0: setting up dev cluster")

        # Create "addrs_data" directly in the cluster state dir, so it can be reused
        # (in normal non-`DEV_CLUSTER_RUNNING` setup we want "addrs_data" stored among
        # tests artifacts, so it can be used during cleanup etc.).
        tmp_path = state_dir / "addrs_data"
        tmp_path.mkdir(exist_ok=True, parents=True)
        cluster_obj = cluster_nodes.get_cluster_type().get_cluster_obj()
        cluster_nodes.setup_test_addrs(cluster_obj=cluster_obj, destination_dir=tmp_path)

    def _is_healthy(self, instance_num: int) -> bool:
        """Check health of cluster services."""
        statuses = cluster_nodes.services_status(instance_num=instance_num)
        failed_services = [s.name for s in statuses if s.status == "FATAL"]
        not_running_services = [(s.name, s.status) for s in statuses if s.status != "RUNNING"]
        if failed_services:
            self.cm._log(f"c{instance_num}: found failed services {failed_services}")
        elif not_running_services:
            self.cm._log(f"c{instance_num}: found not running services {not_running_services}")
        return not failed_services

    def _is_restart_needed(self, instance_num: int) -> bool:
        """Check if it is necessary to restart cluster."""
        instance_dir = self.cm.pytest_tmp_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
        # if cluster instance is not started yet
        if not (instance_dir / CLUSTER_RUNNING_FILE).exists():
            return True
        # if it was indicated that the cluster instance needs to be restarted
        if list(instance_dir.glob(f"{RESTART_NEEDED_GLOB}_*")):
            return True
        # if a service failed on cluster instance
        if not self._is_healthy(instance_num):
            return True
        return False

    def _on_marked_test_stop(self, instance_num: int) -> None:
        """Perform actions after marked tests are finished."""
        self.cm._log(f"c{instance_num}: in `_on_marked_test_stop`")
        instance_dir = self.cm.pytest_tmp_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"

        # set cluster instance to be restarted if needed
        restart_after_mark_files = list(instance_dir.glob(f"{RESTART_AFTER_MARK_GLOB}_*"))
        if restart_after_mark_files:
            for f in restart_after_mark_files:
                f.unlink()
            self.cm._log(
                f"c{instance_num}: in `_on_marked_test_stop`, creating 'restart needed' file"
            )
            helpers.touch(instance_dir / f"{RESTART_NEEDED_GLOB}_{self.cm.worker_id}")

        # remove file that indicates that tests with the mark are running
        marked_running_sfiles = list(instance_dir.glob(f"{TEST_CURR_MARK_GLOB}_*"))
        if marked_running_sfiles:
            marked_running_sfiles[0].unlink()

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
        marked_tests_cache: Dict[int, MarkedTestsStatus],
        cget_status: ClusterGetStatus,
    ) -> None:
        """Update status about running of marked test.

        When marked test is finished, we can't clear the mark right away. There might be a test
        with the same mark in the queue and it will be scheduled in a short while. We would need
        to repeat all the expensive setup if we already cleared the mark. Therefore we need to
        keeps track of marked tests and clear the mark and cluster instance only when no marked
        test was running for some time.
        """
        if not cget_status.marked_running_sfiles:
            return

        # get marked tests status
        marked_tests_status = self._get_marked_tests_status(
            cache=marked_tests_cache, instance_num=cget_status.instance_num
        )

        # update marked tests status
        in_progress = bool(cget_status.started_tests_sfiles or cget_status.marked_starting_sfiles)
        instance_num = cget_status.instance_num

        if in_progress:
            # test with mark is currently running or starting
            marked_tests_status.no_marked_tests_iter = 0
        else:
            # mark is set and no test is currently running, i.e. we are waiting for next marked test
            marked_tests_status.no_marked_tests_iter += 1

        # clean the stale status file if we are waiting too long for the next marked test
        if marked_tests_status.no_marked_tests_iter >= 20:
            self.cm._log(
                f"c{instance_num}: no marked tests running for a while, "
                "cleaning the mark status file"
            )
            self._on_marked_test_stop(instance_num)

    def _are_resources_usable(
        self, resources: Iterable[str], instance_dir: Path, instance_num: int
    ) -> bool:
        """Check if resources are locked or in use."""
        for res in resources:
            res_locked = list(instance_dir.glob(f"{RESOURCE_LOCKED_GLOB}_{res}_*"))
            if res_locked:
                self.cm._log(f"c{instance_num}: resource '{res}' locked, cannot start")
                break
            res_used = list(instance_dir.glob(f"{RESOURCE_IN_USE_GLOB}_{res}_*"))
            if res_used:
                self.cm._log(f"c{instance_num}: resource '{res}' in use, cannot lock and start")
                break
        else:
            self.cm._log(
                f"c{instance_num}: none of the resources in {resources} "
                "locked or in use, can start and lock"
            )
            return True
        return False

    def _are_resources_locked(
        self, resources: Iterable[str], instance_dir: Path, instance_num: int
    ) -> bool:
        """Check if resources are locked."""
        res_locked = []
        for res in resources:
            res_locked = list(instance_dir.glob(f"{RESOURCE_LOCKED_GLOB}_{res}_*"))
            if res_locked:
                self.cm._log(f"c{instance_num}: resource '{res}' locked, cannot start")
                break

        if not res_locked:
            self.cm._log(f"c{instance_num}: none of the resources in {resources} locked, can start")
        return bool(res_locked)

    def _are_resources_available(self, cget_status: ClusterGetStatus) -> bool:
        """Check if all required "use" and "lock" resources are available."""
        if cget_status.lock_resources:
            res_usable = self._are_resources_usable(
                resources=cget_status.lock_resources,
                instance_dir=cget_status.instance_dir,
                instance_num=cget_status.instance_num,
            )
            if not res_usable:
                return False

        # this test wants to use some resources, check if these are not locked
        if cget_status.use_resources:
            res_locked = self._are_resources_locked(
                resources=cget_status.use_resources,
                instance_dir=cget_status.instance_dir,
                instance_num=cget_status.instance_num,
            )
            if res_locked:
                return False

        return True

    def _save_cli_coverage(self) -> None:
        """Save CLI coverage info collected by this `cluster_obj` instance."""
        self.cm._log("called `_save_cli_coverage`")
        cluster_obj = self.cm.cache.cluster_obj
        if not cluster_obj:
            return

        artifacts.save_cli_coverage(cluster_obj=cluster_obj, pytest_config=self.cm.pytest_config)

    def _reload_cluster_obj(self, state_dir: Path) -> None:
        """Reload cluster data if necessary."""
        addrs_data_checksum = helpers.checksum(state_dir / cluster_nodes.ADDRS_DATA)
        # the checksum will not match when cluster was restarted
        if addrs_data_checksum == self.cm.cache.last_checksum:
            return

        # save CLI coverage collected by the old `cluster_obj` instance
        self._save_cli_coverage()
        # replace the old `cluster_obj` instance and reload data
        self.cm.cache.cluster_obj = cluster_nodes.get_cluster_type().get_cluster_obj()
        self.cm.cache.test_data = {}
        self.cm.cache.addrs_data = cluster_nodes.load_addrs_data()
        self.cm.cache.last_checksum = addrs_data_checksum

    def _is_already_running(self, cget_status: ClusterGetStatus) -> bool:
        """Check if the test is already setup and running."""
        test_on_worker = list(
            self.cm.pytest_tmp_dir.glob(
                f"{CLUSTER_DIR_TEMPLATE}*/{TEST_RUNNING_GLOB}_{self.cm.worker_id}"
            )
        )

        # test is already running, nothing to set up
        if (
            cget_status.first_iteration
            and test_on_worker
            and self.cm._cluster_instance_num != -1
            and self.cm.cache.cluster_obj
        ):
            self.cm._log(f"{test_on_worker[0]} already exists")
            return True

        return False

    def _restarted_by_other_worker(self, cget_status: ClusterGetStatus) -> bool:
        """Check if the cluster is currently being restarted by worker other than this one."""
        if cget_status.restart_here:
            return False

        restart_in_progress = list(cget_status.instance_dir.glob(f"{RESTART_IN_PROGRESS_GLOB}_*"))
        if restart_in_progress:
            # no log message here, it would be too many of them
            return True

        return False

    def _marked_select_instance(self, cget_status: ClusterGetStatus) -> bool:
        """Select this cluster instance for running marked tests if possible."""
        marked_running_my_here = list(
            cget_status.instance_dir.glob(f"{TEST_CURR_MARK_GLOB}_{cget_status.mark}_*")
        )
        marked_running_my_anywhere = list(
            self.cm.pytest_tmp_dir.glob(
                f"{CLUSTER_DIR_TEMPLATE}*/{TEST_CURR_MARK_GLOB}_{cget_status.mark}_*"
            )
        )
        if not marked_running_my_here and marked_running_my_anywhere:
            self.cm._log(
                f"c{cget_status.instance_num}: tests marked with my mark '{cget_status.mark}' "
                "already running on other cluster instance, cannot run"
            )
            return False

        marked_starting_my_here = list(
            cget_status.instance_dir.glob(f"{TEST_MARK_STARTING_GLOB}_{cget_status.mark}_*")
        )
        marked_starting_my_anywhere = list(
            self.cm.pytest_tmp_dir.glob(
                f"{CLUSTER_DIR_TEMPLATE}*/{TEST_MARK_STARTING_GLOB}_{cget_status.mark}_*"
            )
        )
        if not marked_starting_my_here and marked_starting_my_anywhere:
            self.cm._log(
                f"c{cget_status.instance_num}: tests marked with my mark '{cget_status.mark}' "
                "starting on other cluster instance, cannot run"
            )
            return False

        if marked_running_my_here or marked_starting_my_here:
            cget_status.selected_instance = cget_status.instance_num
            self.cm._log(
                f"c{cget_status.instance_num}: locking to this cluster instance, "
                f"it has my mark '{cget_status.mark}'"
            )
        elif cget_status.marked_running_sfiles or cget_status.marked_starting_sfiles:
            self.cm._log(
                f"c{cget_status.instance_num}: tests marked with other mark starting "
                f"or running, I have different mark '{cget_status.mark}'"
            )
            return False
        else:
            # No marked tests are running yet. Indicate that it is planned to start marked tests as
            # soon as possible (when all currently running tests are finished or the cluster is
            # restarted).
            cget_status.selected_instance = cget_status.instance_num
            mark_starting_file = (
                cget_status.instance_dir
                / f"{TEST_MARK_STARTING_GLOB}_{cget_status.mark}_{self.cm.worker_id}"
            )
            if not mark_starting_file.exists():
                self.cm._log(f"c{cget_status.instance_num}: initialized mark '{cget_status.mark}'")
                helpers.touch(mark_starting_file)

        return True

    def _cleanup_dead_clusters(self, cget_status: ClusterGetStatus) -> None:
        """Cleanup if the selected cluster instance failed to start."""
        # move on to other cluster instance
        cget_status.selected_instance = -1
        cget_status.restart_here = False
        cget_status.restart_ready = False

        # remove status files that are checked by other workers
        for sf in (
            *cget_status.instance_dir.glob(f"{TEST_CURR_MARK_GLOB}_*"),
            *cget_status.instance_dir.glob(f"{TEST_MARK_STARTING_GLOB}_*"),
        ):
            sf.unlink()

        dead_clusters = list(
            self.cm.pytest_tmp_dir.glob(f"{CLUSTER_DIR_TEMPLATE}*/{CLUSTER_DEAD_FILE}")
        )
        if len(dead_clusters) == self.cm.num_of_instances:
            raise RuntimeError("All clusters are dead, cannot run.")

    def _init_restart(self, cget_status: ClusterGetStatus) -> bool:
        """Initialize restart on this cluster instance."""
        # restart already initialized
        if cget_status.restart_here:
            return True

        # restart is needed when custom start command was specified and the test is marked test or
        # singleton
        initial_marked_test = bool(cget_status.mark and not cget_status.marked_running_sfiles)
        singleton_test = Resources.CLUSTER in cget_status.lock_resources
        new_cmd_restart = bool(cget_status.start_cmd and (initial_marked_test or singleton_test))
        will_restart = new_cmd_restart or self._is_restart_needed(cget_status.instance_num)
        if not will_restart:
            return True

        # if tests are running on the instance, we cannot restart, therefore we cannot continue
        if cget_status.started_tests_sfiles:
            self.cm._log(f"c{cget_status.instance_num}: tests are running, cannot restart")
            return False

        self.cm._log(f"c{cget_status.instance_num}: setting 'restart in progress'")

        # Cluster restart will be performed by this worker.
        # By setting `restart_here`, we make sure this worker continue on this cluster instance
        # after restart is finished. It is important because the `start_cmd` used for starting the
        # cluster instance might be specific to the test.
        cget_status.restart_here = True
        cget_status.selected_instance = cget_status.instance_num

        restart_in_progress_file = (
            cget_status.instance_dir / f"{RESTART_IN_PROGRESS_GLOB}_{self.cm.worker_id}"
        )
        if not restart_in_progress_file.exists():
            helpers.touch(restart_in_progress_file)

        return True

    def _finish_restart(self, cget_status: ClusterGetStatus) -> bool:
        """On first call, setup cluster instance for restart. On second call, perform cleanup."""
        if not cget_status.restart_here:
            return True

        if cget_status.restart_ready:
            # The cluster was already restarted if we are here and `restart_ready` is still True.
            # If that's the case, do cleanup.
            cget_status.restart_ready = False
            cget_status.restart_here = False

            # remove status files that are no longer valid after restart
            for f in cget_status.instance_dir.glob(f"{RESTART_IN_PROGRESS_GLOB}_*"):
                f.unlink()
            for f in cget_status.instance_dir.glob(f"{RESTART_NEEDED_GLOB}_*"):
                f.unlink()
            return True

        # NOTE: when `_restart` is called, the env variables needed for cluster start scripts need
        # to be already set (e.g. CARDANO_NODE_SOCKET_PATH)
        self.cm._log(f"c{cget_status.instance_num}: ready to restart cluster")
        # the actual `_restart` function will be called outside of global lock so other workers
        # don't need to wait
        cget_status.restart_ready = True
        return False

    def _create_test_status_files(self, cget_status: ClusterGetStatus) -> None:
        """Create status files for test that is about to start on this cluster instance."""
        # this test is a first marked test
        if cget_status.mark and not cget_status.marked_running_sfiles:
            self.cm._log(f"c{cget_status.instance_num}: starting '{cget_status.mark}' tests")
            helpers.touch(
                self.cm.instance_dir
                / f"{TEST_CURR_MARK_GLOB}_{cget_status.mark}_{self.cm.worker_id}"
            )
            for sf in cget_status.marked_starting_sfiles:
                sf.unlink()

        # create status file for each in-use resource
        for r in cget_status.use_resources:
            helpers.touch(self.cm.instance_dir / f"{RESOURCE_IN_USE_GLOB}_{r}_{self.cm.worker_id}")

        # create status file for each locked resource
        for r in cget_status.lock_resources:
            helpers.touch(self.cm.instance_dir / f"{RESOURCE_LOCKED_GLOB}_{r}_{self.cm.worker_id}")

        # cleanup = cluster restart after test (group of tests) is finished
        if cget_status.cleanup:
            # cleanup after group of test that are marked with a marker
            if cget_status.mark:
                self.cm._log(f"c{cget_status.instance_num}: cleanup and mark")
                helpers.touch(
                    self.cm.instance_dir / f"{RESTART_AFTER_MARK_GLOB}_{self.cm.worker_id}"
                )
            # cleanup after single test (e.g. singleton)
            else:
                self.cm._log(f"c{cget_status.instance_num}: cleanup and not mark")
                helpers.touch(self.cm.instance_dir / f"{RESTART_NEEDED_GLOB}_{self.cm.worker_id}")

        self.cm._log(f"c{self.cm.cluster_instance_num}: creating 'test running' status file")
        test_running_file = self.cm.instance_dir / f"{TEST_RUNNING_GLOB}_{self.cm.worker_id}"
        helpers.touch(test_running_file)

    def get(  # noqa: C901
        self,
        mark: str = "",
        lock_resources: Iterable[str] = (),
        use_resources: Iterable[str] = (),
        cleanup: bool = False,
        start_cmd: str = "",
    ) -> clusterlib.ClusterLib:
        """Return the `clusterlib.ClusterLib` instance once we can start the test.

        It checks current conditions and waits if the conditions don't allow to start the test
        right away.
        """
        # pylint: disable=too-many-statements,too-many-branches
        assert not isinstance(lock_resources, str), "`lock_resources` must be sequence of strings"
        assert not isinstance(use_resources, str), "`use_resources` must be sequence of strings"

        if configuration.DEV_CLUSTER_RUNNING:
            if start_cmd:
                LOGGER.warning(
                    f"Ignoring the '{start_cmd}' cluster start command as "
                    "'DEV_CLUSTER_RUNNING' is set."
                )
            # check if the development cluster instance is ready by now so we don't need to obtain
            # cluster lock when it is not necessary
            if not self._is_dev_cluster_ready():
                with locking.FileLockIfXdist(self.cm.cluster_lock):
                    self._setup_dev_cluster()

        if configuration.FORBID_RESTART and start_cmd:
            raise RuntimeError("Cannot use custom start command when 'FORBID_RESTART' is set.")

        if start_cmd:
            if not (mark or (Resources.CLUSTER in lock_resources)):
                raise RuntimeError(
                    "Custom start command can be used only together with singleton or `mark`."
                )
            # always clean after test(s) that started cluster with custom configuration
            cleanup = True

        # Add `Resources.CLUSTER` to `use_resources`. Filter out `lock_resources` from the
        # list of `use_resources`.
        use_resources = list(set(use_resources).union({Resources.CLUSTER}) - set(lock_resources))

        cget_status = ClusterGetStatus(
            mark=mark,
            lock_resources=lock_resources,
            use_resources=use_resources,
            cleanup=cleanup,
            start_cmd=start_cmd,
            current_test=os.environ.get("PYTEST_CURRENT_TEST") or "",
        )
        marked_tests_cache: Dict[int, MarkedTestsStatus] = {}

        self.cm._log(f"want to run test '{cget_status.current_test}'")

        # iterate until it is possible to start the test
        while True:
            if cget_status.restart_ready:
                self._restart(start_cmd=start_cmd)

            if not cget_status.first_iteration:
                xdist_sleep(random.uniform(0.6, 1.2) * cget_status.sleep_delay)

            # nothing time consuming can go under this lock as all other workers will need to wait
            with locking.FileLockIfXdist(self.cm.cluster_lock):
                if self._is_already_running(cget_status):
                    if not self.cm.cache.cluster_obj:
                        raise AssertionError("`cluster_obj` not available, that cannot happen")
                    return self.cm.cache.cluster_obj

                # needs to be set here, before the first `continue`
                cget_status.first_iteration = False
                self.cm._cluster_instance_num = -1

                # try all existing cluster instances
                for instance_num in range(self.cm.num_of_instances):
                    # there's only one cluster instance when `DEV_CLUSTER_RUNNING` is set
                    if configuration.DEV_CLUSTER_RUNNING and instance_num != 0:
                        continue

                    # if instance to run the test on was already decided, skip all other instances
                    # pylint: disable=consider-using-in
                    if (
                        cget_status.selected_instance != -1
                        and instance_num != cget_status.selected_instance
                    ):
                        continue

                    cget_status.instance_num = instance_num
                    cget_status.instance_dir = (
                        self.cm.pytest_tmp_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
                    )
                    cget_status.instance_dir.mkdir(exist_ok=True)

                    # cleanup cluster instance where attempt to start cluster failed repeatedly
                    if (cget_status.instance_dir / CLUSTER_DEAD_FILE).exists():
                        self._cleanup_dead_clusters(cget_status)
                        continue

                    # cluster restart planned or in progress, so no new tests can start
                    if self._restarted_by_other_worker(cget_status):
                        cget_status.sleep_delay = 5
                        continue

                    # are there tests already running on this cluster instance?
                    cget_status.started_tests_sfiles = list(
                        cget_status.instance_dir.glob(f"{TEST_RUNNING_GLOB}_*")
                    )

                    # "marked tests" = group of tests marked with a specific mark.
                    # While these tests are running, no unmarked test can start.
                    cget_status.marked_starting_sfiles = list(
                        cget_status.instance_dir.glob(f"{TEST_MARK_STARTING_GLOB}_*")
                    )
                    cget_status.marked_running_sfiles = list(
                        cget_status.instance_dir.glob(f"{TEST_CURR_MARK_GLOB}_*")
                    )

                    # if marked tests are already running, update their status
                    self._update_marked_tests(
                        marked_tests_cache=marked_tests_cache, cget_status=cget_status
                    )

                    # test has mark
                    if mark:
                        # select this instance for running marked tests if possible
                        if not self._marked_select_instance(cget_status):
                            cget_status.sleep_delay = 2
                            continue

                        # check if we need to wait until unmarked tests are finished
                        if (
                            not cget_status.marked_running_sfiles
                            and cget_status.started_tests_sfiles
                        ):
                            cget_status.sleep_delay = 10
                            continue

                        self.cm._log(
                            f"c{instance_num}: in marked tests branch, "
                            f"I have required mark '{mark}'"
                        )

                    # no unmarked test can run while marked tests are starting or running
                    elif cget_status.marked_running_sfiles or cget_status.marked_starting_sfiles:
                        self.cm._log(
                            f"c{instance_num}: marked tests starting or running, "
                            f"I don't have mark"
                        )
                        cget_status.sleep_delay = 2
                        continue

                    # check availability of the required resources
                    if not self._are_resources_available(cget_status):
                        cget_status.sleep_delay = 5
                        continue

                    # if restart is needed, indicate that the cluster will be restarted
                    # (after all currently running tests are finished)
                    if not self._init_restart(cget_status):
                        continue

                    # we've found suitable cluster instance
                    cget_status.selected_instance = instance_num
                    self.cm._cluster_instance_num = instance_num
                    self.cm._log(f"c{instance_num}: can run test '{cget_status.current_test}'")
                    # set environment variables that are needed when restarting the cluster
                    # and running tests
                    cluster_nodes.set_cluster_env(instance_num)

                    # if needed, finish restart related actions
                    if not self._finish_restart(cget_status):
                        continue

                    # from this point on, all conditions needed to start the test are met
                    break
                else:
                    # if the test cannot start on any instance, return to top-level loop
                    continue

                self._create_test_status_files(cget_status)

                # Check if it is necessary to reload data. This still needs to happen under
                # global lock.
                state_dir = cluster_nodes.get_cluster_env().state_dir
                self._reload_cluster_obj(state_dir=state_dir)

                # cluster is ready, we can start the test
                break

        cluster_obj = self.cm.cache.cluster_obj
        if not cluster_obj:
            raise AssertionError("`cluster_obj` not available, that cannot happen")
        cluster_obj.cluster_id = instance_num
        cluster_obj._cluster_manager = self.cm  # type: ignore

        return cluster_obj
