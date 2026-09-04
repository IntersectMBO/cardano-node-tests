"""Functionality for obtaining and setting up a cluster instance for parallel test execution.

The `ClusterGetter` class is responsible for managing a pool of cluster instances and assigning them
to tests running in parallel on different pytest workers. It ensures that tests get a suitable,
properly configured, and healthy cluster instance to run on.

Coordination between workers is achieved through status records kept in a SQLite database in
a shared temporary directory. The records signal the state of each cluster instance (e.g., running,
needs respin), which tests are running on which instance, and what resources are locked or in use.

The core logic is implemented in the `get_cluster_instance` method. It enters a loop where it
evaluates the state of all available cluster instances against the requirements of the current test
(e.g., resource needs, custom scripts, priority). It will wait and retry until a suitable instance
is found and all conditions for starting the test are met. This includes handling cluster restarts
(respins), resource allocation, and synchronization for tests that share expensive setups
(marked tests).
"""

import dataclasses
import enum
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
from cardano_node_tests.cluster_management import status_db
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

# Minimal interval (seconds) between garbage collections of status records
# that were left by crashed pytest workers. The last-run timestamp is kept in the
# status database, so the interval applies across all workers.
GC_INTERVAL_SEC = 60

# How long (seconds) no test with a given mark needs to be running before the mark
# is considered abandoned and its status records are cleaned up.
# To keep the gaps between marked tests short, the marked tests should also be marked
# with `@pytest.mark.xdist_group("<same name>")`. The custom xdist scheduler
# (`pytest_plugins.xdist_scheduler`) then schedules them as one work unit on a single
# pytest worker, so they run back-to-back and the assigned "marked" cluster instance
# is reused instead of being cleaned up and prepared again. Note that when the worker
# dies mid-group, xdist re-queues the remaining tests and they can run on a different
# worker with arbitrary tests in between.
MARK_STALENESS_SEC = 60

# Refresh the "current mark" records only when they are older than this (seconds),
# so pollers don't write to the database on every scheduler iteration.
MARK_REFRESH_SEC = 15

# Back-off (seconds) before the next attempt to obtain a cluster instance. The longer
# back-off is used when the wait depends on other workers finishing their work (a cluster
# respin, a test holding resources, a "prio" test setup), the shorter one when only the
# scheduling of tests over the cluster instances blocks the start.
LONG_BACKOFF_SEC = 5
SHORT_BACKOFF_SEC = 2


def _no_sleep(seconds: float, /) -> None:
    """No need to sleep if tests are running on a single worker."""


_xdist_sleep: tp.Callable[[float], None] = time.sleep if configuration.IS_XDIST else _no_sleep


class _RespinPhase(enum.Enum):
    """Phase of a cluster respin claimed by this worker.

    The claim moves through the phases:

    * CLAIMED - this worker claimed the respin of the selected cluster instance
      (`_init_respin`) and is pinned to it. The claim is armed once all other
      conditions for the respin are met - usually later in the same iteration,
      or on a later one when the instance must be re-evaluated first (e.g. the
      mark's resources must be resolved again).
    * ARMED - the actual `_respin` will run at the start of the next iteration,
      outside of the global lock so other workers don't need to wait.
    * RESPUN - `_respin` has finished (successfully or not). The phase moves here
      right after the `_respin` call, so the respin runs exactly once per claim
      even when later conditions delay the cleanup. The respin status records are
      cleaned up under the lock afterwards (`_cleanup_after_respin`), which
      discards the claim.

    When the cluster instance dies, `_cleanup_dead_cluster` discards the claim in
    any phase and the respin is abandoned.
    """

    CLAIMED = enum.auto()
    ARMED = enum.auto()
    RESPUN = enum.auto()


@dataclasses.dataclass
class _RespinClaim:
    """A respin of a cluster instance claimed by this worker.

    The claim exists only for as long as this worker owns the respin - no claim
    (`None`) means no respin is being done by this worker. The claim pins the worker
    to the cluster instance it was made for, so the two are always established and
    discarded together (`_ClusterGetStatus.claim_respin`, respectively
    `_ClusterGetStatus.release_instance`).
    """

    instance_num: int
    phase: _RespinPhase = _RespinPhase.CLAIMED


class _InstanceVerdict(enum.Enum):
    """Verdict on a cluster instance for running the current test.

    * START - proceed with the instance (the checks passed, respectively the
      selection is finished).
    * SKIP - the test cannot start on the instance; the loop moves on to the next
      cluster instance.
    * DEFER - the test cannot start yet and no other cluster instance is an option,
      as the worker is pinned to this one; the loop returns to the top level and
      re-enters this instance on a later iteration.
    """

    START = enum.auto()
    SKIP = enum.auto()
    DEFER = enum.auto()


@dataclasses.dataclass(frozen=True)
class _InstanceDecision:
    """Verdict on a cluster instance together with the back-off it asks for.

    The back-off is applied to the sticky `_ClusterGetStatus.sleep_delay` by the
    instance loop, so that the checks producing the verdict don't write the
    cross-iteration worker state themselves.
    """

    verdict: _InstanceVerdict
    backoff_sec: int = 0

    @classmethod
    def start(cls) -> tp.Self:
        """Return a START decision."""
        return cls(verdict=_InstanceVerdict.START)

    @classmethod
    def skip(cls, backoff_sec: int = 0) -> tp.Self:
        """Return a SKIP decision, optionally asking for a back-off."""
        return cls(verdict=_InstanceVerdict.SKIP, backoff_sec=backoff_sec)

    @classmethod
    def defer(cls) -> tp.Self:
        """Return a DEFER decision."""
        return cls(verdict=_InstanceVerdict.DEFER)


@dataclasses.dataclass
class _InstanceScratch:
    """What the evaluation found out about a single cluster instance.

    Created for one evaluation of one cluster instance (`_evaluate_instance`) and
    discarded once the loop moves on, so findings about one instance cannot leak
    into decisions taken about another instance or on a later iteration.
    """

    instance_num: int
    # Tests already running on the cluster instance
    started_tests_rows: tp.Sequence[status_db.StatusRow] = ()
    # "marked tests" = group of tests marked with my mark
    marked_ready_rows: tp.Sequence[status_db.StatusRow] = ()
    # Whether the cluster instance needs a respin. Cached, as the check can be expensive.
    cluster_needs_respin: bool = False
    # Resources resolved for the test on this cluster instance
    final_lock_resources: tp.Iterable[str] = ()
    final_use_resources: tp.Iterable[str] = ()


@dataclasses.dataclass
class _ClusterGetStatus:
    """Worker state while trying to `get` suitable cluster instance.

    Lives for the whole `get_cluster_instance` call, i.e. across all iterations of
    its loop. Findings about a single cluster instance belong to `_InstanceScratch`
    instead.
    """

    # Requirements of the test, set once
    mark: str
    lock_resources: resources_management.ResourcesType
    use_resources: resources_management.ResourcesType
    prio: bool
    cleanup: bool
    scriptsdir: ttypes.FileType
    current_test: str
    # Cluster instance this worker is pinned to, -1 when not pinned to any
    selected_instance: int = -1
    # Respin claimed by this worker, `None` when this worker claimed none
    respin: _RespinClaim | None = None
    sleep_delay: int = 0
    prio_here: bool = False
    tried_all_instances: bool = False
    # Status records of my mark on any cluster instance, re-read on every iteration
    marked_running_my_anywhere: tp.Sequence[status_db.StatusRow] = ()

    @property
    def respin_phase_name(self) -> str:
        """Return name of the respin phase, for log and error messages."""
        return self.respin.phase.name if self.respin else "NONE"

    def owns_respin(self, instance_num: int) -> bool:
        """Return whether this worker holds a respin claim on the cluster instance."""
        return self.respin is not None and self.respin.instance_num == instance_num

    def pin_instance(self, instance_num: int) -> None:
        """Pin this worker to the cluster instance."""
        self.selected_instance = instance_num

    def claim_respin(self, instance_num: int) -> None:
        """Claim the respin of the cluster instance and pin this worker to it."""
        self.respin = _RespinClaim(instance_num=instance_num)
        self.pin_instance(instance_num)

    def release_instance(self) -> None:
        """Unpin this worker from its cluster instance, abandoning any claimed respin.

        The pin and the claim are discarded together, so the worker cannot stay pinned
        to an instance it gave up on, nor keep a claim on an instance it is not
        pinned to.
        """
        self.selected_instance = -1
        self.respin = None


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

        if cluster_nodes.get_cluster_type().is_local:
            # Soft timeout (seconds): applies when no cluster is selected.
            self.grace_period_soft = 3600
            # Hard timeout (seconds): always applies, regardless of cluster selection.
            self.grace_period_hard = 7200
        else:
            self.grace_period_soft = 36000
            self.grace_period_hard = 37800

        # Time window (seconds) before deadline when stricter dead cluster checks apply.
        self.strict_check_window = 1200
        # Maximum allowed fraction of dead clusters during strict check window.
        self.strict_dead_fraction = 0.51

        self._cluster_instance_num = -1
        self._snapshot: status_db.StatusSnapshot | None = None

    @property
    def snap(self) -> status_db.StatusSnapshot:
        """Return the snapshot of status records.

        The snapshot is valid only while the global cluster lock is held and must be
        refreshed right after the lock is acquired.
        """
        if self._snapshot is None:
            self._snapshot = status_db.StatusSnapshot()
        return self._snapshot

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
            instance_num=self.cluster_instance_num
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
        cluster_running = status_db.is_cluster_running(instance_num=self.cluster_instance_num)

        # Don't respin cluster if it was started outside of test framework
        if configuration.DEV_CLUSTER_RUNNING:
            self.log(f"c{self.cluster_instance_num}: ignoring respin, dev cluster is running")
            if cluster_running:
                LOGGER.warning("Ignoring requested cluster respin as 'DEV_CLUSTER_RUNNING' is set.")
            else:
                status_db.set_cluster_running(instance_num=self.cluster_instance_num)
            return True

        # Fail if cluster respin is forbidden and the cluster was already started
        if configuration.FORBID_RESTART and cluster_running:
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
        cluster_obj = None
        for i in range(2):
            if i > 0:
                self.log(
                    f"c{self.cluster_instance_num}: failed to start cluster:\n{excp}\nretrying"
                )
                time.sleep(0.2)

            try:
                LOGGER.info(f"Stopping cluster with `{startup_files.stop_script}`.")
                helpers.run_command([startup_files.stop_script])
            except Exception as err:
                self.log(f"c{self.cluster_instance_num}: failed to stop cluster:\n{err}")

            # Give the cluster time to stop
            time.sleep(10)

            # Kill the leftover processes
            netstat_tools.kill_old_cluster(
                instance_num=self.cluster_instance_num, log_func=_netstat_log_func
            )

            # Save artifacts only when produced during this test run.
            # Artifact collection is best-effort diagnostics, its failure must not abort
            # the cluster restart. The two saves are independent, so a failure in one
            # must not prevent the other.
            if cluster_running or i > 0:
                try:
                    artifacts.save_start_script_coverage(
                        log_file=state_dir / common.START_CLUSTER_LOG,
                        pytest_config=self.pytest_config,
                    )
                except Exception as err:
                    self.log(
                        f"c{self.cluster_instance_num}: failed to save start script coverage:"
                        f"\n{err}"
                    )
                    LOGGER.exception(
                        f"Failed to save start script coverage for 'c{self.cluster_instance_num}'."
                    )
                try:
                    artifacts.save_cluster_artifacts(
                        save_dir=self.pytest_tmp_dir, state_dir=state_dir
                    )
                except Exception as err:
                    self.log(f"c{self.cluster_instance_num}: failed to save artifacts:\n{err}")
                    LOGGER.exception(
                        f"Failed to save cluster artifacts for 'c{self.cluster_instance_num}'."
                    )

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
            status_db.set_cluster_dead(instance_num=self.cluster_instance_num)
            return False

        if cluster_obj is None:
            # Should never reach this. Mark the instance as dead anyway - the respin
            # runs only once per claim, so nothing else would retry the failed start.
            self.log(f"c{self.cluster_instance_num}: failed to start cluster")
            status_db.set_cluster_dead(instance_num=self.cluster_instance_num)
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
            status_db.set_cluster_dead(instance_num=self.cluster_instance_num)
            return False

        # Create record that indicates that the cluster is running
        status_db.set_cluster_running(instance_num=self.cluster_instance_num)

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
        if not self.snap.is_cluster_running(instance_num=instance_num):
            return True

        # If it was indicated that the cluster instance needs to be respun
        if self.snap.list_respin_needed(instance_num=instance_num):
            return True

        # If a service failed on cluster instance.
        # Check only if we are really able to restart the cluster instance, because the check
        # is expensive.
        if not (configuration.FORBID_RESTART or self._is_healthy(instance_num)):  # noqa:SIM103
            return True

        return False

    def _test_needs_respin(self, cget_status: _ClusterGetStatus, scratch: _InstanceScratch) -> bool:
        """Check if it is necessary to respin cluster for the test."""
        # If this is non-initial marked test, we can ignore custom start command,
        # as it was handled by the initial marked test
        noninitial_marked_test = cget_status.mark and scratch.marked_ready_rows
        if noninitial_marked_test:
            return False

        # Respin is needed when custom scripts were specified
        return bool(cget_status.scriptsdir)

    def _on_marked_test_stop(self, instance_num: int, mark: str) -> None:
        """Perform actions after all marked tests are finished."""
        self.log(f"c{instance_num}: in `_on_marked_test_stop`")

        respin_after_mark = self._rm_marks(instance_num=instance_num, mark=mark)

        # Set cluster instance to be respun if it was requested by the marked tests
        if respin_after_mark:
            self.log(f"c{instance_num}: in `_on_marked_test_stop`, creating 'respin needed' record")
            status_db.create_respin_needed(instance_num=instance_num, worker_id=self.worker_id)

    def _update_marked_tests(self, instance_num: int) -> None:
        """Update status about running of marked test.

        When marked test is finished, we can't clear the mark right away. There might be a test
        with the same mark in the queue and it will be scheduled in a short while. We would need
        to repeat all the expensive setup if we already cleared the mark. Therefore the "current
        mark" records are timestamped whenever a marked test is seen running or finishes
        (see `ClusterManager.on_test_stop`), and the mark and cluster instance are cleared
        only when no marked test was running for some time.

        Must be called before the current state of the mark is read from the snapshot,
        as the cleanup deletes the mark's status records.
        """
        # No need to continue if there are no marked tests
        curr_mark_rows = self.snap.list_curr_mark(instance_num=instance_num)
        if not curr_mark_rows:
            return

        # Marked tests don't need to be running yet if the cluster is being respun
        respin_in_progress = self.snap.list_respin_progress(instance_num=instance_num)
        if respin_in_progress:
            return

        marks_in_progress = set(self.snap.get_marks_in_progress(instance_num=instance_num))

        # A mark can have one record per worker and the records age together - the newest
        # one tells when a test with the mark was last seen running.
        newest_by_mark: dict[str, float] = {}
        for row in curr_mark_rows:
            newest_by_mark[row.mark] = max(newest_by_mark.get(row.mark, 0.0), row.created_at)

        now_ts = time.time()
        for mark, newest in newest_by_mark.items():
            # Record that a marked test is running now. Skip the write when the records
            # are still fresh, so polling doesn't do a database write (and a snapshot
            # reload) on every iteration.
            if mark in marks_in_progress:
                if now_ts - newest >= MARK_REFRESH_SEC:
                    status_db.refresh_curr_mark(instance_num=instance_num, mark=mark)
                continue

            # Clean the stale status records if no marked test was running for too long
            if now_ts - newest >= MARK_STALENESS_SEC:
                self.log(
                    f"c{instance_num}: no '{mark}' marked tests running "
                    "for a while, cleaning the mark status record"
                )
                self._on_marked_test_stop(instance_num=instance_num, mark=mark)

    def _resolve_resources_availability(
        self, cget_status: _ClusterGetStatus, scratch: _InstanceScratch
    ) -> bool:
        """Resolve availability of required "use" and "lock" resources."""
        instance_num = scratch.instance_num
        resources_locked = self.snap.get_resource_names(
            mode=status_db.MODE_LOCK, instance_num=instance_num
        )

        # This test wants to lock some resources, check if these are not in use
        res_lockable = []
        if cget_status.lock_resources:
            resources_used = self.snap.get_resource_names(
                mode=status_db.MODE_USE, instance_num=instance_num
            )
            unlockable_resources = {*resources_locked, *resources_used}
            res_lockable = resources_management.get_resources(
                resources=cget_status.lock_resources,
                unavailable=unlockable_resources,
            )
            if not res_lockable:
                self.log(
                    f"c{instance_num}: want to lock '{cget_status.lock_resources}' and "
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
                    f"c{instance_num}: want to use '{cget_status.use_resources}' and "
                    f"'{resources_locked}' are locked, cannot start"
                )
                return False

        # Resources that are locked are also in use
        use_minus_lock = list(set(res_usable) - set(res_lockable))

        scratch.final_use_resources = use_minus_lock
        scratch.final_lock_resources = res_lockable

        self.log(
            f"c{instance_num}: can start, resources '{use_minus_lock}' usable, "
            f"resources '{res_lockable}' lockable"
        )

        return True

    def _is_already_running(self) -> bool:
        """Check if the test is already setup and running."""
        tests_on_worker = self.snap.list_test_running(worker_id=self.worker_id)

        # Test is already running, nothing to set up
        if tests_on_worker and self._cluster_instance_num != -1:
            self.log(f"test '{tests_on_worker[0].test_id}' already running on this worker")
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
            and self.snap.list_prio_in_progress()
        ):
            self.log("'prio' test setup in progress, cannot continue")
            return True

        return False

    def _init_prio(self, cget_status: _ClusterGetStatus) -> None:
        """Set "prio" for this test if indicated."""
        if not cget_status.prio:
            return

        status_db.create_prio_in_progress(worker_id=self.worker_id)
        cget_status.prio_here = True
        self.log(f"setting 'prio' for '{cget_status.current_test}'")

    def _being_respun_by_other_worker(
        self, cget_status: _ClusterGetStatus, instance_num: int
    ) -> bool:
        """Check if the cluster is currently being respun by worker other than this one."""
        if cget_status.owns_respin(instance_num):
            return False

        respin_in_progress = self.snap.list_respin_progress(instance_num=instance_num)
        return bool(respin_in_progress)

    def _marked_select_instance(
        self, cget_status: _ClusterGetStatus, scratch: _InstanceScratch
    ) -> bool:
        """Select this cluster instance for running marked tests if possible."""
        instance_num = scratch.instance_num

        if scratch.marked_ready_rows:
            cget_status.pin_instance(instance_num)
            self.log(
                f"c{instance_num}: locking to this cluster instance, "
                f"it has my mark '{cget_status.mark}'"
            )
            return True

        if cget_status.marked_running_my_anywhere and not cget_status.owns_respin(instance_num):
            self.log(
                f"c{instance_num}: tests marked with my mark '{cget_status.mark}' "
                "already running on other cluster instance, cannot start"
            )
            return False

        # If here, this will be the first test with the mark.
        # A worker that is respinning this instance proceeds as the first test even when
        # the mark is seen on another instance: its own mark records were removed when the
        # respin was initiated, and another worker of the group (e.g. after the original
        # worker died and xdist re-queued the remaining tests) could have claimed the mark
        # elsewhere in the meantime. Waiting for the other instance would stall this worker
        # forever - it is pinned to this instance by the respin. The mark then exists on
        # two instances, which the framework already tolerates; it just means the expensive
        # setup is done twice.
        return True

    def _gc_stale_records(self) -> None:
        """Garbage collect status records left by crashed pytest workers.

        Remove the stale records so they don't block cluster instances and resources forever.
        Runs at most once per `GC_INTERVAL_SEC` across all workers.

        Must be called under the global cluster lock.
        """
        for gc_msg in status_db.gc_stale_records(min_interval_sec=GC_INTERVAL_SEC):
            self.log(f"GC: removed stale {gc_msg}")

    def _check_dead_fraction(self, max_dead_fraction: float) -> None:
        """Fail if the fraction of dead cluster instances is too high."""
        total = self.num_of_instances
        if total == 0:
            msg = "Number of cluster instances must be greater than 0."
            raise ValueError(msg)
        dead_count = len(self.snap.list_cluster_dead())
        dead_fraction = dead_count / total

        if dead_fraction >= max_dead_fraction:
            if dead_count == total:
                msg = "All cluster instances are dead."
            else:
                msg = (
                    "Too many cluster instances are dead: "
                    f"{dead_count} out of {total} "
                    f"({dead_fraction:.0%} dead, "
                    f"maximum allowed: {max_dead_fraction:.0%})."
                )
            raise RuntimeError(msg)

    def _fail_on_dead_clusters(self, remaining_time_sec: float) -> None:
        """Fail based on how many cluster instances are dead and time left.

        Use a stricter failure threshold as we approach the deadline.
        If we've been waiting a long time and too many cluster instances are dead,
        it's better to fail than continue trying with too few usable instances.
        """
        if remaining_time_sec <= self.strict_check_window:
            max_dead_fraction = self.strict_dead_fraction
        else:
            # Early in the wait period we only fail if all instances are dead.
            max_dead_fraction = 1.0

        self._check_dead_fraction(max_dead_fraction)

    def _rm_marks(self, instance_num: int, mark: str = "*") -> list[status_db.StatusRow]:
        """Remove status records of marks on the cluster instance.

        By default, records of all marks on the instance are removed. Pass `mark` to
        remove records of a single mark.

        The marked resource records must be removed together with the "current mark"
        records: with the "current mark" records gone, the mark staleness handling
        cannot see the marks anymore, so any marked resource records left behind would
        stay locked or in-use for the rest of the testrun.

        The "respin after mark" records are removed without converting them to "needs
        respin". The removed records are returned, so `_on_marked_test_stop` can do the
        conversion itself; the other callers respin the cluster instance (or the
        instance is dead), so the promised respin is either scheduled or moot. Note that
        when the respinning test belongs to the marked group, the promise is re-created
        only when the test itself requests cleanup - which holds as long as all tests of
        a group pass the same arguments, as they are supposed to.

        Returns:
            The removed "respin after mark" records.
        """
        if not mark:
            msg = "`mark` must be '*' or a non-empty mark."
            raise ValueError(msg)

        status_db.rm_curr_mark(instance_num=instance_num, mark=mark)
        respin_after_mark = status_db.rm_respin_after_mark(instance_num=instance_num, mark=mark)
        status_db.rm_resources(mode=status_db.MODE_LOCK, instance_num=instance_num, mark=mark)
        status_db.rm_resources(mode=status_db.MODE_USE, instance_num=instance_num, mark=mark)

        return respin_after_mark

    def _cleanup_dead_cluster(self, cget_status: _ClusterGetStatus, instance_num: int) -> None:
        """Cleanup if the selected cluster instance failed to start."""
        # Release this worker's claim on the instance and move on to another one
        cget_status.release_instance()

        self._rm_marks(instance_num=instance_num)

        # Remove the respin status records of the whole instance. The instance is dead
        # and stays dead, so the records are of no use to any worker - the respin was
        # abandoned and no new one can be started on a dead instance. Removing records
        # of other workers is safe for the same reason.
        status_db.rm_respin_progress(instance_num=instance_num)
        status_db.rm_respin_needed(instance_num=instance_num)

    def _init_respin(
        self, cget_status: _ClusterGetStatus, scratch: _InstanceScratch
    ) -> _InstanceDecision:
        """Initialize respin of this cluster instance on this worker.

        Returns:
            The decision on the instance. START when the evaluation of the instance can
            continue, DEFER when the respin was claimed but the instance must be
            re-evaluated before the test can start on it, SKIP when the needed respin
            cannot be done right now.
        """
        instance_num = scratch.instance_num

        # Respin already claimed by this worker
        if cget_status.owns_respin(instance_num):
            return _InstanceDecision.start()

        if not (scratch.cluster_needs_respin or self._test_needs_respin(cget_status, scratch)):
            return _InstanceDecision.start()

        # If tests are running on the instance, we cannot respin, therefore we cannot continue
        if scratch.started_tests_rows:
            self.log(f"c{instance_num}: tests are running, cannot respin")
            return _InstanceDecision.skip()

        self.log(f"c{instance_num}: setting 'respin in progress'")

        # Cluster respin will be performed by this worker.
        # By claiming the respin, we make sure this worker continues on this cluster instance
        # after respin is finished. It is important because the `scriptsdir` used for starting the
        # cluster instance might be specific for the test.
        # The durable record is created first, the in-memory state is updated only after
        # that succeeded - same convention as in `_cleanup_after_respin`.
        status_db.create_respin_progress(instance_num=instance_num, worker_id=self.worker_id)
        cget_status.claim_respin(instance_num)

        # Remove mark status records and marked resource records as these will not be
        # valid after respin
        self._rm_marks(instance_num=instance_num)

        if scratch.marked_ready_rows:
            # This test's own mark was just removed and the group's resource records went
            # with it. Return back to the retry loop, so that this cluster instance is
            # re-evaluated - the findings of this evaluation are discarded with the
            # scratch object - and this test resolves its resources again, as the initial
            # test of the mark.
            # Record that the instance needs the respin: the re-evaluation happens on a
            # later iteration, after the global lock was released, and it can be delayed
            # e.g. by a resource conflict. The durable record keeps the respin scheduled
            # even when this worker doesn't get to it right away.
            status_db.create_respin_needed(instance_num=instance_num, worker_id=self.worker_id)
            return _InstanceDecision.defer()

        return _InstanceDecision.start()

    def _arm_respin(self, claim: _RespinClaim) -> None:
        """Arm the claimed respin so `_respin` runs on the next iteration.

        The actual `_respin` function will be called outside of global lock so other
        workers don't need to wait.
        """
        if claim.phase is not _RespinPhase.CLAIMED:
            msg = f"Cannot arm respin in the '{claim.phase.name}' state."
            raise RuntimeError(msg)

        # NOTE: when `_respin` is called, the env variables needed for cluster start scripts need
        # to be already set (e.g. CARDANO_NODE_SOCKET_PATH)
        self.log(f"c{claim.instance_num}: ready to respin cluster")
        claim.phase = _RespinPhase.ARMED

    def _respin_if_armed(self, cget_status: _ClusterGetStatus) -> None:
        """Run the armed respin and record that it was performed.

        Called outside of the global lock so other workers don't need to wait.
        The move to RESPUN right after `_respin` returns makes sure the cluster is
        restarted exactly once per claim, even when a later iteration re-enters
        before the cleanup. The move happens also when the respin fails - the
        failure marks the cluster dead and the dead cluster check handles the
        recovery on the next iteration.
        """
        claim = cget_status.respin
        if claim is None or claim.phase is not _RespinPhase.ARMED:
            return

        self._respin(scriptsdir=cget_status.scriptsdir)
        claim.phase = _RespinPhase.RESPUN

    def _cleanup_after_respin(self, cget_status: _ClusterGetStatus) -> None:
        """Clean up after the respin of this cluster instance was performed.

        Must be called only in the RESPUN phase, after `_respin` has run: the respin
        status records are removed for the whole instance, so a premature call would
        delete another worker's records and break the cross-worker coordination.
        """
        claim = cget_status.respin
        if claim is None or claim.phase is not _RespinPhase.RESPUN:
            msg = f"Cannot clean up after respin in the '{cget_status.respin_phase_name}' state."
            raise RuntimeError(msg)

        # Remove status records that are no longer valid after respin
        status_db.rm_respin_progress(instance_num=claim.instance_num)
        status_db.rm_respin_needed(instance_num=claim.instance_num)

        cget_status.respin = None

    def _init_marked_test(self, cget_status: _ClusterGetStatus, instance_num: int) -> None:
        """Create status record for marked test."""
        if not cget_status.mark:
            return

        status_db.create_curr_mark(
            instance_num=instance_num, worker_id=self.worker_id, mark=cget_status.mark
        )

    def _create_test_status_records(
        self, cget_status: _ClusterGetStatus, scratch: _InstanceScratch
    ) -> None:
        """Create status records for test that is about to start on this cluster instance."""
        instance_num = scratch.instance_num

        # Create status record for each in-use resource
        status_db.create_resources(
            instance_num=instance_num,
            worker_id=self.worker_id,
            names=scratch.final_use_resources,
            mode=status_db.MODE_USE,
            mark=cget_status.mark,
        )

        # Create status record for each locked resource
        status_db.create_resources(
            instance_num=instance_num,
            worker_id=self.worker_id,
            names=scratch.final_lock_resources,
            mode=status_db.MODE_LOCK,
            mark=cget_status.mark,
        )

        # Cleanup = cluster respin after test (group of tests) is finished
        if cget_status.cleanup:
            # Cleanup after group of test that are marked with a marker
            if cget_status.mark:
                self.log(f"c{instance_num}: cleanup and mark")
                status_db.create_respin_after_mark(
                    instance_num=instance_num,
                    worker_id=self.worker_id,
                    mark=cget_status.mark,
                )
            # Cleanup after single test (e.g. singleton)
            else:
                self.log(f"c{instance_num}: cleanup and not mark")
                status_db.create_respin_needed(instance_num=instance_num, worker_id=self.worker_id)

        self.log(f"c{self.cluster_instance_num}: creating 'test running' status record")
        status_db.create_test_running(
            instance_num=self.cluster_instance_num,
            worker_id=self.worker_id,
            # Record the name of the test that is starting on this cluster instance, leave out the
            # '(setup)' part
            test_id=cget_status.current_test.split(" ")[0],
            mark=cget_status.mark,
        )

    def _make_instances_order(
        self,
        available_instances: list[int],
        lock_resources: tp.Collection[tp.Any],
        use_resources: tp.Collection[tp.Any],
    ) -> tuple[int, ...]:
        """Return a randomized iteration order over cluster instances.

        Light tests (no `lock_resources`, only `CLUSTER` in `use_resources`) get the tail
        (last two instances) first so heavy tests have a better chance to claim the head
        instances. Earlier instances would otherwise fill up with light tests, leaving
        heavy tests unable to find a free instance.
        """
        tail_size = min(2, self.num_of_instances)
        head_size = self.num_of_instances - tail_size
        head_sample = random.sample(available_instances[:head_size], head_size)
        # Iterate tail instances in fixed order so light tests pack onto the first
        # tail instance up to `configuration.MAX_TESTS_PER_CLUSTER` before spilling
        # onto the next. This keeps light load concentrated on a single tail instance
        # for as long as possible, leaving more head instances free for heavy tests.
        tail_instances = available_instances[head_size:]
        light_test = not lock_resources and len(use_resources) == 1
        return (*tail_instances, *head_sample) if light_test else (*head_sample, *tail_instances)

    def _init_use_resources(
        self,
        lock_resources: resources_management.ResourcesType,
        use_resources: resources_management.ResourcesType,
    ) -> list[str | resources_management.ResourceFilter]:
        """Add `resources.Resources.CLUSTER` to `use_resources`.

        Filter out `lock_resources` from the list of `use_resources`.
        """
        lock_named = {r for r in lock_resources if isinstance(r, str)}

        # Materialize the iterable - a one-shot iterator would be exhausted after the
        # first of the two passes below
        use_resources = list(use_resources)

        use_named = {r for r in use_resources if isinstance(r, str)}
        use_w_filter = [r for r in use_resources if not isinstance(r, str)]

        use_named.add(resources.Resources.CLUSTER)

        use_minus_lock = use_named - lock_named
        use_resources = [*use_minus_lock, *use_w_filter]

        return use_resources

    def _evaluate_instance(  # noqa: PLR0911
        self, cget_status: _ClusterGetStatus, instance_num: int
    ) -> tuple[_InstanceDecision, _InstanceScratch]:
        """Evaluate if the cluster instance is suitable for running the current test.

        Runs the checks in order and rules the instance out on the first failed one.
        Besides recording what they found out in the returned scratch object, the checks
        can also modify durable status records - e.g. wipe records of a dead instance, or
        claim a needed respin - as well as the worker state. A non-START verdict can
        therefore leave the worker pinned to this instance with a claimed respin, to be
        re-evaluated on a later iteration.

        Must be called under the global lock.

        Returns:
            The decision on the instance and the findings of the evaluation. The
            decision is START when the test can start on this instance, SKIP when the
            loop should move on to the next instance, DEFER when it should return to the
            top-level loop and re-enter this pinned instance later.
        """
        scratch = _InstanceScratch(instance_num=instance_num)
        status_files.get_instance_dir(instance_num=instance_num).mkdir(exist_ok=True)

        # Cleanup cluster instance where attempt to start cluster failed repeatedly
        if self.snap.is_cluster_dead(instance_num=instance_num):
            self._cleanup_dead_cluster(cget_status, instance_num=instance_num)
            return _InstanceDecision.skip(), scratch

        # Cluster respin planned or in progress, so no new tests can start
        if self._being_respun_by_other_worker(cget_status, instance_num=instance_num):
            return _InstanceDecision.skip(LONG_BACKOFF_SEC), scratch

        # If marked tests are already running, update their status.
        # Must be done before the mark state is read below, as stale marks
        # get cleaned up here.
        self._update_marked_tests(instance_num=instance_num)

        # Are there tests already running on this cluster instance?
        scratch.started_tests_rows = self.snap.list_test_running(instance_num=instance_num)

        # "marked tests" = group of tests marked with my mark
        scratch.marked_ready_rows = self.snap.list_curr_mark(
            instance_num=instance_num, mark=cget_status.mark
        )

        # If there would be more tests running on this cluster instance than allowed,
        # we need to wait.
        if (
            self.num_of_instances > 1
            and (tnum := len(scratch.started_tests_rows)) >= configuration.MAX_TESTS_PER_CLUSTER
        ):
            self.log(f"c{instance_num}: {tnum} tests are already running, cannot start")
            return _InstanceDecision.skip(SHORT_BACKOFF_SEC), scratch

        # Does the cluster instance need respin to continue?
        # Cache the result as the check itself can be expensive.
        scratch.cluster_needs_respin = self._cluster_needs_respin(instance_num)

        # Select this instance for running marked tests if possible
        if cget_status.mark and not self._marked_select_instance(cget_status, scratch):
            return _InstanceDecision.skip(SHORT_BACKOFF_SEC), scratch

        # Try next cluster instance when the current one needs respin.
        # Respin only if:
        # * we are already locked on this instance
        # * respin is needed by the current test anyway
        # * we already tried all cluster instances and there's no other option
        if (
            scratch.cluster_needs_respin
            and cget_status.selected_instance != instance_num
            and not self._test_needs_respin(cget_status, scratch)
            and not cget_status.tried_all_instances
        ):
            return _InstanceDecision.skip(), scratch

        # We don't need to resolve resources availability if there was already a test
        # with this mark before (the first test already resolved the resources
        # availability).
        # It is responsibility of tests to make sure that the same resources are
        # requested for all the tests with the same mark (e.g. specific pool and
        # not "any pool").
        # A worker that owns the respin claim on this instance is the initial test of the
        # mark, even when it sees a "current mark" record - the record is the one it
        # created itself when it selected the instance for the respin (see
        # `_init_marked_test`). It must resolve its resources here, otherwise it would
        # start with no resource records at all and other workers would see the resources
        # it locked as free.
        need_resolve_resources = not scratch.marked_ready_rows or cget_status.owns_respin(
            instance_num
        )

        # Check availability of the required resources
        if need_resolve_resources and not self._resolve_resources_availability(
            cget_status, scratch
        ):
            return _InstanceDecision.skip(LONG_BACKOFF_SEC), scratch

        # If respin is needed, indicate that the cluster will be re-spun
        # (after all currently running tests are finished)
        return self._init_respin(cget_status, scratch), scratch

    def _prepare_selected_instance(
        self, cget_status: _ClusterGetStatus, scratch: _InstanceScratch
    ) -> _InstanceDecision:
        """Select the evaluated cluster instance and finish respin-related actions.

        Pins the worker to the instance, sets the cluster env variables, removes the
        "prio in progress" record and creates the "current mark" record for a marked
        test.

        Must be called under the global lock.

        Returns:
            START when the selection is finished and the test can start. DEFER when
            the claimed respin was just armed - the instance stays pinned and is
            re-entered on a later iteration, after the respin was performed.
        """
        claim = cget_status.respin

        # An armed respin must run (`_respin_if_armed`) before the instance can be
        # selected - finishing the selection here would skip the promised respin
        if claim is not None and claim.phase is _RespinPhase.ARMED:
            msg = f"Cannot select instance in the '{claim.phase.name}' respin state."
            raise RuntimeError(msg)

        instance_num = scratch.instance_num

        # We've found suitable cluster instance
        cget_status.pin_instance(instance_num)
        self._cluster_instance_num = instance_num
        self.log(f"c{instance_num}: can run test '{cget_status.current_test}'")
        # Set environment variables that are needed when respinning the cluster
        # and running tests
        cluster_nodes.set_cluster_env(instance_num=instance_num)

        # Remove "prio" status record
        if cget_status.prio:
            status_db.rm_prio_in_progress(worker_id=self.worker_id)

        # Create status record for marked tests.
        # This must be done before the cluster is re-spun, so that other marked tests
        # don't try to prepare another cluster instance.
        self._init_marked_test(cget_status, instance_num=instance_num)

        # Arm the claimed respin and return to the top-level loop, where
        # `_respin` runs outside of the global lock
        if claim is not None and claim.phase is _RespinPhase.CLAIMED:
            self._arm_respin(claim)
            return _InstanceDecision.defer()

        # The respin was already performed, clean up the respin status records
        if claim is not None and claim.phase is _RespinPhase.RESPUN:
            self._cleanup_after_respin(cget_status)

        return _InstanceDecision.start()

    def _try_instances(
        self, cget_status: _ClusterGetStatus, instances_order: tp.Iterable[int]
    ) -> _InstanceDecision:
        """Try the cluster instances in the given order until the test can start on one.

        Sets up the test on the first suitable cluster instance. Records that all
        instances were tried when none of them is usable in this iteration.

        Must be called under the global lock.

        Returns:
            START when the test was set up and can start, SKIP or DEFER when the
            top-level loop needs to wait and try again.
        """
        for instance_num in instances_order:
            # If instance to run the test on was already decided, skip all other instances
            if cget_status.selected_instance not in (-1, instance_num):
                continue

            # Check if the test can start on this instance
            decision, scratch = self._evaluate_instance(cget_status, instance_num=instance_num)

            # Select the instance. A freshly claimed respin is only armed here
            # and the instance is re-entered after the respin was performed.
            if decision.verdict is _InstanceVerdict.START:
                decision = self._prepare_selected_instance(cget_status, scratch)

            if decision.verdict is _InstanceVerdict.START:
                # From this point on, all conditions needed to start the test are met
                self._create_test_status_records(cget_status, scratch)
                return decision

            # The worker is pinned to this instance, no other one is an option
            if decision.verdict is _InstanceVerdict.DEFER:
                return decision

            # Back off before the next attempt as the verdict asks. The delay is sticky -
            # it stays in effect until another verdict asks for a different one - and this
            # is the only place where a verdict on an instance sets it.
            if decision.backoff_sec:
                cget_status.sleep_delay = decision.backoff_sec

        # The test cannot start on any instance, return to the top-level loop
        cget_status.tried_all_instances = True
        return _InstanceDecision.skip()

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
                cluster instance. Mark the tests also with
                `@pytest.mark.xdist_group("<same name>")` so they are scheduled together
                on a single pytest worker - the mark is considered abandoned and cleaned up
                (see `MARK_STALENESS_SEC`) when no marked test is running for too long.
                Where applicable, prefer subtests (`pytest_subtests`) over `mark` - a single
                test with multiple subtests shares one setup with less magic. Subtests
                however don't work with `hypothesis` property based tests.
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

        # Materialize the iterable - it is iterated multiple times below.
        # `use_resources` is materialized by `_init_use_resources`, where its double
        # iteration lives.
        lock_resources = list(lock_resources)

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

        self.log(f"want to run test '{cget_status.current_test}'")

        # Iterate until it is possible to start the test. Timeout after grace period.
        now = time.monotonic()
        deadline_soft = now + self.grace_period_soft
        deadline_hard = now + self.grace_period_hard
        while True:
            now = time.monotonic()
            remaining_soft = deadline_soft - now
            remaining_hard = deadline_hard - now

            # Timeout after soft grace period if no cluster instance was selected yet
            if cget_status.selected_instance == -1 and remaining_soft <= 0:
                msg = "Timeout (soft) while waiting to obtain cluster instance."
                raise TimeoutError(msg)
            # Timeout after hard grace period even if cluster instance was already selected
            if remaining_hard <= 0:
                msg = "Timeout (hard) while waiting to obtain cluster instance."
                raise TimeoutError(msg)

            self._respin_if_armed(cget_status)

            # Sleep for a while to avoid too many checks in a short time
            _xdist_sleep(random.uniform(0.6, 1.2) * cget_status.sleep_delay)
            cget_status.sleep_delay = max(cget_status.sleep_delay, 1)

            # Compute the instance iteration order outside the lock to keep the locked
            # section as short as possible.
            instances_order = self._make_instances_order(
                available_instances=available_instances,
                lock_resources=lock_resources,
                use_resources=use_resources,
            )

            # Nothing time consuming can go under this lock as all other workers will need to wait
            with locking.FileLockIfXdist(self.cluster_lock):
                # Load status records that were modified by other workers while the lock
                # was not held. All status reads in this iteration are then answered from
                # the snapshot - it refreshes itself after writes done by this process.
                self.snap.refresh()

                if self._is_already_running():
                    return self.cluster_instance_num

                self._gc_stale_records()

                self._fail_on_dead_clusters(remaining_time_sec=remaining_soft)

                if cget_status.mark:
                    # Check if tests with my mark are already locked to any cluster instance
                    cget_status.marked_running_my_anywhere = self.snap.list_curr_mark(
                        mark=cget_status.mark
                    )

                # A "prio" test has priority in obtaining cluster instance. Check if it is needed
                # to wait until earlier "prio" test obtains a cluster instance.
                if self._wait_for_prio(cget_status):
                    cget_status.sleep_delay = LONG_BACKOFF_SEC
                    continue

                # Set "prio" for this test if indicated
                self._init_prio(cget_status)

                self._cluster_instance_num = -1

                # Try all existing cluster instances in the precomputed order
                decision = self._try_instances(cget_status, instances_order=instances_order)
                if decision.verdict is not _InstanceVerdict.START:
                    continue

                # Cluster instance is ready, we can start the test
                break

        return self.cluster_instance_num
