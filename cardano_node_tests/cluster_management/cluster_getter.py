"""Functionality for obtaining and setting up a cluster instance."""

import dataclasses
import logging
import os
import pathlib as pl
import random
import shutil
import time
import typing as tp

import pytest
from _pytest.config import Config
from cardonnay import local_scripts as cardonnay_local

from cardano_node_tests.cluster_management import common
from cardano_node_tests.cluster_management import netstat_tools
from cardano_node_tests.cluster_management import resources
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.cluster_management import status_files
from cardano_node_tests.utils import artifacts
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import framework_log
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools
from cardano_node_tests.utils import types as ttypes

LOGGER = logging.getLogger(__name__)

if configuration.IS_XDIST:
    _xdist_sleep = time.sleep
else:

    def _xdist_sleep(
        secs: float,
    ) -> None:
        """No need to sleep if tests are running on a single worker."""


@dataclasses.dataclass
class _ClusterGetStatus:
    """Intermediate status while trying to `get` suitable cluster instance."""

    mark: str
    lock_resources: resources_management.ResourcesType
    use_resources: resources_management.ResourcesType
    prio: bool
    cleanup: bool
    scriptsdir: ttypes.FileType
    current_test: str
    selected_instance: int = -1
    instance_num: int = -1
    sleep_delay: int = 0
    respin_here: bool = False
    respin_ready: bool = False
    cluster_needs_respin: bool = False
    prio_here: bool = False
    tried_all_instances: bool = False
    instance_dir: pl.Path = pl.Path("/nonexistent")
    final_lock_resources: tp.Iterable[str] = ()
    final_use_resources: tp.Iterable[str] = ()
    # Status files
    started_tests_sfiles: tp.Iterable[pl.Path] = ()
    marked_ready_sfiles: tp.Iterable[pl.Path] = ()
    marked_running_my_anywhere: tp.Iterable[pl.Path] = ()


class ClusterGetter:
    """Internal class that encapsulate functionality for getting a cluster instance."""

    def __init__(
        self,
        worker_id: str,
        pytest_config: Config,
        num_of_instances: int,
        log_func: tp.Callable,
    ) -> None:
        self.pytest_config = pytest_config
        self.worker_id = worker_id
        self.num_of_instances = num_of_instances
        self.log = log_func

        self.pytest_tmp_dir = temptools.get_pytest_root_tmp()
        self.cluster_lock = common.get_cluster_lock_file()

        self._cluster_instance_num = -1

    @property
    def cluster_instance_num(self) -> int:
        if self._cluster_instance_num == -1:
            msg = "Cluster instance not set."
            raise RuntimeError(msg)
        return self._cluster_instance_num

    @property
    def instance_dir(self) -> pl.Path:
        return status_files.get_instance_dir(instance_num=self.cluster_instance_num)

    @property
    def ports(self) -> cardonnay_local.InstancePorts:
        """Return port mappings for current cluster instance."""
        return cluster_nodes.get_cluster_type().cluster_scripts.get_instance_ports(
            self.cluster_instance_num
        )

    def _create_startup_files_dir(self, instance_num: int) -> pl.Path:
        inst_dir = status_files.get_instance_dir(instance_num=instance_num)
        rand_str = helpers.get_rand_str(8)
        startup_files_dir = inst_dir / "startup_files" / rand_str
        startup_files_dir.mkdir(exist_ok=True, parents=True)
        return startup_files_dir

    def _respin(self, scriptsdir: ttypes.FileType = "") -> bool:  # noqa: C901
        """Respin cluster.

        Not called under global lock!
        """
        cluster_running_file = status_files.get_cluster_running_file(
            instance_num=self.cluster_instance_num
        )

        # Don't respin cluster if it was started outside of test framework
        if configuration.DEV_CLUSTER_RUNNING:
            self.log(f"c{self.cluster_instance_num}: ignoring respin, dev cluster is running")
            if cluster_running_file.exists():
                LOGGER.warning("Ignoring requested cluster respin as 'DEV_CLUSTER_RUNNING' is set.")
            else:
                cluster_running_file.touch()
            return True

        # Fail if cluster respin is forbidden and the cluster was already started
        if configuration.FORBID_RESTART and cluster_running_file.exists():
            msg = "Cannot respin cluster when 'FORBID_RESTART' is set."
            raise RuntimeError(msg)

        self.log(f"c{self.cluster_instance_num}: called `_respin`, scriptsdir='{scriptsdir}'")

        state_dir = cluster_nodes.get_cluster_env().state_dir

        if (
            state_dir.exists()
            and not status_files.get_started_by_framework_file(state_dir=state_dir).exists()
        ):
            self.log(
                f"c{self.cluster_instance_num}: ERROR: state dir exists but cluster "
                "was not started by the framework"
            )
            msg = "Cannot respin cluster when it was not started by the framework."
            raise RuntimeError(msg)

        startup_files = cluster_nodes.get_cluster_type().cluster_scripts.prepare_scripts_files(
            destdir=self._create_startup_files_dir(self.cluster_instance_num),
            instance_num=self.cluster_instance_num,
            scriptsdir=scriptsdir,
        )

        self.log(
            f"c{self.cluster_instance_num}: in `_respin`, new files "
            f"scriptsdir='{startup_files.start_script.parent}', "
        )

        def _netstat_log_func(msg: str) -> None:
            self.log(f"c{self.cluster_instance_num}: {msg}")

        excp: Exception | None = None
        netstat_out = ""
        ports = self.ports
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

            # Give the cluster time to stop
            time.sleep(10)

            # Kill the leftover processes
            netstat_tools.kill_old_cluster(
                instance_num=self.cluster_instance_num, log_func=_netstat_log_func
            )

            # Save artifacts only when produced during this test run
            if cluster_running_file.exists() or i > 0:
                artifacts.save_start_script_coverage(
                    log_file=state_dir / common.START_CLUSTER_LOG,
                    pytest_config=self.pytest_config,
                )
                artifacts.save_cluster_artifacts(save_dir=self.pytest_tmp_dir, state_dir=state_dir)

            shutil.rmtree(state_dir, ignore_errors=True)

            _cluster_started = False
            try:
                cluster_obj = cluster_nodes.start_cluster(
                    cmd=str(startup_files.start_script), args=startup_files.start_script_args
                )
                _cluster_started = True
            except Exception as err:
                LOGGER.error(f"Failed to start cluster: {err}")  # noqa: TRY400
                excp = err
            finally:
                if state_dir.exists():
                    status_files.create_started_by_framework_file(state_dir=state_dir)
            # `else` cannot be used together with `finally`
            if _cluster_started:
                break

            netstat_out = netstat_tools.get_netstat_conn()
            self.log(
                f"c{self.cluster_instance_num}: failed to start cluster:\n{excp}"
                f"\nports:\n{ports}"
                f"\nnetstat:\n{netstat_out}"
            )
        else:
            self.log(f"c{self.cluster_instance_num}: cluster dead")
            framework_log.framework_logger().error(
                "Failed to start cluster instance 'c%s':\n%s\nports:\n%s\nnetstat:\n%s",
                self.cluster_instance_num,
                excp,
                ports,
                netstat_out,
            )
            if not configuration.IS_XDIST:
                pytest.exit(reason="Failed to start cluster", returncode=1)
            status_files.create_cluster_dead_file(instance_num=self.cluster_instance_num)
            return False

        # Generate ID for the new cluster instance so it is possible to match log entries with
        # cluster instance files saved as artifacts.
        cluster_instance_id = helpers.get_rand_str(8)
        with open(
            state_dir / artifacts.CLUSTER_INSTANCE_ID_FILENAME, "w", encoding="utf-8"
        ) as fp_out:
            fp_out.write(cluster_instance_id)
        self.log(f"c{self.cluster_instance_num}: started cluster instance '{cluster_instance_id}'")

        # Create dir for faucet addresses data among tests artifacts, so it can be accessed
        # during testnet cleanup.
        addr_data_dir = (
            temptools.get_pytest_worker_tmp() / f"{common.ADDRS_DATA_DIRNAME}_"
            f"ci{self.cluster_instance_num}_{cluster_instance_id}"
        )
        addr_data_dir.mkdir(parents=True, exist_ok=True)

        # Setup faucet addresses
        try:
            cluster_nodes.setup_test_addrs(cluster_obj=cluster_obj, destination_dir=addr_data_dir)
        except Exception as err:
            self.log(
                f"c{self.cluster_instance_num}: failed to setup test addresses:\n{err}\n"
                "cluster dead"
            )
            framework_log.framework_logger().error(
                "Failed to setup test addresses on instance 'c%s':\n%s",
                self.cluster_instance_num,
                excp,
            )
            if not configuration.IS_XDIST:
                pytest.exit(
                    reason=f"Failed to setup test addresses, exception: {err}", returncode=1
                )
            status_files.create_cluster_dead_file(instance_num=self.cluster_instance_num)
            return False

        # Create file that indicates that the cluster is running
        cluster_running_file.touch()

        return True

    def _is_dev_cluster_ready(self) -> bool:
        """Check if development cluster instance is ready to be used."""
        state_dir = cluster_nodes.get_cluster_env().state_dir
        return (state_dir / cluster_nodes.ADDRS_DATA).exists()

    def _setup_dev_cluster(self) -> None:
        """Set up cluster instance that was already started outside of test framework."""
        cluster_env = cluster_nodes.get_cluster_env()
        if (cluster_env.state_dir / cluster_nodes.ADDRS_DATA).exists():
            return

        self.log(f"c{cluster_env.instance_num}: setting up dev cluster")

        # Setup faucet addresses
        addr_data_dir = cluster_env.state_dir / common.ADDRS_DATA_DIRNAME
        addr_data_dir.mkdir(exist_ok=True, parents=True)
        cluster_obj = cluster_nodes.get_cluster_type().get_cluster_obj()
        cluster_nodes.setup_test_addrs(cluster_obj=cluster_obj, destination_dir=addr_data_dir)

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
        # If cluster instance is not started yet
        if not status_files.get_cluster_running_file(instance_num=instance_num).exists():
            return True

        # If it was indicated that the cluster instance needs to be respun
        if status_files.list_respin_needed_files(instance_num=instance_num):
            return True

        # If a service failed on cluster instance.
        # Check only if we are really able to restart the cluster instance, because the check
        # is expensive.
        if not (configuration.FORBID_RESTART or self._is_healthy(instance_num)):  # noqa:SIM103
            return True

        return False

    def _test_needs_respin(self, cget_status: _ClusterGetStatus) -> bool:
        """Check if it is necessary to respin cluster for the test."""
        # If this is non-initial marked test, we can ignore custom start command,
        # as it was handled by the initial marked test
        noninitial_marked_test = cget_status.mark and cget_status.marked_ready_sfiles
        if noninitial_marked_test:
            return False

        # Respin is needed when custom scripts were specified
        return bool(cget_status.scriptsdir)

    def _on_marked_test_stop(self, instance_num: int, mark: str) -> None:
        """Perform actions after all marked tests are finished."""
        self.log(f"c{instance_num}: in `_on_marked_test_stop`")

        # Set cluster instance to be respun if needed
        respin_after_mark_files = status_files.rm_respin_after_mark_files(
            instance_num=instance_num, mark=mark
        )
        if respin_after_mark_files:
            self.log(f"c{instance_num}: in `_on_marked_test_stop`, creating 'respin needed' file")
            status_files.create_respin_needed_file(
                instance_num=instance_num, worker_id=self.worker_id
            )

        # Remove files that indicates that the mark is ready
        status_files.rm_curr_mark_files(instance_num=instance_num, mark=mark)

        # Remove file that indicates resources that are locked by the marked tests
        status_files.rm_resource_locked_files(instance_num=instance_num, mark=mark)

        # Remove file that indicates resources that are in-use by the marked tests
        status_files.rm_resource_used_files(instance_num=instance_num, mark=mark)

    def _get_marked_tests_status(
        self, marked_tests_cache: dict[int, dict[str, int]], instance_num: int
    ) -> dict[str, int]:
        """Return marked tests status for cluster instance."""
        if instance_num not in marked_tests_cache:
            marked_tests_cache[instance_num] = {}
        marked_tests_status = marked_tests_cache[instance_num]
        return marked_tests_status

    def _update_marked_tests(
        self,
        marked_tests_cache: dict[int, dict[str, int]],
        cget_status: _ClusterGetStatus,
    ) -> None:
        """Update status about running of marked test.

        When marked test is finished, we can't clear the mark right away. There might be a test
        with the same mark in the queue and it will be scheduled in a short while. We would need
        to repeat all the expensive setup if we already cleared the mark. Therefore we need to
        keep track of marked tests and clear the mark and cluster instance only when no marked
        test was running for some time.
        """
        # No need to continue if there are no marked tests
        if not status_files.list_curr_mark_files(instance_num=cget_status.instance_num):
            return

        # Marked tests don't need to be running yet if the cluster is being respun
        respin_in_progress = status_files.list_respin_progress_files(
            instance_num=cget_status.instance_num
        )
        if respin_in_progress:
            return

        # Get marked tests status
        marked_tests_status = self._get_marked_tests_status(
            marked_tests_cache=marked_tests_cache, instance_num=cget_status.instance_num
        )

        # Update marked tests status
        marks_in_progress = status_files.get_marks_in_progress(
            instance_num=cget_status.instance_num
        )

        for m in marks_in_progress:
            marked_tests_status[m] = 0

        for m in marked_tests_status:
            marked_tests_status[m] += 1

            # Clean the stale status files if we are waiting too long for the next marked test
            if marked_tests_status[m] >= 20:
                self.log(
                    f"c{cget_status.instance_num}: no marked tests running for a while, "
                    "cleaning the mark status file"
                )
                self._on_marked_test_stop(instance_num=cget_status.instance_num, mark=m)

    def _resolve_resources_availability(self, cget_status: _ClusterGetStatus) -> bool:
        """Resolve availability of required "use" and "lock" resources."""
        resources_locked = status_files.get_resources_from_path(
            paths=status_files.list_resource_locked_files(instance_num=cget_status.instance_num)
        )

        # This test wants to lock some resources, check if these are not in use
        res_lockable = []
        if cget_status.lock_resources:
            resources_used = status_files.get_resources_from_path(
                paths=status_files.list_resource_used_files(instance_num=cget_status.instance_num)
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

        # This test wants to use some resources, check if these are not locked
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

        # Make sure that all resource names are sanitized, otherwise there will be issues with
        # matching the unsanitized names to the sanitized ones.
        unsanitized_res = resources.get_unsanitized([*res_usable, *res_lockable])
        if unsanitized_res:
            msg = (
                "Following resource names violates naming '[a-zA-Z0-9_-]{1,20}': "
                f"{unsanitized_res}"
            )
            raise RuntimeError(msg)

        # Resources that are locked are also in use
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
        test_on_worker = status_files.list_test_running_files(worker_id=self.worker_id)

        # Test is already running, nothing to set up
        if test_on_worker and self._cluster_instance_num != -1:
            self.log(f"{test_on_worker[0]} already exists")
            return True

        return False

    def _wait_for_prio(self, cget_status: _ClusterGetStatus) -> bool:
        """Check if there is a priority test waiting for cluster instance."""
        # A "prio" test has priority in obtaining cluster instance. Non-priority
        # tests can continue with their setup only if they are already locked to a
        # cluster instance.
        if (
            not (
                cget_status.prio_here
                or cget_status.selected_instance != -1
                or cget_status.marked_running_my_anywhere
            )
            and status_files.list_prio_in_progress_files()
        ):
            self.log("'prio' test setup in progress, cannot continue")
            return True

        return False

    def _init_prio(self, cget_status: _ClusterGetStatus) -> None:
        """Set "prio" for this test if indicated."""
        if not cget_status.prio:
            return

        status_files.create_prio_in_progress_file(worker_id=self.worker_id)
        cget_status.prio_here = True
        self.log(f"setting 'prio' for '{cget_status.current_test}'")

    def _respun_by_other_worker(self, cget_status: _ClusterGetStatus) -> bool:
        """Check if the cluster is currently being respun by worker other than this one."""
        if cget_status.respin_here:
            return False

        respin_in_progress = status_files.list_respin_progress_files(
            instance_num=cget_status.instance_num
        )
        return bool(respin_in_progress)

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
                "already running on other cluster instance, cannot start"
            )
            return False

        # If here, this will be the first test with the mark
        return True

    def _fail_on_all_dead(self) -> None:
        """Fail if all cluster instances are dead."""
        dead_clusters = status_files.list_cluster_dead_files()
        if len(dead_clusters) == self.num_of_instances:
            msg = "All clusters are dead, cannot run."
            raise RuntimeError(msg)

    def _cleanup_dead_clusters(self, cget_status: _ClusterGetStatus) -> None:
        """Cleanup if the selected cluster instance failed to start."""
        # Move on to other cluster instance
        cget_status.selected_instance = -1
        cget_status.respin_here = False
        cget_status.respin_ready = False

        # Remove status files that are checked by other workers
        status_files.rm_curr_mark_files(instance_num=cget_status.instance_num)

    def _init_respin(self, cget_status: _ClusterGetStatus) -> bool:
        """Initialize respin of this cluster instance on this worker."""
        # Respin already initialized
        if cget_status.respin_here:
            return True

        if not (cget_status.cluster_needs_respin or self._test_needs_respin(cget_status)):
            return True

        # If tests are running on the instance, we cannot respin, therefore we cannot continue
        if cget_status.started_tests_sfiles:
            self.log(f"c{cget_status.instance_num}: tests are running, cannot respin")
            return False

        self.log(f"c{cget_status.instance_num}: setting 'respin in progress'")

        # Cluster respin will be performed by this worker.
        # By setting `respin_here`, we make sure this worker continue on this cluster instance
        # after respin is finished. It is important because the `scriptsdir` used for starting the
        # cluster instance might be specific for the test.
        cget_status.respin_here = True
        cget_status.selected_instance = cget_status.instance_num

        status_files.create_respin_progress_file(
            instance_num=cget_status.instance_num, worker_id=self.worker_id
        )

        # Remove mark status files as these will not be valid after respin
        status_files.rm_curr_mark_files(instance_num=cget_status.instance_num)

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

            # Remove status files that are no longer valid after respin
            status_files.rm_respin_progress_files(instance_num=cget_status.instance_num)
            status_files.rm_respin_needed_files(instance_num=cget_status.instance_num)

            return True

        # NOTE: when `_respin` is called, the env variables needed for cluster start scripts need
        # to be already set (e.g. CARDANO_NODE_SOCKET_PATH)
        self.log(f"c{cget_status.instance_num}: ready to respin cluster")
        # The actual `_respin` function will be called outside of global lock so other workers
        # don't need to wait
        cget_status.respin_ready = True
        return False

    def _init_marked_test(self, cget_status: _ClusterGetStatus) -> None:
        """Create status file for marked test."""
        if not cget_status.mark:
            return

        status_files.create_curr_mark_file(
            instance_num=cget_status.instance_num, worker_id=self.worker_id, mark=cget_status.mark
        )

    def _create_test_status_files(self, cget_status: _ClusterGetStatus) -> None:
        """Create status files for test that is about to start on this cluster instance."""
        # Create status file for each in-use resource
        status_files.create_resource_used_files(
            instance_num=cget_status.instance_num,
            worker_id=self.worker_id,
            use_names=cget_status.final_use_resources,
            mark=cget_status.mark,
        )

        # Create status file for each locked resource
        status_files.create_resource_locked_files(
            instance_num=cget_status.instance_num,
            worker_id=self.worker_id,
            lock_names=cget_status.final_lock_resources,
            mark=cget_status.mark,
        )

        # Cleanup = cluster respin after test (group of tests) is finished
        if cget_status.cleanup:
            # Cleanup after group of test that are marked with a marker
            if cget_status.mark:
                self.log(f"c{cget_status.instance_num}: cleanup and mark")
                status_files.create_respin_after_mark_file(
                    instance_num=cget_status.instance_num,
                    worker_id=self.worker_id,
                    mark=cget_status.mark,
                )
            # Cleanup after single test (e.g. singleton)
            else:
                self.log(f"c{cget_status.instance_num}: cleanup and not mark")
                status_files.create_respin_needed_file(
                    instance_num=cget_status.instance_num, worker_id=self.worker_id
                )

        self.log(f"c{self.cluster_instance_num}: creating 'test running' status file")
        status_files.create_test_running_file(
            instance_num=self.cluster_instance_num,
            worker_id=self.worker_id,
            # Write the name of the test that is starting on this cluster instance, leave out the
            # '(setup)' part
            test_id=cget_status.current_test.split(" ")[0],
            mark=cget_status.mark,
        )

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
        scriptsdir: ttypes.FileType = "",
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
            scriptsdir: Path to custom scripts for the cluster.
        """
        if isinstance(lock_resources, str):
            msg = "`lock_resources` cannot be a string"
            raise TypeError(msg)
        if isinstance(use_resources, str):
            msg = "`use_resources` cannot be a string"
            raise TypeError(msg)

        # Sanitize strings so they can be used in file names
        mark = resources.sanitize_res_name(mark)
        lock_resources = [
            resources.sanitize_res_name(r) if isinstance(r, str) else r for r in lock_resources
        ]
        use_resources = [
            resources.sanitize_res_name(r) if isinstance(r, str) else r for r in use_resources
        ]

        if configuration.DEV_CLUSTER_RUNNING:
            if scriptsdir:
                LOGGER.warning(
                    f"Ignoring the '{scriptsdir}' custom cluster scripts as "
                    "'DEV_CLUSTER_RUNNING' is set."
                )

            # Check if the development cluster instance is ready by now so we don't need to obtain
            # cluster lock when it is not necessary
            if not self._is_dev_cluster_ready():
                with locking.FileLockIfXdist(self.cluster_lock):
                    self._setup_dev_cluster()

            available_instances = [cluster_nodes.get_cluster_env().instance_num]
        else:
            available_instances = list(range(self.num_of_instances))

        if configuration.FORBID_RESTART and scriptsdir:
            msg = "Cannot use custom cluster scripts when 'FORBID_RESTART' is set."
            raise RuntimeError(msg)

        if scriptsdir:
            if resources.Resources.CLUSTER not in lock_resources:
                msg = "Custom cluster scripts can be used only together with singleton."
                raise RuntimeError(msg)
            # Always clean after test(s) that started cluster with custom configuration
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
            scriptsdir=scriptsdir,
            current_test=os.environ.get("PYTEST_CURRENT_TEST") or "",
        )
        marked_tests_cache: dict[int, dict[str, int]] = {}

        self.log(f"want to run test '{cget_status.current_test}'")

        # Iterate until it is possible to start the test
        while True:
            if cget_status.respin_ready:
                self._respin(scriptsdir=scriptsdir)

            # Sleep for a while to avoid too many checks in a short time
            _xdist_sleep(random.uniform(0.6, 1.2) * cget_status.sleep_delay)
            cget_status.sleep_delay = max(cget_status.sleep_delay, 1)

            # Nothing time consuming can go under this lock as all other workers will need to wait
            with locking.FileLockIfXdist(self.cluster_lock):
                if self._is_already_running():
                    return self.cluster_instance_num

                # Fail if all cluster instances are dead
                self._fail_on_all_dead()

                if mark:
                    # Check if tests with my mark are already locked to any cluster instance
                    cget_status.marked_running_my_anywhere = status_files.list_curr_mark_files(
                        mark=mark
                    )

                # A "prio" test has priority in obtaining cluster instance. Check if it is needed
                # to wait until earlier "prio" test obtains a cluster instance.
                if self._wait_for_prio(cget_status):
                    cget_status.sleep_delay = 5
                    continue

                # Set "prio" for this test if indicated
                self._init_prio(cget_status)

                self._cluster_instance_num = -1

                # Try all existing cluster instances; randomize the order
                for instance_num in random.sample(available_instances, k=self.num_of_instances):
                    # If instance to run the test on was already decided, skip all other instances
                    if cget_status.selected_instance not in (-1, instance_num):
                        continue

                    cget_status.instance_num = instance_num
                    cget_status.instance_dir = status_files.get_instance_dir(
                        instance_num=instance_num
                    )
                    cget_status.instance_dir.mkdir(exist_ok=True)

                    # Cleanup cluster instance where attempt to start cluster failed repeatedly
                    if status_files.get_cluster_dead_file(instance_num=instance_num).exists():
                        self._cleanup_dead_clusters(cget_status)
                        continue

                    # Cluster respin planned or in progress, so no new tests can start
                    if self._respun_by_other_worker(cget_status):
                        cget_status.sleep_delay = 5
                        continue

                    # Are there tests already running on this cluster instance?
                    cget_status.started_tests_sfiles = status_files.list_test_running_files(
                        instance_num=instance_num
                    )

                    # "marked tests" = group of tests marked with my mark
                    cget_status.marked_ready_sfiles = status_files.list_curr_mark_files(
                        instance_num=instance_num, mark=mark
                    )

                    # If marked tests are already running, update their status
                    self._update_marked_tests(
                        marked_tests_cache=marked_tests_cache, cget_status=cget_status
                    )

                    # If there would be more tests running on this cluster instance than allowed,
                    # we need to wait.
                    if (
                        self.num_of_instances > 1
                        and (tnum := len(cget_status.started_tests_sfiles))
                        >= configuration.MAX_TESTS_PER_CLUSTER
                    ):
                        cget_status.sleep_delay = 2
                        self.log(f"c{instance_num}: {tnum} tests are already running, cannot start")
                        continue

                    # Does the cluster instance needs respin to continue?
                    # Cache the result as the check itself can be expensive.
                    cget_status.cluster_needs_respin = self._cluster_needs_respin(instance_num)

                    # Select this instance for running marked tests if possible
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

                    # Check availability of the required resources
                    if need_resolve_resources and not self._resolve_resources_availability(
                        cget_status
                    ):
                        cget_status.sleep_delay = 5
                        continue

                    # If respin is needed, indicate that the cluster will be re-spun
                    # (after all currently running tests are finished)
                    if not self._init_respin(cget_status):
                        continue

                    # We've found suitable cluster instance
                    cget_status.selected_instance = instance_num
                    self._cluster_instance_num = instance_num
                    self.log(f"c{instance_num}: can run test '{cget_status.current_test}'")
                    # Set environment variables that are needed when respinning the cluster
                    # and running tests
                    cluster_nodes.set_cluster_env(instance_num)

                    # Remove "prio" status file
                    if prio:
                        status_files.rm_prio_in_progress_files(worker_id=self.worker_id)

                    # Create status file for marked tests.
                    # This must be done before the cluster is re-spun, so that other marked tests
                    # don't try to prepare another cluster instance.
                    self._init_marked_test(cget_status)

                    # If needed, finish respin related actions
                    if not self._finish_respin(cget_status):
                        continue

                    # From this point on, all conditions needed to start the test are met
                    break
                else:
                    # If the test cannot start on any instance, return to top-level loop
                    cget_status.tried_all_instances = True
                    continue

                self._create_test_status_files(cget_status)

                # Cluster instance is ready, we can start the test
                break

        return instance_num
