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

    def _xdist_sleep(secs: float) -> None:  # noqa: ARG001
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
        line_p = line.replace("  ", " ").strip()
        pid = line_p.split()[-1].split("/")[0]
        os.kill(int(pid), 15)
        return


@dataclasses.dataclass
class _ClusterGetStatus:
    """Intermediate status while trying to `get` suitable cluster instance."""

    mark: str
    lock_resources: resources_management.ResourcesType
    use_resources: resources_management.ResourcesType
    prio: bool
    cleanup: bool
    start_cmd: str
    current_test: str
    selected_instance: int = -1
    instance_num: int = -1
    sleep_delay: int = 0
    respin_here: bool = False
    respin_ready: bool = False
    cluster_needs_respin: bool = False
    prio_here: bool = False
    tried_all_instances: bool = False
    instance_dir: Path = Path("/nonexistent")
    final_lock_resources: Iterable[str] = ()
    final_use_resources: Iterable[str] = ()
    # status files
    started_tests_sfiles: Iterable[Path] = ()
    marked_ready_sfiles: Iterable[Path] = ()
    marked_running_my_anywhere: Iterable[Path] = ()


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

    def _respin(self, start_cmd: str = "", stop_cmd: str = "") -> bool:  # noqa: C901
        """Respin cluster.

        Not called under global lock!
        """
        # pylint: disable=too-many-branches,too-many-statements
        cluster_running_file = self.instance_dir / common.CLUSTER_RUNNING_FILE

        # don't respin cluster if it was started outside of test framework
        if configuration.DEV_CLUSTER_RUNNING:
            self.log(f"c{self.cluster_instance_num}: ignoring respin, dev cluster is running")
            if cluster_running_file.exists():
                LOGGER.warning("Ignoring requested cluster respin as 'DEV_CLUSTER_RUNNING' is set.")
            else:
                cluster_running_file.touch()
            return True

        # fail if cluster respin is forbidden and the cluster was already started
        if configuration.FORBID_RESTART and cluster_running_file.exists():
            raise RuntimeError("Cannot respin cluster when 'FORBID_RESTART' is set.")

        self.log(
            f"c{self.cluster_instance_num}: called `_respin`, start_cmd='{start_cmd}', "
            f"stop_cmd='{stop_cmd}'"
        )

        state_dir = cluster_nodes.get_cluster_env().state_dir

        if state_dir.exists() and not (state_dir / common.CLUSTER_STARTED_BY_FRAMEWORK).exists():
            self.log(
                f"c{self.cluster_instance_num}: ERROR: state dir exists but cluster "
                "was not started by the framework"
            )
            raise RuntimeError("Cannot respin cluster when it was not started by the framework.")

        startup_files = cluster_nodes.get_cluster_type().cluster_scripts.prepare_scripts_files(
            destdir=self._create_startup_files_dir(self.cluster_instance_num),
            instance_num=self.cluster_instance_num,
            start_script=start_cmd,
            stop_script=stop_cmd,
        )

        self.log(
            f"c{self.cluster_instance_num}: in `_respin`, new files "
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
                    (state_dir / common.CLUSTER_STARTED_BY_FRAMEWORK).touch()
            # `else` cannot be used together with `finally`
            if _cluster_started:
                break
        else:
            self.log(
                f"c{self.cluster_instance_num}: failed to start cluster:\n{excp}\ncluster dead"
            )
            if not configuration.IS_XDIST:
                pytest.exit(msg=f"Failed to start cluster, exception: {excp}", returncode=1)
            (self.instance_dir / common.CLUSTER_DEAD_FILE).touch()
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
        try:
            cluster_nodes.setup_test_addrs(cluster_obj=cluster_obj, destination_dir=tmp_path)
        except Exception as err:
            self.log(
                f"c{self.cluster_instance_num}: failed to setup test addresses:\n{err}\n"
                "cluster dead"
            )
            if not configuration.IS_XDIST:
                pytest.exit(msg=f"Failed to setup test addresses, exception: {err}", returncode=1)
            (self.instance_dir / common.CLUSTER_DEAD_FILE).touch()
            return False

        # create file that indicates that the cluster is running
        if not cluster_running_file.exists():
            cluster_running_file.touch()

        return True

    def _is_dev_cluster_ready(self) -> bool:
        """Check if development cluster instance is ready to be used."""
        state_dir = cluster_nodes.get_cluster_env().state_dir
        if (state_dir / cluster_nodes.ADDRS_DATA).exists():
            return True
        return False

    def _setup_dev_cluster(self) -> None:
        """Set up cluster instance that was already started outside of test framework."""
        cluster_env = cluster_nodes.get_cluster_env()
        if (cluster_env.state_dir / cluster_nodes.ADDRS_DATA).exists():
            return

        self.log(f"c{cluster_env.instance_num}: setting up dev cluster")

        # Create "addrs_data" directly in the cluster state dir, so it can be reused
        # (in normal non-`DEV_CLUSTER_RUNNING` setup we want "addrs_data" stored among
        # tests artifacts, so it can be used during cleanup etc.).
        tmp_path = cluster_env.state_dir / "addrs_data"
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

    def _cluster_needs_respin(self, instance_num: int) -> bool:
        """Check if it is necessary to respin cluster."""
        instance_dir = self.pytest_tmp_dir / f"{common.CLUSTER_DIR_TEMPLATE}{instance_num}"
        # if cluster instance is not started yet
        if not (instance_dir / common.CLUSTER_RUNNING_FILE).exists():
            return True

        # if it was indicated that the cluster instance needs to be respun
        if list(instance_dir.glob(f"{common.RESPIN_NEEDED_GLOB}_*")):
            return True

        # If a service failed on cluster instance.
        # Check only if we are really able to restart the cluster instance, because the check
        # is expensive.
        if not (configuration.FORBID_RESTART or self._is_healthy(instance_num)):
            return True

        return False

    def _test_needs_respin(self, cget_status: _ClusterGetStatus) -> bool:
        """Check if it is necessary to respin cluster for the test."""
        # if this is non-initial marked test, we can ignore custom start command,
        # as it was handled by the initial marked test
        noninitial_marked_test = cget_status.mark and cget_status.marked_ready_sfiles
        if noninitial_marked_test:
            return False

        # respin is needed when custom start command was specified
        if cget_status.start_cmd:
            return True

        return False

    def _on_marked_test_stop(self, instance_num: int, mark: str) -> None:
        """Perform actions after all marked tests are finished."""
        self.log(f"c{instance_num}: in `_on_marked_test_stop`")
        instance_dir = self.pytest_tmp_dir / f"{common.CLUSTER_DIR_TEMPLATE}{instance_num}"

        # set cluster instance to be respun if needed
        respin_after_mark_files = list(
            instance_dir.glob(f"{common.RESPIN_AFTER_MARK_GLOB}_@@{mark}@@_*")
        )
        if respin_after_mark_files:
            for f in respin_after_mark_files:
                f.unlink()
            self.log(f"c{instance_num}: in `_on_marked_test_stop`, creating 'respin needed' file")
            (instance_dir / f"{common.RESPIN_NEEDED_GLOB}_{self.worker_id}").touch()

        # remove files that indicates that the mark is ready
        marked_ready_sfiles = instance_dir.glob(f"{common.TEST_CURR_MARK_GLOB}_@@{mark}@@_*")
        for f in marked_ready_sfiles:
            f.unlink()

        # remove file that indicates resources that are locked by the marked tests
        marked_lock_files = instance_dir.glob(f"{common.RESOURCE_LOCKED_GLOB}_*_%%{mark}%%_*")
        for f in marked_lock_files:
            f.unlink()

        # remove file that indicates resources that are in-use by the marked tests
        marked_use_files = instance_dir.glob(f"{common.RESOURCE_IN_USE_GLOB}_*_%%{mark}%%_*")
        for f in marked_use_files:
            f.unlink()

    def _get_marked_tests_status(
        self, marked_tests_cache: Dict[int, Dict[str, int]], instance_num: int
    ) -> Dict[str, int]:
        """Return marked tests status for cluster instance."""
        if instance_num not in marked_tests_cache:
            marked_tests_cache[instance_num] = {}
        marked_tests_status = marked_tests_cache[instance_num]
        return marked_tests_status

    def _update_marked_tests(
        self,
        marked_tests_cache: Dict[int, Dict[str, int]],
        cget_status: _ClusterGetStatus,
    ) -> None:
        """Update status about running of marked test.

        When marked test is finished, we can't clear the mark right away. There might be a test
        with the same mark in the queue and it will be scheduled in a short while. We would need
        to repeat all the expensive setup if we already cleared the mark. Therefore we need to
        keep track of marked tests and clear the mark and cluster instance only when no marked
        test was running for some time.
        """
        # no need to continue if there are no marked tests
        if not list(cget_status.instance_dir.glob(f"{common.TEST_CURR_MARK_GLOB}_*")):
            return

        # marked tests don't need to be running yet if the cluster is being respun
        respin_in_progress = list(
            cget_status.instance_dir.glob(f"{common.RESPIN_IN_PROGRESS_GLOB}_*")
        )
        if respin_in_progress:
            return

        # get marked tests status
        marked_tests_status = self._get_marked_tests_status(
            marked_tests_cache=marked_tests_cache, instance_num=cget_status.instance_num
        )

        # update marked tests status
        instance_num = cget_status.instance_num
        marks_in_progress = [
            f.name.split("@@")[1]
            for f in cget_status.instance_dir.glob(f"{common.TEST_RUNNING_GLOB}_@@*")
        ]

        for m in marks_in_progress:
            marked_tests_status[m] = 0

        for m in marked_tests_status:
            marked_tests_status[m] += 1

            # clean the stale status files if we are waiting too long for the next marked test
            if marked_tests_status[m] >= 20:
                self.log(
                    f"c{instance_num}: no marked tests running for a while, "
                    "cleaning the mark status file"
                )
                self._on_marked_test_stop(instance_num=instance_num, mark=m)

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

    def _is_already_running(self) -> bool:
        """Check if the test is already setup and running."""
        test_on_worker = list(
            self.pytest_tmp_dir.glob(
                f"{common.CLUSTER_DIR_TEMPLATE}*/{common.TEST_RUNNING_GLOB}*_{self.worker_id}"
            )
        )

        # test is already running, nothing to set up
        if test_on_worker and self._cluster_instance_num != -1:
            self.log(f"{test_on_worker[0]} already exists")
            return True

        return False

    def _wait_for_prio(self, cget_status: _ClusterGetStatus) -> bool:
        """Check if there is a priority test waiting for cluster instance."""
        # A "prio" test has priority in obtaining cluster instance. Non-priority
        # tests can continue with their setup only if they are already locked to a
        # cluster instance.
        if not (
            cget_status.prio_here
            or cget_status.selected_instance != -1
            or cget_status.marked_running_my_anywhere
        ) and list(self.pytest_tmp_dir.glob(f"{common.PRIO_IN_PROGRESS_GLOB}_*")):
            self.log("'prio' test setup in progress, cannot continue")
            return True

        return False

    def _init_prio(self, cget_status: _ClusterGetStatus) -> None:
        """Set "prio" for this test if indicated."""
        if not cget_status.prio:
            return

        prio_status_file = self.pytest_tmp_dir / f"{common.PRIO_IN_PROGRESS_GLOB}_{self.worker_id}"
        if not prio_status_file.exists():
            prio_status_file.touch()
        cget_status.prio_here = True
        self.log(f"setting 'prio' for '{cget_status.current_test}'")

    def _respun_by_other_worker(self, cget_status: _ClusterGetStatus) -> bool:
        """Check if the cluster is currently being respun by worker other than this one."""
        if cget_status.respin_here:
            return False

        respin_in_progress = list(
            cget_status.instance_dir.glob(f"{common.RESPIN_IN_PROGRESS_GLOB}_*")
        )
        if respin_in_progress:
            # no log message here, it would be too many of them
            return True

        return False

    def _marked_select_instance(self, cget_status: _ClusterGetStatus) -> bool:
        """Select this cluster instance for running marked tests if possible."""
        if cget_status.marked_ready_sfiles:
            cget_status.selected_instance = cget_status.instance_num
            self.log(
                f"c{cget_status.instance_num}: locking to this cluster instance, "
                f"it has my mark '{cget_status.mark}'"
            )
            return True

        if cget_status.marked_running_my_anywhere:
            self.log(
                f"c{cget_status.instance_num}: tests marked with my mark '{cget_status.mark}' "
                "already running on other cluster instance, cannot run"
            )
            return False

        # if here, this will be the first test with the mark
        return True

    def _fail_on_all_dead(self) -> None:
        """Fail if all cluster instances are dead."""
        dead_clusters = list(
            self.pytest_tmp_dir.glob(f"{common.CLUSTER_DIR_TEMPLATE}*/{common.CLUSTER_DEAD_FILE}")
        )
        if len(dead_clusters) == self.num_of_instances:
            raise RuntimeError("All clusters are dead, cannot run.")

    def _cleanup_dead_clusters(self, cget_status: _ClusterGetStatus) -> None:
        """Cleanup if the selected cluster instance failed to start."""
        # move on to other cluster instance
        cget_status.selected_instance = -1
        cget_status.respin_here = False
        cget_status.respin_ready = False

        # remove status files that are checked by other workers
        for sf in cget_status.instance_dir.glob(f"{common.TEST_CURR_MARK_GLOB}_*"):
            sf.unlink()

    def _init_respin(self, cget_status: _ClusterGetStatus) -> bool:
        """Initialize respin of this cluster instance on this worker."""
        # respin already initialized
        if cget_status.respin_here:
            return True

        if not (cget_status.cluster_needs_respin or self._test_needs_respin(cget_status)):
            return True

        # if tests are running on the instance, we cannot respin, therefore we cannot continue
        if cget_status.started_tests_sfiles:
            self.log(f"c{cget_status.instance_num}: tests are running, cannot respin")
            return False

        self.log(f"c{cget_status.instance_num}: setting 'respin in progress'")

        # Cluster respin will be performed by this worker.
        # By setting `respin_here`, we make sure this worker continue on this cluster instance
        # after respin is finished. It is important because the `start_cmd` used for starting the
        # cluster instance might be specific for the test.
        cget_status.respin_here = True
        cget_status.selected_instance = cget_status.instance_num

        respin_in_progress_file = (
            cget_status.instance_dir / f"{common.RESPIN_IN_PROGRESS_GLOB}_{self.worker_id}"
        )
        if not respin_in_progress_file.exists():
            respin_in_progress_file.touch()

        # remove mark status files as these will not be valid after respin
        for f in cget_status.instance_dir.glob(f"{common.TEST_CURR_MARK_GLOB}_*"):
            f.unlink()

        return True

    def _finish_respin(self, cget_status: _ClusterGetStatus) -> bool:
        """On first call, setup cluster instance for respin. On second call, perform cleanup."""
        if not cget_status.respin_here:
            return True

        if cget_status.respin_ready:
            # The cluster was already respined if we are here and `respin_ready` is still True.
            # If that's the case, do cleanup.
            cget_status.respin_ready = False
            cget_status.respin_here = False

            # remove status files that are no longer valid after respin
            for f in cget_status.instance_dir.glob(f"{common.RESPIN_IN_PROGRESS_GLOB}_*"):
                f.unlink()
            for f in cget_status.instance_dir.glob(f"{common.RESPIN_NEEDED_GLOB}_*"):
                f.unlink()
            return True

        # NOTE: when `_respin` is called, the env variables needed for cluster start scripts need
        # to be already set (e.g. CARDANO_NODE_SOCKET_PATH)
        self.log(f"c{cget_status.instance_num}: ready to respin cluster")
        # the actual `_respin` function will be called outside of global lock so other workers
        # don't need to wait
        cget_status.respin_ready = True
        return False

    def _init_marked_test(self, cget_status: _ClusterGetStatus) -> None:
        """Create status file for marked test."""
        if not cget_status.mark:
            return

        self.log(f"c{cget_status.instance_num}: starting '{cget_status.mark}' test")
        (
            self.instance_dir
            / f"{common.TEST_CURR_MARK_GLOB}_@@{cget_status.mark}@@_{self.worker_id}"
        ).touch()

    def _create_test_status_files(self, cget_status: _ClusterGetStatus) -> None:
        """Create status files for test that is about to start on this cluster instance."""
        mark_res_str = f"_%%{cget_status.mark}%%" if cget_status.mark else ""

        # create status file for each in-use resource
        for r in cget_status.final_use_resources:
            (
                self.instance_dir
                / f"{common.RESOURCE_IN_USE_GLOB}_@@{r}@@{mark_res_str}_{self.worker_id}"
            ).touch()

        # create status file for each locked resource
        for r in cget_status.final_lock_resources:
            (
                self.instance_dir
                / f"{common.RESOURCE_LOCKED_GLOB}_@@{r}@@{mark_res_str}_{self.worker_id}"
            ).touch()

        # cleanup = cluster respin after test (group of tests) is finished
        if cget_status.cleanup:
            # cleanup after group of test that are marked with a marker
            if cget_status.mark:
                self.log(f"c{cget_status.instance_num}: cleanup and mark")
                (
                    self.instance_dir
                    / f"{common.RESPIN_AFTER_MARK_GLOB}_@@{cget_status.mark}@@_{self.worker_id}"
                ).touch()
            # cleanup after single test (e.g. singleton)
            else:
                self.log(f"c{cget_status.instance_num}: cleanup and not mark")
                (self.instance_dir / f"{common.RESPIN_NEEDED_GLOB}_{self.worker_id}").touch()

        self.log(f"c{self.cluster_instance_num}: creating 'test running' status file")
        mark_run_str = f"_@@{cget_status.mark}@@" if cget_status.mark else ""
        test_running_file = (
            self.instance_dir / f"{common.TEST_RUNNING_GLOB}{mark_run_str}_{self.worker_id}"
        )
        # write the name of the test that is starting on this cluster instance, leave out the
        # '(setup)' part
        test_running_file.write_text(cget_status.current_test.split(" ")[0])

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
        prio: bool = False,
        cleanup: bool = False,
        start_cmd: str = "",
    ) -> int:
        """Return a number of initialized cluster instance once we can start the test.

        It checks current conditions and waits if the conditions don't allow to start the test
        right away.

        Args:
            mark: A string marking group of tests. Useful when group of tests need the same
                expensive setup. The `mark` will make sure the marked tests run on the same
                cluster instance.
            lock_resources: An iterable of resources (names of resources) that will be used
                exclusively by the test (or marked group of tests). A locked resource cannot be used
                by other tests.
            use_resources: An iterable of resources (names of resources) that will be used
                by the test (or marked group of tests). The resources can be shared with other
                tests, however resources in use cannot be locked by other tests.
            prio: A boolean indicating that the test has priority in obtaining cluster instance.
                All other tests that also want to get a cluster instance need to wait.
            cleanup: A boolean indicating if the cluster will be respun after the test (or marked
                group of tests) is finished. Can be used only for tests that locked whole cluster
                ("singleton" tests).
            start_cmd: Custom command to start the cluster.
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

            available_instances = [cluster_nodes.get_cluster_env().instance_num]
        else:
            available_instances = list(range(self.num_of_instances))

        if configuration.FORBID_RESTART and start_cmd:
            raise RuntimeError("Cannot use custom start command when 'FORBID_RESTART' is set.")

        if start_cmd:
            if resources.Resources.CLUSTER not in lock_resources:
                raise RuntimeError("Custom start command can be used only together with singleton.")
            # always clean after test(s) that started cluster with custom configuration
            cleanup = True

        use_resources = self._init_use_resources(
            lock_resources=lock_resources, use_resources=use_resources
        )

        cget_status = _ClusterGetStatus(
            mark=mark,
            lock_resources=lock_resources,
            use_resources=use_resources,
            prio=prio,
            cleanup=cleanup,
            start_cmd=start_cmd,
            current_test=os.environ.get("PYTEST_CURRENT_TEST") or "",
        )
        marked_tests_cache: Dict[int, Dict[str, int]] = {}

        self.log(f"want to run test '{cget_status.current_test}'")

        # iterate until it is possible to start the test
        while True:
            if cget_status.respin_ready:
                self._respin(start_cmd=start_cmd)

            # sleep for a while to avoid too many checks in a short time
            _xdist_sleep(random.uniform(0.6, 1.2) * cget_status.sleep_delay)
            # pylint: disable=consider-using-max-builtin
            if cget_status.sleep_delay < 1:
                cget_status.sleep_delay = 1

            # nothing time consuming can go under this lock as all other workers will need to wait
            with locking.FileLockIfXdist(self.cluster_lock):
                if self._is_already_running():
                    return self.cluster_instance_num

                # fail if all cluster instances are dead
                self._fail_on_all_dead()

                if mark:
                    # check if tests with my mark are already locked to any cluster instance
                    cget_status.marked_running_my_anywhere = list(
                        self.pytest_tmp_dir.glob(
                            f"{common.CLUSTER_DIR_TEMPLATE}*/"
                            f"{common.TEST_CURR_MARK_GLOB}_@@{mark}@@_*"
                        )
                    )

                # A "prio" test has priority in obtaining cluster instance. Check if it is needed
                # to wait until earlier "prio" test obtains a cluster instance.
                if self._wait_for_prio(cget_status):
                    cget_status.sleep_delay = 5
                    continue

                # set "prio" for this test if indicated
                self._init_prio(cget_status)

                self._cluster_instance_num = -1

                # try all existing cluster instances; randomize the order
                for instance_num in random.sample(available_instances, k=self.num_of_instances):
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

                    # cluster respin planned or in progress, so no new tests can start
                    if self._respun_by_other_worker(cget_status):
                        cget_status.sleep_delay = 5
                        continue

                    # are there tests already running on this cluster instance?
                    cget_status.started_tests_sfiles = list(
                        cget_status.instance_dir.glob(f"{common.TEST_RUNNING_GLOB}_*")
                    )

                    # Does the cluster instance needs respin to continue?
                    # Cache the result as the check itself can be expensive.
                    cget_status.cluster_needs_respin = self._cluster_needs_respin(instance_num)

                    # "marked tests" = group of tests marked with my mark
                    cget_status.marked_ready_sfiles = list(
                        cget_status.instance_dir.glob(f"{common.TEST_CURR_MARK_GLOB}_@@{mark}@@_*")
                    )

                    # if marked tests are already running, update their status
                    self._update_marked_tests(
                        marked_tests_cache=marked_tests_cache, cget_status=cget_status
                    )

                    # select this instance for running marked tests if possible
                    if mark and not self._marked_select_instance(cget_status):
                        cget_status.sleep_delay = 2
                        continue

                    # Try next cluster instance when the current one needs respin.
                    # Respin only if:
                    # * we are already locked on this instance
                    # * respin is needed by the current test anyway
                    # * we already tried all cluster instances and there's no other option
                    if (
                        cget_status.cluster_needs_respin
                        and cget_status.selected_instance != instance_num
                        and not self._test_needs_respin(cget_status)
                        and not cget_status.tried_all_instances
                    ):
                        continue

                    # We don't need to resolve resources availability if there was already a test
                    # with this mark before (the first test already resolved the resources
                    # availability).
                    # It is responsibility of tests to make sure that the same resources are
                    # requested for all the tests with the same mark (e.g. specific pool and
                    # not "any pool").
                    need_resolve_resources = not cget_status.marked_ready_sfiles

                    # check availability of the required resources
                    if need_resolve_resources and not self._resolve_resources_availability(
                        cget_status
                    ):
                        cget_status.sleep_delay = 5
                        continue

                    # if respin is needed, indicate that the cluster will be re-spun
                    # (after all currently running tests are finished)
                    if not self._init_respin(cget_status):
                        continue

                    # we've found suitable cluster instance
                    cget_status.selected_instance = instance_num
                    self._cluster_instance_num = instance_num
                    self.log(f"c{instance_num}: can run test '{cget_status.current_test}'")
                    # set environment variables that are needed when respinning the cluster
                    # and running tests
                    cluster_nodes.set_cluster_env(instance_num)

                    # remove "prio" status file
                    if prio:
                        (
                            self.pytest_tmp_dir / f"{common.PRIO_IN_PROGRESS_GLOB}_{self.worker_id}"
                        ).unlink(missing_ok=True)

                    # Create status file for marked tests.
                    # This must be done before the cluster is re-spun, so that other marked tests
                    # don't try to prepare another cluster instance.
                    self._init_marked_test(cget_status)

                    # if needed, finish respin related actions
                    if not self._finish_respin(cget_status):
                        continue

                    # from this point on, all conditions needed to start the test are met
                    break
                else:
                    # if the test cannot start on any instance, return to top-level loop
                    cget_status.tried_all_instances = True
                    continue

                self._create_test_status_files(cget_status)

                # cluster instance is ready, we can start the test
                break

        return instance_num
