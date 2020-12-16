"""Functionality for parallel execution of tests on DevOps cluster instances."""
import contextlib
import dataclasses
import datetime
import logging
import os
import random
import time
from pathlib import Path
from typing import Dict
from typing import Generator
from typing import Optional

import pytest
from _pytest.config import Config
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import cluster_instances
from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import devops_cluster
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils.types import UnpackableSequence

LOGGER = logging.getLogger(__name__)

CLUSTER_LOCK = ".cluster.lock"
RUN_LOG_FILE = ".parallel.log"
TEST_SINGLETON_FILE = ".test_singleton"
RESOURCE_LOCKED_GLOB = ".resource_locked"
RESOURCE_IN_USE_GLOB = ".resource_in_use"
RESTART_NEEDED_GLOB = ".needs_restart"
RESTART_IN_PROGRESS_GLOB = ".restart_in_progress"
RESTART_AFTER_MARK_GLOB = ".restart_after_mark"
TEST_RUNNING_GLOB = ".test_running"
TEST_CURR_MARK_GLOB = ".curr_test_mark"
TEST_MARK_STARTING_GLOB = ".starting_marked_tests"

WORKERS_COUNT = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", 1))
CLUSTERS_COUNT = WORKERS_COUNT if WORKERS_COUNT <= 9 else 9
CLUSTER_DIR_TEMPLATE = "cluster"
CLUSTER_RUNNING_FILE = ".cluster_running"
CLUSTER_STOPPED_FILE = ".cluster_stopped"

DEV_CLUSTER_RUNNING = bool(os.environ.get("DEV_CLUSTER_RUNNING"))

if helpers.IS_XDIST and DEV_CLUSTER_RUNNING:
    raise RuntimeError("Cannot run tests in parallel when 'DEV_CLUSTER_RUNNING' is set.")


def _kill_supervisor(instance_num: int) -> None:
    """Kill supervisor process."""
    port = f":{cluster_instances.get_instance_ports(instance_num).supervisor}"
    netstat = helpers.run_command("netstat -plnt").decode().splitlines()
    for line in netstat:
        if port not in line:
            continue
        line = line.replace("  ", " ").strip()
        pid = line.split(" ")[-1].split("/")[0]
        os.kill(int(pid), 15)
        return


@dataclasses.dataclass
class ClusterManagerCache:
    cluster_obj: Optional[clusterlib.ClusterLib] = None
    test_data: dict = dataclasses.field(default_factory=dict)
    addrs_data: dict = dataclasses.field(default_factory=dict)
    last_checksum: str = ""


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
        self.lock_log = self._init_log()

        self._cluster_instance = -1

    @property
    def cluster_instance(self) -> int:
        if self._cluster_instance == -1:
            raise RuntimeError("Cluster instance not set.")
        return self._cluster_instance

    @property
    def cache(self) -> ClusterManagerCache:
        _cache = self.get_cache()
        instance_cache = _cache.get(self.cluster_instance)
        if not instance_cache:
            instance_cache = ClusterManagerCache()
            _cache[self.cluster_instance] = instance_cache
        return instance_cache

    @property
    def instance_dir(self) -> Path:
        instance_dir = self.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{self.cluster_instance}"
        return instance_dir

    @property
    def ports(self) -> cluster_instances.InstancePorts:
        """Return port mappings for current cluster instance."""
        return cluster_instances.get_instance_ports(self.cluster_instance)

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
        """Log message - needs to be called while having lock."""
        if not self.lock_log.is_file():
            return
        with open(self.lock_log, "a") as logfile:
            logfile.write(f"{datetime.datetime.now()} on {self.worker_id}: {msg}\n")

    def _locked_log(self, msg: str) -> None:
        """Log message - will obtain lock first."""
        if not self.lock_log.is_file():
            return
        with helpers.FileLockIfXdist(self.cluster_lock):
            with open(self.lock_log, "a") as logfile:
                logfile.write(f"{datetime.datetime.now()} on {self.worker_id}: {msg}\n")

    def _create_startup_files_dir(self, instance_num: int) -> Path:
        instance_dir = self.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
        rand_str = clusterlib.get_rand_str(8)
        startup_files_dir = instance_dir / "startup_files" / rand_str
        startup_files_dir.mkdir(exist_ok=True, parents=True)
        return startup_files_dir

    def _restart_save_cluster_artifacts(self, clean: bool = False) -> None:
        """Save cluster artifacts (logs, certs, etc.) to pytest temp dir before cluster restart."""
        cluster_obj = self.cache.cluster_obj
        if not cluster_obj:
            return

        devops_cluster.save_cluster_artifacts(artifacts_dir=self.pytest_tmp_dir, clean=clean)

    def _restart(  # noqa: C901
        self, start_cmd: str = "", stop_cmd: str = ""
    ) -> clusterlib.ClusterLib:
        """Restart cluster."""
        # don't restart cluster if it was started outside of test framework
        if self.num_of_instances == 1 and DEV_CLUSTER_RUNNING:
            cluster_obj = self.cache.cluster_obj
            if not cluster_obj:
                cluster_obj = devops_cluster.get_cluster_obj()
            return cluster_obj

        # using `_locked_log` because restart is not called under global lock
        self._locked_log(
            f"c{self.cluster_instance}: called `_restart`, start_cmd='{start_cmd}', "
            f"stop_cmd='{stop_cmd}'"
        )

        startup_files = cluster_instances.prepare_files(
            destdir=self._create_startup_files_dir(self.cluster_instance),
            instance_num=self.cluster_instance,
            start_script=start_cmd,
            stop_script=stop_cmd,
        )

        self._locked_log(
            f"c{self.cluster_instance}: in `_restart`, new files "
            f"start_cmd='{startup_files.start_script}', "
            f"stop_cmd='{startup_files.stop_script}'"
        )

        excp: Optional[Exception]
        for i in range(2):
            excp = None
            if i > 0:
                self._locked_log(f"c{self.cluster_instance}: failed to start cluster, retrying")
                time.sleep(0.2)

            devops_cluster.stop_cluster(cmd=str(startup_files.stop_script))
            self._restart_save_cluster_artifacts(clean=True)
            try:
                _kill_supervisor(self.cluster_instance)
            except Exception:
                pass

            try:
                cluster_obj = devops_cluster.start_cluster(cmd=str(startup_files.start_script))
            except Exception as err:
                LOGGER.error(f"Failed to start cluster: {err}")
                excp = err
            else:
                break
        else:
            msg = "Failed to start cluster"
            if helpers.IS_XDIST:
                # just leave this instance unusable
                if excp is not None:
                    raise RuntimeError(msg) from excp
                raise RuntimeError(msg)
            pytest.exit(msg=f"{msg}, exception: {excp}", returncode=1)

        # setup faucet addresses
        tmp_path = Path(self.tmp_path_factory.mktemp("addrs_data"))
        devops_cluster.setup_test_addrs(cluster_obj, tmp_path)

        # create file that indicates that the cluster is running
        cluster_running_file = self.instance_dir / CLUSTER_RUNNING_FILE
        if not cluster_running_file.exists():
            open(cluster_running_file, "a").close()

        return cluster_obj

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

            clusterlib_utils.save_cli_coverage(
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

            startup_files = cluster_instances.prepare_files(
                destdir=self._create_startup_files_dir(instance_num),
                instance_num=instance_num,
            )
            cluster_instances.set_cardano_node_socket_path(instance_num)
            self._log(
                f"stopping cluster instance {instance_num} with `{startup_files.stop_script}`"
            )
            devops_cluster.stop_cluster(cmd=str(startup_files.stop_script))
            devops_cluster.save_cluster_artifacts(artifacts_dir=self.pytest_tmp_dir, clean=True)
            open(instance_dir / CLUSTER_STOPPED_FILE, "a").close()
            self._log(f"stopped cluster instance {instance_num}")

    def set_needs_restart(self) -> None:
        """Indicate that the cluster needs restart."""
        with helpers.FileLockIfXdist(self.cluster_lock):
            self._log(f"c{self.cluster_instance}: called `set_needs_restart`")
            open(self.instance_dir / f"{RESTART_NEEDED_GLOB}_{self.worker_id}", "a").close()

    @contextlib.contextmanager
    def restart_on_failure(self) -> Generator:
        """Indicate that the cluster needs restart if command failed - context manager."""
        try:
            yield
        except Exception:
            self.set_needs_restart()
            raise

    def on_test_stop(self) -> None:
        """Perform actions after the test finished."""
        if self._cluster_instance == -1:
            return

        with helpers.FileLockIfXdist(self.cluster_lock):
            self._log(f"c{self.cluster_instance}: called `on_test_stop`")

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

    #
    # methods below are unsafe to use outside of `get`
    #

    def _is_restart_needed(self, instance_num: int) -> bool:
        """Check if it is necessary to restart cluster."""
        instance_dir = self.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
        if not (instance_dir / CLUSTER_RUNNING_FILE).exists():
            return True
        if list(instance_dir.glob(f"{RESTART_NEEDED_GLOB}_*")):
            return True
        return False

    def _on_marked_test_stop(self, instance_num: int) -> None:
        """Perform actions after marked tests are finished."""
        self._log(f"c{instance_num}: in `_on_marked_test_stop`")
        instance_dir = self.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"

        # set cluster to be restarted if needed
        restart_after_mark_files = list(instance_dir.glob(f"{RESTART_AFTER_MARK_GLOB}_*"))
        if restart_after_mark_files:
            for f in restart_after_mark_files:
                os.remove(f)
            self._log(
                f"c{instance_num}: in `_on_marked_test_stop`, " "creating 'restart needed' file"
            )
            open(instance_dir / f"{RESTART_NEEDED_GLOB}_{self.worker_id}", "a").close()

        # remove file that indicates that tests with the mark are running
        test_curr_mark = list(instance_dir.glob(f"{TEST_CURR_MARK_GLOB}_*"))
        if test_curr_mark:
            os.remove(test_curr_mark[0])

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
            self._log(
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
                self._log(f"c{instance_num}: resource '{res}' locked, cannot start")
                break
            res_used = list(instance_dir.glob(f"{RESOURCE_IN_USE_GLOB}_{res}_*"))
            if res_used:
                self._log(f"c{instance_num}: resource '{res}' in use, " "cannot lock and start")
                break
        else:
            self._log(
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
                self._log(f"c{instance_num}: resource '{res}' locked, cannot start")
                break

        if not res_locked:
            self._log(
                f"c{instance_num}: none of the resources in '{resources}' locked, " "can start"
            )
        return bool(res_locked)

    def _save_cli_coverage(self) -> None:
        """Save CLI coverage info collected by this `cluster_obj` instance."""
        self._log("called `_save_cli_coverage`")
        cluster_obj = self.cache.cluster_obj
        if not cluster_obj:
            return

        clusterlib_utils.save_cli_coverage(
            cluster_obj=cluster_obj, pytest_config=self.pytest_config
        )

    def _reload_cluster_obj(self, state_dir: Path) -> None:
        """Realod cluster data if necessary."""
        addrs_data_checksum = helpers.checksum(state_dir / devops_cluster.ADDRS_DATA)
        if addrs_data_checksum == self.cache.last_checksum:
            return

        # save CLI coverage collected by the old `cluster_obj` instance
        self._save_cli_coverage()
        # replace the old `cluster_obj` instance and reload data
        self.cache.cluster_obj = devops_cluster.get_cluster_obj()
        self.cache.test_data = {}
        self.cache.addrs_data = devops_cluster.load_addrs_data()
        self.cache.last_checksum = addrs_data_checksum

    def _reuse_dev_cluster(self) -> clusterlib.ClusterLib:
        """Reuse cluster that was already started outside of test framework."""
        self._cluster_instance = 0
        cluster_env = devops_cluster.get_cluster_env()
        state_dir = Path(cluster_env["state_dir"])

        cluster_obj = self.cache.cluster_obj
        if not cluster_obj:
            cluster_obj = devops_cluster.get_cluster_obj()

        # setup faucet addresses
        if not (state_dir / devops_cluster.ADDRS_DATA).exists():
            tmp_path = state_dir / "addrs_data"
            tmp_path.mkdir(exist_ok=True, parents=True)
            devops_cluster.setup_test_addrs(cluster_obj, tmp_path)

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
        if self.num_of_instances == 1 and DEV_CLUSTER_RUNNING:
            return self._reuse_dev_cluster()

        selected_instance = -1
        restart_here = False
        restart_ready = False
        mark_start_here = False
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
            with helpers.FileLockIfXdist(self.cluster_lock):
                test_on_worker = list(
                    self.lock_dir.glob(
                        f"{CLUSTER_DIR_TEMPLATE}*/{TEST_RUNNING_GLOB}_{self.worker_id}"
                    )
                )

                # test is already running, nothing to set up
                if (
                    first_iteration
                    and test_on_worker
                    and self._cluster_instance != -1
                    and self.cache.cluster_obj
                ):
                    self._log(f"{test_on_worker[0]} already exists")
                    return self.cache.cluster_obj

                first_iteration = False  # needs to be set here, before the first `continue`
                self._cluster_instance = -1

                # try all existing cluster instances
                for instance_num in range(self.num_of_instances):
                    # if instance to run the test on was already decided, skip all other instances
                    # pylint: disable=consider-using-in
                    if selected_instance != -1 and instance_num != selected_instance:
                        continue

                    instance_dir = self.lock_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
                    instance_dir.mkdir(exist_ok=True)

                    # singleton test is running, so no other test can be started
                    if (instance_dir / TEST_SINGLETON_FILE).exists():
                        self._log(f"c{instance_num}: singleton test in progress, cannot run")
                        sleep_delay = 5
                        continue

                    restart_in_progress = list(instance_dir.glob(f"{RESTART_IN_PROGRESS_GLOB}_*"))
                    # cluster restart planned, no new tests can start
                    if not restart_here and restart_in_progress:
                        self._log(f"c{instance_num}: restart in progress, cannot run")
                        continue

                    started_tests = list(instance_dir.glob(f"{TEST_RUNNING_GLOB}_*"))

                    # "marked tests" = group of tests marked with a specific mark.
                    # While these tests are running, no unmarked test can start.
                    # Check if it is indicated that marked tests will start next.
                    marked_tests_starting = list(instance_dir.glob(f"{TEST_MARK_STARTING_GLOB}_*"))
                    marked_tests_starting_my = list(
                        instance_dir.glob(f"{TEST_MARK_STARTING_GLOB}_{mark}_*")
                    )
                    if not mark_start_here and marked_tests_starting_my:
                        self._log(
                            f"c{instance_num}: marked tests starting with my mark, cannot run"
                        )
                        selected_instance = instance_num
                        sleep_delay = 2
                        continue
                    if not mark_start_here and marked_tests_starting:
                        self._log(f"c{instance_num}: marked tests starting, cannot run")
                        sleep_delay = 2
                        continue
                    if mark_start_here and marked_tests_starting:
                        if started_tests:
                            self._log(
                                f"c{instance_num}: unmarked tests running, cannot start marked test"
                            )
                            sleep_delay = 2
                            continue
                        os.remove(marked_tests_starting[0])
                        mark_start_here = False

                    test_curr_mark = list(instance_dir.glob(f"{TEST_CURR_MARK_GLOB}_*"))
                    first_marked_test = bool(mark and not test_curr_mark)

                    # indicate that it is planned to start marked tests as soon as
                    # all currently running tests are finished
                    if first_marked_test and started_tests:
                        self._log(
                            f"c{instance_num}: unmarked tests running, wants to start '{mark}'"
                        )
                        mark_start_here = True
                        selected_instance = instance_num
                        open(
                            instance_dir / f"{TEST_MARK_STARTING_GLOB}_{mark}_{self.worker_id}", "a"
                        ).close()
                        sleep_delay = 2
                        continue

                    # get marked tests status
                    marked_tests_status = self._get_marked_tests_status(
                        cache=marked_tests_cache, instance_num=instance_num
                    )

                    # marked tests are already running
                    if test_curr_mark:
                        active_mark_file = test_curr_mark[0].name

                        self._update_marked_tests(
                            marked_tests_status=marked_tests_status,
                            active_mark_name=active_mark_file,
                            started_tests=started_tests,
                            instance_num=instance_num,
                        )

                        if not mark:
                            self._log(f"c{instance_num}: marked tests running, I don't have mark")
                            sleep_delay = 5
                            continue

                        # check if this test has the same mark as currently running marked tests,
                        # so it can run
                        if f"{TEST_CURR_MARK_GLOB}_{mark}" not in active_mark_file:
                            self._log(
                                f"c{instance_num}: marked tests running, "
                                f"I have different mark - {mark}"
                            )
                            sleep_delay = 5
                            continue

                        self._log(
                            f"c{instance_num}: in marked tests branch, "
                            f"I have required mark '{mark}'"
                        )

                    # reset counter of cycles with no marked test running
                    marked_tests_status.no_marked_tests_iter = 0

                    # this test is a singleton - no other test can run while this one is running
                    if singleton and started_tests:
                        self._log(f"c{instance_num}: tests are running, cannot start singleton")
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
                    new_cmd_restart = bool(start_cmd and (first_marked_test or singleton))
                    if not restart_here and (
                        new_cmd_restart or self._is_restart_needed(instance_num)
                    ):
                        self._log(f"c{instance_num}: setting to restart cluster")
                        restart_here = True
                        selected_instance = instance_num
                        open(
                            instance_dir / f"{RESTART_IN_PROGRESS_GLOB}_{self.worker_id}", "a"
                        ).close()

                    # cluster restart will be performed by this worker
                    if restart_here and started_tests:
                        self._log(f"c{instance_num}: tests are running, cannot restart")
                        sleep_delay = 2
                        continue

                    # we've found suitable cluster instance
                    self._cluster_instance = instance_num
                    cluster_instances.set_cardano_node_socket_path(instance_num)

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
                            self._log(f"c{instance_num}: calling restart")
                            # the actual `_restart` function will be called outside
                            # of global lock
                            restart_ready = True
                            continue

                    # from this point on, all conditions needed to start the test are met

                    # this test is a singleton
                    if singleton:
                        self._log(f"c{instance_num}: starting singleton")
                        open(self.instance_dir / TEST_SINGLETON_FILE, "a").close()

                    # this test is a first marked test
                    if first_marked_test:
                        self._log(f"c{instance_num}: starting '{mark}' tests")
                        open(self.instance_dir / f"{TEST_CURR_MARK_GLOB}_{mark}", "a").close()

                    # create status file for each in-use resource
                    _ = [
                        open(
                            self.instance_dir / f"{RESOURCE_IN_USE_GLOB}_{r}_{self.worker_id}", "a"
                        ).close()
                        for r in use_resources
                    ]

                    # create status file for each locked resource
                    _ = [
                        open(
                            self.instance_dir / f"{RESOURCE_LOCKED_GLOB}_{r}_{self.worker_id}", "a"
                        ).close()
                        for r in lock_resources
                    ]

                    # cleanup = cluster restart after test (group of tests) is finished
                    if cleanup:
                        # cleanup after group of test that are marked with a marker
                        if mark:
                            self._log(f"c{instance_num}: cleanup and mark")
                            open(
                                self.instance_dir / f"{RESTART_AFTER_MARK_GLOB}_{self.worker_id}",
                                "a",
                            ).close()
                        # cleanup after single test (e.g. singleton)
                        else:
                            self._log(f"c{instance_num}: cleanup and not mark")
                            open(
                                self.instance_dir / f"{RESTART_NEEDED_GLOB}_{self.worker_id}", "a"
                            ).close()

                    break
                else:
                    # if the test cannot run on any instance, return to top-level loop
                    continue

                test_running_file = self.instance_dir / f"{TEST_RUNNING_GLOB}_{self.worker_id}"
                self._log(f"c{self.cluster_instance}: creating {test_running_file}")
                open(test_running_file, "a").close()

                cluster_env = devops_cluster.get_cluster_env()
                state_dir = Path(cluster_env["state_dir"])

                # check if it is necessary to reload data
                self._reload_cluster_obj(state_dir=state_dir)

                cluster_obj = self.cache.cluster_obj
                if not cluster_obj:
                    cluster_obj = devops_cluster.get_cluster_obj()

                # `cluster_obj` is ready, we can start the test
                break

        return cluster_obj
