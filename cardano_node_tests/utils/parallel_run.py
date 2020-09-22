import contextlib
import dataclasses
import datetime
import os
import random
from pathlib import Path
from typing import Generator
from typing import Optional

from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import UnpackableSequence

CLUSTER_LOCK = ".cluster.lock"
LOCK_LOG_FILE = ".lock.log"
SESSION_RUNNING_FILE = ".session_running"
TEST_SINGLETON_FILE = ".test_singleton"
RESOURCE_LOCKED_GLOB = ".resource_locked"
RESOURCE_IN_USE_GLOB = ".resource_in_use"
RESTART_NEEDED_GLOB = ".needs_restart"
RESTART_IN_PROGRESS_GLOB = ".restart_in_progress"
RESTART_AFTER_MARK_GLOB = ".restart_after_mark"
TEST_RUNNING_GLOB = ".test_running"
TEST_RUNNING_MARK_GLOB = ".test_marked"
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

        lock_log = self.lock_dir.parent / LOCK_LOG_FILE
        self.lock_log = lock_log if lock_log.is_file() else None

    @property
    def cache(self) -> ClusterManagerCache:
        return self.get_cache()

    def _log(self, msg: str) -> None:
        """Log message - needs to be called while having lock."""
        if not self.lock_log:
            return
        with open(self.lock_log, "a") as logfile:
            logfile.write(f"{datetime.datetime.now()} on {self.worker_id}: {msg}\n")

    def _locked_log(self, msg: str) -> None:
        """Log message - will obtain lock first."""
        if not self.lock_log:
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

    def _save_cluster_data(self) -> None:
        """Save cluster artifacts generated during running the tests."""
        self._log("called `_save_cluster_data`")
        cluster_obj = self.cache.cluster_obj
        if not cluster_obj:
            return

        # save CLI coverage
        helpers.save_cli_coverage(cluster_obj, self.request)
        # save artifacts
        helpers.save_cluster_artifacts(artifacts_dir=self.pytest_tmp_dir)

    def _restart(self) -> clusterlib.ClusterLib:
        """Restart cluster."""
        self._log("called `_restart`")
        self.stop()
        cluster_obj = helpers.start_cluster()

        # setup faucet addresses
        tmp_path = Path(self.tmp_path_factory.mktemp("addrs_data"))
        helpers.setup_test_addrs(cluster_obj, tmp_path)

        # remove status files that are no longer valid after restart
        for f in self.lock_dir.glob(f"{RESTART_IN_PROGRESS_GLOB}_*"):
            os.remove(f)
        for f in self.lock_dir.glob(f"{RESTART_NEEDED_GLOB}_*"):
            os.remove(f)
        for f in self.lock_dir.glob(f"{TEST_RUNNING_GLOB}_*"):
            os.remove(f)
        for f in self.lock_dir.glob(f"{TEST_RUNNING_MARK_GLOB}_*"):
            os.remove(f)
        try:
            os.remove(self.lock_dir / TEST_SINGLETON_FILE)
        except FileNotFoundError:
            pass

        # create file that indicates that the session is running
        session_running_file = self.lock_dir / SESSION_RUNNING_FILE
        if not session_running_file.exists():
            open(session_running_file, "a").close()

        return cluster_obj

    def stop(self) -> None:
        """Stop cluster."""
        self._log("called `_stop`")
        helpers.stop_cluster()
        self._save_cluster_data()

    def set_needs_restart(self) -> None:
        """Indicate that the cluster needs restart."""
        with helpers.FileLockIfXdist(self.cluster_lock):
            self._log("called `_set_needs_restart`")
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
            self._log("in `_on_marked_test_stop`, creating restart needed")
            open(self.lock_dir / f"{RESTART_NEEDED_GLOB}_{self.worker_id}", "a").close()

        # remove file that indicates that tests with the mark are running
        test_curr_mark = list(self.lock_dir.glob(f"{TEST_CURR_MARK_GLOB}_*"))
        if test_curr_mark:
            os.remove(test_curr_mark[0])

    def on_test_stop(self) -> None:
        """Perform actions after the test finished."""
        with helpers.FileLockIfXdist(self.cluster_lock):
            self._log("called `on_test_stop`")

            # remove mark files created by the worker
            mark_files = list(self.lock_dir.glob(f"{TEST_RUNNING_MARK_GLOB}_*_{self.worker_id}"))
            for f in mark_files:
                os.remove(f)

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
            os.remove(self.lock_dir / f"{TEST_RUNNING_GLOB}_{self.worker_id}")

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
        no_tests_iteration = 0
        test_running_file = self.lock_dir / f"{TEST_RUNNING_GLOB}_{self.worker_id}"
        cluster_obj = self.cache.cluster_obj

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
                    self._log("singleton file exists, cannot run")
                    sleep_delay = 5
                    no_tests_iteration = 0
                    continue

                restart_in_progress = list(self.lock_dir.glob(f"{RESTART_IN_PROGRESS_GLOB}_*"))
                # cluster restart planned, no new tests can start
                if not restart_here and restart_in_progress:
                    self._log("restart in progress, cannot run")
                    no_tests_iteration = 0
                    continue
                # indicate that there will be cluster restart
                if not restart_here and self._is_restart_needed():
                    self._log("setting to restart cluster")
                    restart_here = True
                    open(
                        self.lock_dir / f"{RESTART_IN_PROGRESS_GLOB}_{self.worker_id}", "a"
                    ).close()

                started_tests = list(self.lock_dir.glob(f"{TEST_RUNNING_GLOB}_*"))

                # cluster restart will be performed by this worker
                if restart_here:
                    if started_tests:
                        self._log("tests are running, cannot restart")
                        sleep_delay = 2
                        no_tests_iteration = 0
                        continue
                    self._log("calling restart")
                    restart_here = False
                    cluster_obj = self._restart()
                    self.cache.cluster_obj = cluster_obj

                # "marked tests" = group of tests marked with a specific mark.
                # While these tests are running, no unmarked test can start.
                # Check if it is indicated that marked tests will start next.
                marked_tests_starting = list(self.lock_dir.glob(f"{TEST_MARK_STARTING_GLOB}_*"))
                if not mark_start_here and marked_tests_starting:
                    self._log("marked tests starting, cannot run")
                    sleep_delay = 2
                    no_tests_iteration = 0
                    continue
                if mark_start_here and marked_tests_starting:
                    if started_tests:
                        self._log("unmarked tests running, cannot start marked test")
                        sleep_delay = 2
                        no_tests_iteration = 0
                        continue
                    os.remove(marked_tests_starting[0])
                    mark_start_here = False

                test_curr_mark = list(self.lock_dir.glob(f"{TEST_CURR_MARK_GLOB}_*"))

                # indicate that it is planned to start marked tests as soon as all currently running
                # tests are finished
                if mark and not test_curr_mark and started_tests:
                    self._log(f"unmarked tests running, wants to start '{mark}'")
                    mark_start_here = True
                    open(self.lock_dir / f"{TEST_MARK_STARTING_GLOB}_{self.worker_id}", "a").close()
                    sleep_delay = 2
                    no_tests_iteration = 0
                    continue
                # no tests are running, can start marked test
                if mark and not test_curr_mark:
                    self._log(f"no tests running, starting '{mark}'")
                    open(
                        self.lock_dir / f"{TEST_RUNNING_MARK_GLOB}_{mark}_{self.worker_id}", "a"
                    ).close()
                    open(self.lock_dir / f"{TEST_CURR_MARK_GLOB}_{mark}", "a").close()
                    no_tests_iteration = 0

                # marked tests are already running
                if test_curr_mark:
                    # check if there is a stale mark status file
                    if started_tests:
                        no_tests_iteration = 0
                    else:
                        no_tests_iteration += 1
                    if no_tests_iteration >= 10:
                        self._log("no tests running for a while, cleaning the mark status file")
                        self._on_marked_test_stop()

                    if not mark:
                        self._log("marked tests running, I don't have mark")
                        sleep_delay = 5
                        continue

                    # check if this test has the same mark as currently running marked tests,
                    # so it can run
                    active_mark_file = test_curr_mark[0].name
                    if f"{TEST_CURR_MARK_GLOB}_{mark}" not in active_mark_file:
                        self._log(f"marked tests running, I have different mark - {mark}")
                        sleep_delay = 5
                        continue

                    self._log(f"in marked tests branch, I have required mark '{mark}'")
                    open(
                        self.lock_dir / f"{TEST_RUNNING_MARK_GLOB}_{mark}_{self.worker_id}", "a"
                    ).close()

                # this test is a singleton - not other test can run while this one is running
                if singleton:
                    if started_tests:
                        self._log("tests are running, cannot start singleton")
                        sleep_delay = 5
                        no_tests_iteration = 0
                        continue
                    self._log("tests are not running, starting singleton")
                    open(self.lock_dir / TEST_SINGLETON_FILE, "a").close()

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
                        no_tests_iteration = 0
                        continue
                    self._log(
                        f"none of the resources in '{lock_resources}' locked or in use, "
                        "starting and locking"
                    )

                    # create status file for each locked resource
                    _ = [
                        open(
                            self.lock_dir / f"{RESOURCE_LOCKED_GLOB}_{r}_{self.worker_id}", "a"
                        ).close()
                        for r in lock_resources
                    ]

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
                        no_tests_iteration = 0
                        continue
                    self._log(f"none of the resources in '{use_resources}' locked, starting")

                    # create status file for each in-use resource
                    _ = [
                        open(
                            self.lock_dir / f"{RESOURCE_IN_USE_GLOB}_{r}_{self.worker_id}", "a"
                        ).close()
                        for r in use_resources
                    ]

                # cleanup = restart of cluster
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

                cluster_env = helpers.get_cluster_env()
                state_dir = Path(cluster_env["state_dir"])

                # check if it is necessary to reload data
                # must be lock-protected because `load_addrs_data` is reading file from disk
                addrs_data_checksum = helpers.checksum(state_dir / helpers.ADDR_DATA)
                if addrs_data_checksum != self.cache.last_checksum:
                    self.cache.cluster_obj = clusterlib.ClusterLib(state_dir)
                    self.cache.test_data = {}
                    self.cache.addrs_data = helpers.load_addrs_data()
                    self.cache.last_checksum = addrs_data_checksum

                cluster_obj = self.cache.cluster_obj
                if not cluster_obj:
                    cluster_obj = clusterlib.ClusterLib(state_dir)

                # `cluster_obj` is ready, we can start the test
                break

        return cluster_obj
