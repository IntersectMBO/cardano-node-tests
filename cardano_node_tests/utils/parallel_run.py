import contextlib
import dataclasses
import datetime
import os
import random
from pathlib import Path
from typing import Generator
from typing import Optional

import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import devops_cluster
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import UnpackableSequence

CLUSTER_LOCK = ".cluster.lock"
RUN_LOG_FILE = ".parallel.log"
SESSION_RUNNING_FILE = ".session_running"
TEST_SINGLETON_FILE = ".test_singleton"
RESOURCE_LOCKED_GLOB = ".resource_locked"
RESOURCE_IN_USE_GLOB = ".resource_in_use"
RESTART_NEEDED_GLOB = ".needs_restart"
RESTART_IN_PROGRESS_GLOB = ".restart_in_progress"
RESTART_AFTER_MARK_GLOB = ".restart_after_mark"
TEST_RUNNING_GLOB = ".test_running"
TEST_CURR_MARK_GLOB = ".curr_test_mark"
TEST_MARK_STARTING_GLOB = ".starting_marked_tests"


@dataclasses.dataclass
class ClusterManagerCache:
    cluster_obj: Optional[clusterlib.ClusterLib] = None
    test_data: dict = dataclasses.field(default_factory=dict)
    addrs_data: dict = dataclasses.field(default_factory=dict)
    last_checksum: str = ""


class ClusterManager:
    manager_cache = ClusterManagerCache()

    @classmethod
    def get_cache(cls) -> ClusterManagerCache:
        return cls.manager_cache

    def __init__(
        self, tmp_path_factory: TempdirFactory, worker_id: str, request: FixtureRequest
    ) -> None:
        self.cluster_obj: Optional[clusterlib.ClusterLib] = None
        self.worker_id = worker_id
        self.request = request
        self.tmp_path_factory = tmp_path_factory
        self.pytest_tmp_dir = Path(tmp_path_factory.getbasetemp())

        self.is_xdist = worker_id != "master"
        if self.is_xdist:
            self.lock_dir = self.pytest_tmp_dir.parent
            self.range_num = 5
        else:
            self.lock_dir = self.pytest_tmp_dir
            self.range_num = 1

        self.cluster_lock = f"{self.lock_dir}/{CLUSTER_LOCK}"
        self.lock_log = self._init_log()

    @property
    def cache(self) -> ClusterManagerCache:
        return self.get_cache()

    def _init_log(self) -> Path:
        """Return path to run log file."""
        env_log = os.environ.get("PARALLEL_LOG")
        run_log = Path(env_log or self.lock_dir / RUN_LOG_FILE).resolve()
        # if PARALLEL_LOG env variable was set, create the log file if it doesn't exist
        if env_log:
            open(run_log, "a").close()
        return run_log

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

    def _is_restart_needed(self) -> bool:
        """Check if it is necessary to restart cluster."""
        if not (self.lock_dir / SESSION_RUNNING_FILE).exists():
            return True
        if list(self.lock_dir.glob(f"{RESTART_NEEDED_GLOB}_*")):
            return True
        return False

    def _save_cluster_artifacts(self) -> None:
        """Save cluster artifacts (logs, certs, etc.) to pytest temp dir."""
        self._log("called `_save_cluster_artifacts`")
        cluster_obj = self.cache.cluster_obj
        if not cluster_obj:
            return

        devops_cluster.save_cluster_artifacts(artifacts_dir=self.pytest_tmp_dir)

    def save_cli_coverage(self) -> None:
        """Save CLI coverage info collected by this `cluster_obj` instance."""
        self._log("called `save_cli_coverage`")
        cluster_obj = self.cache.cluster_obj
        if not cluster_obj:
            return

        clusterlib_utils.save_cli_coverage(cluster_obj, self.request)

    def _restart(self, start_cmd: str = "") -> clusterlib.ClusterLib:
        """Restart cluster."""
        self._log(f"called `_restart`, start_cmd='{start_cmd}'")
        self.stop()

        try:
            cluster_obj = devops_cluster.start_cluster(cmd=start_cmd)
        except Exception:
            self._log("failed to start cluster, exiting pytest")
            pytest.exit(msg="Failed to start cluster", returncode=1)

        # setup faucet addresses
        tmp_path = Path(self.tmp_path_factory.mktemp("addrs_data"))
        devops_cluster.setup_test_addrs(cluster_obj, tmp_path)

        # remove status files that are no longer valid after restart
        for f in self.lock_dir.glob(f"{RESTART_IN_PROGRESS_GLOB}_*"):
            os.remove(f)
        for f in self.lock_dir.glob(f"{RESTART_NEEDED_GLOB}_*"):
            os.remove(f)

        # create file that indicates that the session is running
        session_running_file = self.lock_dir / SESSION_RUNNING_FILE
        if not session_running_file.exists():
            open(session_running_file, "a").close()

        return cluster_obj

    def stop(self) -> None:
        """Stop cluster."""
        self._log("called `stop`")
        devops_cluster.stop_cluster()
        self._save_cluster_artifacts()

    def set_needs_restart(self) -> None:
        """Indicate that the cluster needs restart."""
        with helpers.FileLockIfXdist(self.cluster_lock):
            self._log("called `set_needs_restart`")
            open(self.lock_dir / f"{RESTART_NEEDED_GLOB}_{self.worker_id}", "a").close()

    @contextlib.contextmanager
    def needs_restart_after_failure(self) -> Generator:
        """Indicate that the cluster needs restart if command failed - context manager."""
        try:
            yield
        except Exception:
            self.set_needs_restart()
            raise

    def _on_marked_test_stop(self) -> None:
        """Perform actions after marked tests are finished."""
        self._log("in `_on_marked_test_stop`")

        # set cluster to be restarted if needed
        restart_after_mark_files = list(self.lock_dir.glob(f"{RESTART_AFTER_MARK_GLOB}_*"))
        if restart_after_mark_files:
            for f in restart_after_mark_files:
                os.remove(f)
            self._log("in `_on_marked_test_stop`, creating 'restart needed' file")
            open(self.lock_dir / f"{RESTART_NEEDED_GLOB}_{self.worker_id}", "a").close()

        # remove file that indicates that tests with the mark are running
        test_curr_mark = list(self.lock_dir.glob(f"{TEST_CURR_MARK_GLOB}_*"))
        if test_curr_mark:
            os.remove(test_curr_mark[0])

    def on_test_stop(self) -> None:
        """Perform actions after the test finished."""
        with helpers.FileLockIfXdist(self.cluster_lock):
            self._log("called `on_test_stop`")

            # remove resource locking files created by the worker
            resource_locking_files = list(
                self.lock_dir.glob(f"{RESOURCE_LOCKED_GLOB}_*_{self.worker_id}")
            )
            for f in resource_locking_files:
                os.remove(f)

            # remove "resource in use" files created by the worker
            resource_in_use_files = list(
                self.lock_dir.glob(f"{RESOURCE_IN_USE_GLOB}_*_{self.worker_id}")
            )
            for f in resource_in_use_files:
                os.remove(f)

            # remove file that indicates that a test is running on the worker
            try:
                os.remove(self.lock_dir / f"{TEST_RUNNING_GLOB}_{self.worker_id}")
            except FileNotFoundError:
                pass

            # remove file that indicates the test was singleton
            try:
                os.remove(self.lock_dir / TEST_SINGLETON_FILE)
            except FileNotFoundError:
                pass

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
        # pylint: disable=too-many-statements,too-many-branches
        restart_here = False
        mark_start_here = False
        first_iteration = True
        sleep_delay = 1
        no_marked_tests_iter = 0
        last_seen_mark = ""
        test_running_file = self.lock_dir / f"{TEST_RUNNING_GLOB}_{self.worker_id}"
        cluster_obj = self.cache.cluster_obj

        if start_cmd:
            if not (singleton or mark):
                raise AssertionError(
                    "Custom start command can be used only together with `singleton` or `mark`"
                )
            # always clean after test(s) that started custom cluster
            cleanup = True

        # iterate until it is possible to start the test
        while True:
            if not first_iteration:
                helpers.xdist_sleep(random.random() * sleep_delay)

            with helpers.FileLockIfXdist(self.cluster_lock):
                # test is already running, nothing to set up
                if first_iteration and test_running_file.exists() and cluster_obj:
                    self._log(f"{test_running_file} already exists")
                    return cluster_obj

                first_iteration = False  # needs to be set here, before the first `continue`

                # singleton test is running, so no other test can be started
                if (self.lock_dir / TEST_SINGLETON_FILE).exists():
                    self._log("singleton test in progress, cannot run")
                    sleep_delay = 5
                    continue

                restart_in_progress = list(self.lock_dir.glob(f"{RESTART_IN_PROGRESS_GLOB}_*"))
                # cluster restart planned, no new tests can start
                if not restart_here and restart_in_progress:
                    self._log("restart in progress, cannot run")
                    continue

                started_tests = list(self.lock_dir.glob(f"{TEST_RUNNING_GLOB}_*"))

                # "marked tests" = group of tests marked with a specific mark.
                # While these tests are running, no unmarked test can start.
                # Check if it is indicated that marked tests will start next.
                marked_tests_starting = list(self.lock_dir.glob(f"{TEST_MARK_STARTING_GLOB}_*"))
                if not mark_start_here and marked_tests_starting:
                    self._log("marked tests starting, cannot run")
                    sleep_delay = 2
                    continue
                if mark_start_here and marked_tests_starting:
                    if started_tests:
                        self._log("unmarked tests running, cannot start marked test")
                        sleep_delay = 2
                        continue
                    os.remove(marked_tests_starting[0])
                    mark_start_here = False

                test_curr_mark = list(self.lock_dir.glob(f"{TEST_CURR_MARK_GLOB}_*"))
                first_marked_test = bool(mark and not test_curr_mark)

                # indicate that it is planned to start marked tests as soon as all currently running
                # tests are finished
                if first_marked_test and started_tests:
                    self._log(f"unmarked tests running, wants to start '{mark}'")
                    mark_start_here = True
                    open(self.lock_dir / f"{TEST_MARK_STARTING_GLOB}_{self.worker_id}", "a").close()
                    sleep_delay = 2
                    continue

                # marked tests are already running
                if test_curr_mark:
                    active_mark_file = test_curr_mark[0].name

                    # check if there is a stale mark status file
                    if started_tests or last_seen_mark != active_mark_file:
                        no_marked_tests_iter = 0
                    else:
                        no_marked_tests_iter += 1
                    if no_marked_tests_iter >= 10:
                        self._log(
                            "no marked tests running for a while, cleaning the mark status file"
                        )
                        self._on_marked_test_stop()

                    last_seen_mark = active_mark_file

                    if not mark:
                        self._log("marked tests running, I don't have mark")
                        sleep_delay = 5
                        continue

                    # check if this test has the same mark as currently running marked tests,
                    # so it can run
                    if f"{TEST_CURR_MARK_GLOB}_{mark}" not in active_mark_file:
                        self._log(f"marked tests running, I have different mark - {mark}")
                        sleep_delay = 5
                        continue

                    self._log(f"in marked tests branch, I have required mark '{mark}'")

                # reset counter of cycles with no marked test running
                no_marked_tests_iter = 0

                # this test is a singleton - no other test can run while this one is running
                if singleton and started_tests:
                    self._log("tests are running, cannot start singleton")
                    sleep_delay = 5
                    continue

                # this test wants to lock some resources, check if these are not locked or in use
                if lock_resources:
                    res_usable = False
                    for res in lock_resources:
                        res_locked = list(self.lock_dir.glob(f"{RESOURCE_LOCKED_GLOB}_{res}_*"))
                        if res_locked:
                            self._log(f"resource '{res}' locked, cannot start")
                            break
                        res_used = list(self.lock_dir.glob(f"{RESOURCE_IN_USE_GLOB}_{res}_*"))
                        if res_used:
                            self._log(f"resource '{res}' in use, cannot lock and start")
                            break
                    else:
                        res_usable = True

                    if not res_usable:
                        sleep_delay = 5
                        continue
                    self._log(
                        f"none of the resources in '{lock_resources}' locked or in use, "
                        "can start and lock"
                    )

                # filter out `lock_resources` from the list of `use_resources`
                if use_resources and lock_resources:
                    use_resources = list(set(use_resources) - set(lock_resources))

                # this test wants to use some resources, check if these are not locked
                if use_resources:
                    res_locked = []
                    for res in use_resources:
                        res_locked = list(self.lock_dir.glob(f"{RESOURCE_LOCKED_GLOB}_{res}_*"))
                        if res_locked:
                            self._log(f"resource '{res}' locked, cannot start")
                            break
                    if res_locked:
                        sleep_delay = 5
                        continue
                    self._log(f"none of the resources in '{use_resources}' locked, can start")

                # indicate that there will be cluster restart
                new_cmd_restart = bool(start_cmd and (first_marked_test or singleton))
                if not restart_here and (new_cmd_restart or self._is_restart_needed()):
                    self._log("setting to restart cluster")
                    restart_here = True
                    open(
                        self.lock_dir / f"{RESTART_IN_PROGRESS_GLOB}_{self.worker_id}", "a"
                    ).close()

                # cluster restart will be performed by this worker
                if restart_here:
                    if started_tests:
                        self._log("tests are running, cannot restart")
                        sleep_delay = 2
                        continue
                    self._log("calling restart")
                    restart_here = False
                    self._restart(start_cmd=start_cmd)

                # from this point on, all conditions needed to start the test are met

                # this test is a singleton
                if singleton:
                    self._log("starting singleton")
                    open(self.lock_dir / TEST_SINGLETON_FILE, "a").close()

                # this test is a first marked test
                if first_marked_test:
                    self._log(f"starting '{mark}' tests")
                    open(self.lock_dir / f"{TEST_CURR_MARK_GLOB}_{mark}", "a").close()

                # create status file for each in-use resource
                _ = [
                    open(
                        self.lock_dir / f"{RESOURCE_IN_USE_GLOB}_{r}_{self.worker_id}", "a"
                    ).close()
                    for r in use_resources
                ]

                # create status file for each locked resource
                _ = [
                    open(
                        self.lock_dir / f"{RESOURCE_LOCKED_GLOB}_{r}_{self.worker_id}", "a"
                    ).close()
                    for r in lock_resources
                ]

                # cleanup = cluster restart after test (group of tests) is finished
                if cleanup:
                    # cleanup after group of test that are marked with a marker
                    if mark:
                        self._log("cleanup and mark")
                        open(
                            self.lock_dir / f"{RESTART_AFTER_MARK_GLOB}_{self.worker_id}", "a"
                        ).close()
                    # cleanup after single test (e.g. singleton)
                    else:
                        self._log("cleanup and not mark")
                        open(self.lock_dir / f"{RESTART_NEEDED_GLOB}_{self.worker_id}", "a").close()

                self._log(f"creating {test_running_file}")
                open(test_running_file, "a").close()

                cluster_env = devops_cluster.get_cluster_env()
                state_dir = Path(cluster_env["state_dir"])

                # check if it is necessary to reload data
                # must be lock-protected because `load_addrs_data` is reading file from disk
                addrs_data_checksum = helpers.checksum(state_dir / devops_cluster.ADDR_DATA)
                if addrs_data_checksum != self.cache.last_checksum:
                    # save CLI coverage collected by the old `cluster_obj` instance
                    self.save_cli_coverage()
                    # replace the old `cluster_obj` instance and reload data
                    self.cache.cluster_obj = clusterlib.ClusterLib(state_dir)
                    self.cache.test_data = {}
                    self.cache.addrs_data = devops_cluster.load_addrs_data()
                    self.cache.last_checksum = addrs_data_checksum

                cluster_obj = self.cache.cluster_obj
                if not cluster_obj:
                    cluster_obj = clusterlib.ClusterLib(state_dir)

                # `cluster_obj` is ready, we can start the test
                break

        return cluster_obj
