"""Functionality for obtaining and setting up a cluster instance."""
# pylint: disable=abstract-class-instantiated
import contextlib
import dataclasses
import logging
import os
import random
import shutil
import time
from pathlib import Path
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import Optional

import pytest
from _pytest.config import Config
from _pytest.tmpdir import TempPathFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import common
from cardano_node_tests.cluster_management import resources
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.utils import artifacts
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)

if configuration.IS_XDIST:
    _xdist_sleep = time.sleep
else:

    def _xdist_sleep(secs: float) -> None:
        """No need to sleep if tests are running on a single worker."""
        # pylint: disable=unused-argument,unnecessary-pass
        pass


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


@dataclasses.dataclass
class _MarkedTestsStatus:
    no_marked_tests_iter: int = 0


@dataclasses.dataclass
class _ClusterGetStatus:
    """Intermediate status while trying to `get` suitable cluster instance."""

    mark: str
    lock_resources: resources_management.ResourcesType
    use_resources: resources_management.ResourcesType
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
    final_lock_resources: Iterable[str] = ()
    final_use_resources: Iterable[str] = ()
    # status files
    started_tests_sfiles: Iterable[Path] = ()
    marked_starting_sfiles: Iterable[Path] = ()
    marked_running_sfiles: Iterable[Path] = ()


class ClusterGetter:
    """Internal class that encapsulate functionality for getting a cluster instance."""

    def __init__(
        self,
        tmp_path_factory: TempPathFactory,
        worker_id: str,
        pytest_config: Config,
        num_of_instances: int,
        log_func: Callable,
    ) -> None:
        self.pytest_config = pytest_config
        self.worker_id = worker_id
        self.tmp_path_factory = tmp_path_factory
        self.num_of_instances = num_of_instances
        self.log = log_func

        self.pytest_tmp_dir = temptools.get_pytest_root_tmp(self.tmp_path_factory)
        self.cluster_lock = f"{self.pytest_tmp_dir}/{common.CLUSTER_LOCK}"

        self._cluster_instance_num = -1

    @property
    def cluster_instance_num(self) -> int:
        if self._cluster_instance_num == -1:
            raise RuntimeError("Cluster instance not set.")
        return self._cluster_instance_num

    @property
    def instance_dir(self) -> Path:
        _instance_dir = (
            self.pytest_tmp_dir / f"{common.CLUSTER_DIR_TEMPLATE}{self.cluster_instance_num}"
        )
        return _instance_dir

    def _create_startup_files_dir(self, instance_num: int) -> Path:
        _instance_dir = self.pytest_tmp_dir / f"{common.CLUSTER_DIR_TEMPLATE}{instance_num}"
        rand_str = clusterlib.get_rand_str(8)
        startup_files_dir = _instance_dir / "startup_files" / rand_str
        startup_files_dir.mkdir(exist_ok=True, parents=True)
        return startup_files_dir

    def _restart(self, start_cmd: str = "", stop_cmd: str = "") -> bool:  # noqa: C901
        """Restart cluster.

        Not called under global lock!
        """
        # pylint: disable=too-many-branches,too-many-statements
        cluster_running_file = self.instance_dir / common.CLUSTER_RUNNING_FILE

        # don't restart cluster if it was started outside of test framework
        if configuration.DEV_CLUSTER_RUNNING:
            self.log(f"c{self.cluster_instance_num}: ignoring restart, dev cluster is running")
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

        self.log(
            f"c{self.cluster_instance_num}: called `_restart`, start_cmd='{start_cmd}', "
            f"stop_cmd='{stop_cmd}'"
        )

        state_dir = cluster_nodes.get_cluster_env().state_dir

        if state_dir.exists() and not (state_dir / common.CLUSTER_STARTED_BY_FRAMEWORK).exists():
            raise RuntimeError("Cannot restart cluster when it was not started by the framework.")

        startup_files = cluster_nodes.get_cluster_type().cluster_scripts.prepare_scripts_files(
            destdir=self._create_startup_files_dir(self.cluster_instance_num),
            instance_num=self.cluster_instance_num,
            start_script=start_cmd,
            stop_script=stop_cmd,
        )

        self.log(
            f"c{self.cluster_instance_num}: in `_restart`, new files "
            f"start_cmd='{startup_files.start_script}', "
            f"stop_cmd='{startup_files.stop_script}'"
        )

        excp: Optional[Exception] = None
        for i in range(2):
            if i > 0:
                self.log(
                    f"c{self.cluster_instance_num}: failed to start cluster:\n{excp}\nretrying"
                )
                time.sleep(0.2)

            try:
                LOGGER.info(f"Stopping cluster with `{startup_files.stop_script}`.")
                helpers.run_command(str(startup_files.stop_script))
            except Exception as err:
                self.log(f"c{self.cluster_instance_num}: failed to stop cluster:\n{err}")

            # save artifacts only when produced during this test run
            if cluster_running_file.exists():
                artifacts.save_start_script_coverage(
                    log_file=state_dir / common.CLUSTER_START_CMDS_LOG,
                    pytest_config=self.pytest_config,
                )
                artifacts.save_cluster_artifacts(save_dir=self.pytest_tmp_dir, state_dir=state_dir)

            shutil.rmtree(state_dir, ignore_errors=True)

            with contextlib.suppress(Exception):
                _kill_supervisor(self.cluster_instance_num)

            _cluster_started = False
            try:
                cluster_obj = cluster_nodes.start_cluster(
                    cmd=str(startup_files.start_script), args=startup_files.start_script_args
                )
                _cluster_started = True
            except Exception as err:
                LOGGER.error(f"Failed to start cluster: {err}")
                excp = err
            finally:
                if state_dir.exists():
                    helpers.touch(state_dir / common.CLUSTER_STARTED_BY_FRAMEWORK)
            # `else` cannot be used together with `finally`
            if _cluster_started:
                break
        else:
            self.log(
                f"c{self.cluster_instance_num}: failed to start cluster:\n{excp}\ncluster dead"
            )
            if not configuration.IS_XDIST:
                pytest.exit(msg=f"Failed to start cluster, exception: {excp}", returncode=1)
            helpers.touch(self.instance_dir / common.CLUSTER_DEAD_FILE)
            return False

        # generate ID for the new cluster instance so it is possible to match log entries with
        # cluster instance files saved as artifacts
        cluster_instance_id = helpers.get_rand_str(8)
        with open(
            state_dir / artifacts.CLUSTER_INSTANCE_ID_FILENAME, "w", encoding="utf-8"
        ) as fp_out:
            fp_out.write(cluster_instance_id)
        self.log(f"c{self.cluster_instance_num}: started cluster instance '{cluster_instance_id}'")

        # Create temp dir for faucet addresses data.
        # Pytest's mktemp adds number to the end of the dir name, so keep the trailing '_'
        # as separator. Resulting dir name is e.g. 'addrs_data_ci3_0'.
        tmp_path = Path(self.tmp_path_factory.mktemp(f"addrs_data_ci{self.cluster_instance_num}_"))
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

        self.log("c0: setting up dev cluster")

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
            self.log(f"c{instance_num}: found failed services {failed_services}")
        elif not_running_services:
            self.log(f"c{instance_num}: found not running services {not_running_services}")
        return not failed_services

    def _is_restart_needed(self, instance_num: int) -> bool:
        """Check if it is necessary to restart cluster."""
        instance_dir = self.pytest_tmp_dir / f"{common.CLUSTER_DIR_TEMPLATE}{instance_num}"
        # if cluster instance is not started yet
        if not (instance_dir / common.CLUSTER_RUNNING_FILE).exists():
            return True
        # if it was indicated that the cluster instance needs to be restarted
        if list(instance_dir.glob(f"{common.RESTART_NEEDED_GLOB}_*")):
            return True
        # if a service failed on cluster instance
        if not self._is_healthy(instance_num):
            return True
        return False

    def _on_marked_test_stop(self, instance_num: int) -> None:
        """Perform actions after marked tests are finished."""
        self.log(f"c{instance_num}: in `_on_marked_test_stop`")
        instance_dir = self.pytest_tmp_dir / f"{common.CLUSTER_DIR_TEMPLATE}{instance_num}"

        # set cluster instance to be restarted if needed
        restart_after_mark_files = list(instance_dir.glob(f"{common.RESTART_AFTER_MARK_GLOB}_*"))
        if restart_after_mark_files:
            for f in restart_after_mark_files:
                f.unlink()
            self.log(f"c{instance_num}: in `_on_marked_test_stop`, creating 'restart needed' file")
            helpers.touch(instance_dir / f"{common.RESTART_NEEDED_GLOB}_{self.worker_id}")

        # remove file that indicates that tests with the mark are running
        marked_running_sfiles = list(instance_dir.glob(f"{common.TEST_CURR_MARK_GLOB}_*"))
        if marked_running_sfiles:
            marked_running_sfiles[0].unlink()

    def _get_marked_tests_status(
        self, marked_tests_cache: Dict[int, _MarkedTestsStatus], instance_num: int
    ) -> _MarkedTestsStatus:
        """Return marked tests status for cluster instance."""
        if instance_num not in marked_tests_cache:
            marked_tests_cache[instance_num] = _MarkedTestsStatus()
        marked_tests_status: _MarkedTestsStatus = marked_tests_cache[instance_num]
        return marked_tests_status

    def _update_marked_tests(
        self,
        marked_tests_cache: Dict[int, _MarkedTestsStatus],
        cget_status: _ClusterGetStatus,
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
            marked_tests_cache=marked_tests_cache, instance_num=cget_status.instance_num
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
            self.log(
                f"c{instance_num}: no marked tests running for a while, "
                "cleaning the mark status file"
            )
            self._on_marked_test_stop(instance_num)

    def _resolve_resources_availability(self, cget_status: _ClusterGetStatus) -> bool:
        """Resolve availability of required "use" and "lock" resources."""
        resources_locked = common._get_resources_from_paths(
            paths=cget_status.instance_dir.glob(f"{common.RESOURCE_LOCKED_GLOB}_*")
        )

        # this test wants to lock some resources, check if these are not in use
        res_lockable = []
        if cget_status.lock_resources:
            resources_used = common._get_resources_from_paths(
                paths=cget_status.instance_dir.glob(f"{common.RESOURCE_IN_USE_GLOB}_*")
            )
            unlockable_resources = {*resources_locked, *resources_used}
            res_lockable = resources_management.get_resources(
                resources=cget_status.lock_resources,
                unavailable=unlockable_resources,
            )
            if not res_lockable:
                self.log(
                    f"c{cget_status.instance_num}: want to lock '{cget_status.lock_resources}' and "
                    f"'{unlockable_resources}' are unavailable, cannot start"
                )
                return False

        # this test wants to use some resources, check if these are not locked
        res_usable = []
        if cget_status.use_resources:
            res_usable = resources_management.get_resources(
                resources=cget_status.use_resources,
                unavailable=resources_locked,
            )
            if not res_usable:
                self.log(
                    f"c{cget_status.instance_num}: want to use '{cget_status.use_resources}' and "
                    f"'{resources_locked}' are locked, cannot start"
                )
                return False

        # resources that are locked are also in use
        use_minus_lock = list(set(res_usable) - set(res_lockable))

        cget_status.final_use_resources = use_minus_lock
        cget_status.final_lock_resources = res_lockable

        self.log(
            f"c{cget_status.instance_num}: can start, resources '{use_minus_lock}' usable, "
            f"resources '{res_lockable}' lockable"
        )

        return True

    def _is_already_running(self, cget_status: _ClusterGetStatus) -> bool:
        """Check if the test is already setup and running."""
        test_on_worker = list(
            self.pytest_tmp_dir.glob(
                f"{common.CLUSTER_DIR_TEMPLATE}*/{common.TEST_RUNNING_GLOB}_{self.worker_id}"
            )
        )

        # test is already running, nothing to set up
        if cget_status.first_iteration and test_on_worker and self._cluster_instance_num != -1:
            self.log(f"{test_on_worker[0]} already exists")
            return True

        return False

    def _restarted_by_other_worker(self, cget_status: _ClusterGetStatus) -> bool:
        """Check if the cluster is currently being restarted by worker other than this one."""
        if cget_status.restart_here:
            return False

        restart_in_progress = list(
            cget_status.instance_dir.glob(f"{common.RESTART_IN_PROGRESS_GLOB}_*")
        )
        if restart_in_progress:
            # no log message here, it would be too many of them
            return True

        return False

    def _marked_select_instance(self, cget_status: _ClusterGetStatus) -> bool:
        """Select this cluster instance for running marked tests if possible."""
        marked_running_my_here = list(
            cget_status.instance_dir.glob(f"{common.TEST_CURR_MARK_GLOB}_@@{cget_status.mark}@@_*")
        )
        marked_running_my_anywhere = list(
            self.pytest_tmp_dir.glob(
                f"{common.CLUSTER_DIR_TEMPLATE}*/"
                f"{common.TEST_CURR_MARK_GLOB}_@@{cget_status.mark}@@_*"
            )
        )
        if not marked_running_my_here and marked_running_my_anywhere:
            self.log(
                f"c{cget_status.instance_num}: tests marked with my mark '{cget_status.mark}' "
                "already running on other cluster instance, cannot run"
            )
            return False

        marked_starting_my_here = list(
            cget_status.instance_dir.glob(
                f"{common.TEST_MARK_STARTING_GLOB}_@@{cget_status.mark}@@_*"
            )
        )
        marked_starting_my_anywhere = list(
            self.pytest_tmp_dir.glob(
                f"{common.CLUSTER_DIR_TEMPLATE}*/"
                f"{common.TEST_MARK_STARTING_GLOB}_@@{cget_status.mark}@@_*"
            )
        )
        if not marked_starting_my_here and marked_starting_my_anywhere:
            self.log(
                f"c{cget_status.instance_num}: tests marked with my mark '{cget_status.mark}' "
                "starting on other cluster instance, cannot run"
            )
            return False

        if marked_running_my_here or marked_starting_my_here:
            cget_status.selected_instance = cget_status.instance_num
            self.log(
                f"c{cget_status.instance_num}: locking to this cluster instance, "
                f"it has my mark '{cget_status.mark}'"
            )
        elif cget_status.marked_running_sfiles or cget_status.marked_starting_sfiles:
            self.log(
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
                / f"{common.TEST_MARK_STARTING_GLOB}_@@{cget_status.mark}@@_{self.worker_id}"
            )
            if not mark_starting_file.exists():
                self.log(f"c{cget_status.instance_num}: initialized mark '{cget_status.mark}'")
                helpers.touch(mark_starting_file)

        return True

    def _cleanup_dead_clusters(self, cget_status: _ClusterGetStatus) -> None:
        """Cleanup if the selected cluster instance failed to start."""
        # move on to other cluster instance
        cget_status.selected_instance = -1
        cget_status.restart_here = False
        cget_status.restart_ready = False

        # remove status files that are checked by other workers
        for sf in (
            *cget_status.instance_dir.glob(f"{common.TEST_CURR_MARK_GLOB}_*"),
            *cget_status.instance_dir.glob(f"{common.TEST_MARK_STARTING_GLOB}_*"),
        ):
            sf.unlink()

        dead_clusters = list(
            self.pytest_tmp_dir.glob(f"{common.CLUSTER_DIR_TEMPLATE}*/{common.CLUSTER_DEAD_FILE}")
        )
        if len(dead_clusters) == self.num_of_instances:
            raise RuntimeError("All clusters are dead, cannot run.")

    def _init_restart(self, cget_status: _ClusterGetStatus) -> bool:
        """Initialize restart of this cluster instance on this worker."""
        # restart already initialized
        if cget_status.restart_here:
            return True

        # restart is needed when custom start command was specified and the test is marked test or
        # singleton
        initial_marked_test = bool(cget_status.mark and not cget_status.marked_running_sfiles)
        singleton_test = resources.Resources.CLUSTER in cget_status.lock_resources
        new_cmd_restart = bool(cget_status.start_cmd and (initial_marked_test or singleton_test))
        will_restart = new_cmd_restart or self._is_restart_needed(cget_status.instance_num)
        if not will_restart:
            return True

        # if tests are running on the instance, we cannot restart, therefore we cannot continue
        if cget_status.started_tests_sfiles:
            self.log(f"c{cget_status.instance_num}: tests are running, cannot restart")
            return False

        self.log(f"c{cget_status.instance_num}: setting 'restart in progress'")

        # Cluster restart will be performed by this worker.
        # By setting `restart_here`, we make sure this worker continue on this cluster instance
        # after restart is finished. It is important because the `start_cmd` used for starting the
        # cluster instance might be specific for the test.
        cget_status.restart_here = True
        cget_status.selected_instance = cget_status.instance_num

        restart_in_progress_file = (
            cget_status.instance_dir / f"{common.RESTART_IN_PROGRESS_GLOB}_{self.worker_id}"
        )
        if not restart_in_progress_file.exists():
            helpers.touch(restart_in_progress_file)

        return True

    def _finish_restart(self, cget_status: _ClusterGetStatus) -> bool:
        """On first call, setup cluster instance for restart. On second call, perform cleanup."""
        if not cget_status.restart_here:
            return True

        if cget_status.restart_ready:
            # The cluster was already restarted if we are here and `restart_ready` is still True.
            # If that's the case, do cleanup.
            cget_status.restart_ready = False
            cget_status.restart_here = False

            # remove status files that are no longer valid after restart
            for f in cget_status.instance_dir.glob(f"{common.RESTART_IN_PROGRESS_GLOB}_*"):
                f.unlink()
            for f in cget_status.instance_dir.glob(f"{common.RESTART_NEEDED_GLOB}_*"):
                f.unlink()
            return True

        # NOTE: when `_restart` is called, the env variables needed for cluster start scripts need
        # to be already set (e.g. CARDANO_NODE_SOCKET_PATH)
        self.log(f"c{cget_status.instance_num}: ready to restart cluster")
        # the actual `_restart` function will be called outside of global lock so other workers
        # don't need to wait
        cget_status.restart_ready = True
        return False

    def _create_test_status_files(self, cget_status: _ClusterGetStatus) -> None:
        """Create status files for test that is about to start on this cluster instance."""
        # this test is a first marked test
        if cget_status.mark and not cget_status.marked_running_sfiles:
            self.log(f"c{cget_status.instance_num}: starting '{cget_status.mark}' tests")
            helpers.touch(
                self.instance_dir
                / f"{common.TEST_CURR_MARK_GLOB}_@@{cget_status.mark}@@_{self.worker_id}"
            )
            for sf in cget_status.marked_starting_sfiles:
                sf.unlink()

        # create status file for each in-use resource
        for r in cget_status.final_use_resources:
            helpers.touch(
                self.instance_dir / f"{common.RESOURCE_IN_USE_GLOB}_@@{r}@@_{self.worker_id}"
            )

        # create status file for each locked resource
        for r in cget_status.final_lock_resources:
            helpers.touch(
                self.instance_dir / f"{common.RESOURCE_LOCKED_GLOB}_@@{r}@@_{self.worker_id}"
            )

        # cleanup = cluster restart after test (group of tests) is finished
        if cget_status.cleanup:
            # cleanup after group of test that are marked with a marker
            if cget_status.mark:
                self.log(f"c{cget_status.instance_num}: cleanup and mark")
                helpers.touch(
                    self.instance_dir / f"{common.RESTART_AFTER_MARK_GLOB}_{self.worker_id}"
                )
            # cleanup after single test (e.g. singleton)
            else:
                self.log(f"c{cget_status.instance_num}: cleanup and not mark")
                helpers.touch(self.instance_dir / f"{common.RESTART_NEEDED_GLOB}_{self.worker_id}")

        self.log(f"c{self.cluster_instance_num}: creating 'test running' status file")
        test_running_file = self.instance_dir / f"{common.TEST_RUNNING_GLOB}_{self.worker_id}"
        helpers.touch(test_running_file)

    def _init_use_resources(
        self,
        lock_resources: resources_management.ResourcesType,
        use_resources: resources_management.ResourcesType,
    ) -> resources_management.ResourcesType:
        """Add `resources.Resources.CLUSTER` to `use_resources`.

        Filter out `lock_resources` from the list of `use_resources`.
        """
        lock_named = {r for r in lock_resources if isinstance(r, str)}

        use_named = {r for r in use_resources if isinstance(r, str)}
        use_w_filter = [r for r in use_resources if not isinstance(r, str)]

        use_named.add(resources.Resources.CLUSTER)

        use_minus_lock = use_named - lock_named
        use_resources = [*use_minus_lock, *use_w_filter]

        return use_resources

    def get_cluster_instance(  # noqa: C901
        self,
        mark: str = "",
        lock_resources: resources_management.ResourcesType = (),
        use_resources: resources_management.ResourcesType = (),
        cleanup: bool = False,
        start_cmd: str = "",
    ) -> int:
        """Return a number of initialized cluster instance once we can start the test.

        It checks current conditions and waits if the conditions don't allow to start the test
        right away.
        """
        # pylint: disable=too-many-statements,too-many-branches
        assert not isinstance(lock_resources, str), "`lock_resources` can't be single string"
        assert not isinstance(use_resources, str), "`use_resources` can't be single string"

        if configuration.DEV_CLUSTER_RUNNING:
            if start_cmd:
                LOGGER.warning(
                    f"Ignoring the '{start_cmd}' cluster start command as "
                    "'DEV_CLUSTER_RUNNING' is set."
                )
            # check if the development cluster instance is ready by now so we don't need to obtain
            # cluster lock when it is not necessary
            if not self._is_dev_cluster_ready():
                with locking.FileLockIfXdist(self.cluster_lock):
                    self._setup_dev_cluster()

        if configuration.FORBID_RESTART and start_cmd:
            raise RuntimeError("Cannot use custom start command when 'FORBID_RESTART' is set.")

        if start_cmd:
            if not (mark or (resources.Resources.CLUSTER in lock_resources)):
                raise RuntimeError(
                    "Custom start command can be used only together with singleton or `mark`."
                )
            # always clean after test(s) that started cluster with custom configuration
            cleanup = True

        use_resources = self._init_use_resources(
            lock_resources=lock_resources, use_resources=use_resources
        )

        cget_status = _ClusterGetStatus(
            mark=mark,
            lock_resources=lock_resources,
            use_resources=use_resources,
            cleanup=cleanup,
            start_cmd=start_cmd,
            current_test=os.environ.get("PYTEST_CURRENT_TEST") or "",
        )
        marked_tests_cache: Dict[int, _MarkedTestsStatus] = {}

        self.log(f"want to run test '{cget_status.current_test}'")

        # iterate until it is possible to start the test
        while True:
            if cget_status.restart_ready:
                self._restart(start_cmd=start_cmd)

            if not cget_status.first_iteration:
                _xdist_sleep(random.uniform(0.6, 1.2) * cget_status.sleep_delay)

            # nothing time consuming can go under this lock as all other workers will need to wait
            with locking.FileLockIfXdist(self.cluster_lock):
                if self._is_already_running(cget_status):
                    return self.cluster_instance_num

                # needs to be set here, before the first `continue`
                cget_status.first_iteration = False
                self._cluster_instance_num = -1

                # try all existing cluster instances
                for instance_num in range(self.num_of_instances):
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
                        self.pytest_tmp_dir / f"{common.CLUSTER_DIR_TEMPLATE}{instance_num}"
                    )
                    cget_status.instance_dir.mkdir(exist_ok=True)

                    # cleanup cluster instance where attempt to start cluster failed repeatedly
                    if (cget_status.instance_dir / common.CLUSTER_DEAD_FILE).exists():
                        self._cleanup_dead_clusters(cget_status)
                        continue

                    # cluster restart planned or in progress, so no new tests can start
                    if self._restarted_by_other_worker(cget_status):
                        cget_status.sleep_delay = 5
                        continue

                    # are there tests already running on this cluster instance?
                    cget_status.started_tests_sfiles = list(
                        cget_status.instance_dir.glob(f"{common.TEST_RUNNING_GLOB}_*")
                    )

                    # "marked tests" = group of tests marked with a specific mark.
                    # While these tests are running, no unmarked test can start.
                    cget_status.marked_starting_sfiles = list(
                        cget_status.instance_dir.glob(f"{common.TEST_MARK_STARTING_GLOB}_*")
                    )
                    cget_status.marked_running_sfiles = list(
                        cget_status.instance_dir.glob(f"{common.TEST_CURR_MARK_GLOB}_*")
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

                        self.log(
                            f"c{instance_num}: in marked tests branch, "
                            f"I have required mark '{mark}'"
                        )

                    # no unmarked test can run while marked tests are starting or running
                    elif cget_status.marked_running_sfiles or cget_status.marked_starting_sfiles:
                        self.log(
                            f"c{instance_num}: marked tests starting or running, "
                            f"I don't have mark"
                        )
                        cget_status.sleep_delay = 2
                        continue

                    # check availability of the required resources
                    if not self._resolve_resources_availability(cget_status):
                        cget_status.sleep_delay = 5
                        continue

                    # if restart is needed, indicate that the cluster will be restarted
                    # (after all currently running tests are finished)
                    if not self._init_restart(cget_status):
                        continue

                    # we've found suitable cluster instance
                    cget_status.selected_instance = instance_num
                    self._cluster_instance_num = instance_num
                    self.log(f"c{instance_num}: can run test '{cget_status.current_test}'")
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

                # cluster instance is ready, we can start the test
                break

        return instance_num
