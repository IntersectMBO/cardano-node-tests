"""High-level management of cluster instances.

This module provides the `ClusterManager` class, which is the main interface for tests to get a
fully initialized cluster instance. The `ClusterManager` is responsible for selecting an available
cluster instance that meets the test's resource requirements, preparing the `clusterlib` object,
and performing cleanup actions after the test has finished.

The `ClusterManager` is instantiated by the `cluster_manager` fixture for each test worker and is
used by the `cluster` fixture to get a cluster instance for a test.
"""

import contextlib
import dataclasses
import datetime
import hashlib
import inspect
import logging
import os
import pathlib as pl
import shutil
import typing as tp

from _pytest.config import Config
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cache
from cardano_node_tests.cluster_management import cluster_getter
from cardano_node_tests.cluster_management import common
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.cluster_management import status_files
from cardano_node_tests.utils import artifacts
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import cluster_scripts
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools
from cardano_node_tests.utils import types as ttypes

LOGGER = logging.getLogger(__name__)

T = tp.TypeVar("T")

if configuration.CLUSTERS_COUNT > 1 and configuration.DEV_CLUSTER_RUNNING:
    msg = "Cannot run multiple cluster instances when 'DEV_CLUSTER_RUNNING' is set."
    raise RuntimeError(msg)


def _get_manager_fixture_line_str() -> str:
    """Get `filename#lineno` of current fixture, called from contextmanager."""
    # Get past `cache_fixture` and `contextmanager` to the fixture
    calling_frame = inspect.currentframe().f_back.f_back.f_back  # type: ignore
    if not calling_frame:
        msg = "Couldn't get the calling frame."
        raise ValueError(msg)
    return helpers.get_line_str_from_frame(frame=calling_frame)


@dataclasses.dataclass
class FixtureCache(tp.Generic[T]):
    """Cache for a fixture."""

    value: T | None


class ClusterManager:
    """Set of management methods for cluster instances."""

    def __init__(self, worker_id: str, pytest_config: Config) -> None:
        self.worker_id = worker_id
        self.pytest_config = pytest_config
        self.pytest_tmp_dir = temptools.get_pytest_root_tmp()

        if configuration.IS_XDIST:
            self.range_num = 5
            self.num_of_instances = configuration.CLUSTERS_COUNT
        else:
            self.range_num = 1
            self.num_of_instances = 1

        self.cluster_lock = f"{self.pytest_tmp_dir}/{common.CLUSTER_LOCK}"
        self.log_lock = f"{self.pytest_tmp_dir}/{common.LOG_LOCK}"

        self._cluster_instance_num = -1
        self._initialized = False

    @property
    def cluster_instance_num(self) -> int:
        if self._cluster_instance_num == -1:
            msg = "Cluster instance not set."
            raise RuntimeError(msg)
        return self._cluster_instance_num

    @property
    def cache(self) -> cache.ClusterManagerCache:
        return cache.CacheManager.get_instance_cache(instance_num=self.cluster_instance_num)

    @property
    def instance_dir(self) -> pl.Path:
        return status_files.get_instance_dir(instance_num=self.cluster_instance_num)

    def log(self, msg: str) -> None:
        """Log a message."""
        if not configuration.SCHEDULING_LOG:
            return

        with (
            locking.FileLockIfXdist(self.log_lock),
            open(configuration.SCHEDULING_LOG, "a", encoding="utf-8") as logfile,
        ):
            logfile.write(
                f"{datetime.datetime.now(tz=datetime.timezone.utc)} on {self.worker_id}: {msg}\n"
            )

    def save_worker_cli_coverage(self) -> None:
        """Save CLI coverage info collected by this pytest worker.

        Must be done when session of the worker is about to finish, because there's no other job to
        call `_reload_cluster_obj` and thus save CLI coverage of the old `cluster_obj` instance.
        """
        self.log("called `save_worker_cli_coverage`")
        worker_cache = cache.CacheManager.get_cache()
        for cache_instance in worker_cache.values():
            cluster_obj = cache_instance.cluster_obj
            if not cluster_obj:
                continue

            artifacts.save_cli_coverage(cluster_obj=cluster_obj, pytest_config=self.pytest_config)

    def _is_valid_cluster_instance(self, work_dir: pl.Path, instance_num: int) -> bool:
        """Check if cluster instance is valid."""
        state_dir = work_dir / f"{cluster_nodes.STATE_CLUSTER}{instance_num}"
        cluster_stopped = status_files.get_cluster_stopped_file(instance_num=instance_num).exists()
        cluster_started_or_dead = (
            status_files.get_cluster_running_file(instance_num=instance_num).exists()
            or status_files.get_cluster_dead_file(instance_num=instance_num).exists()
        )

        if cluster_stopped or not cluster_started_or_dead:
            self.log(f"c{instance_num}: cluster instance not running")
            return False

        if not state_dir.exists():
            self.log(f"c{instance_num}: cluster instance state dir '{state_dir}' doesn't exist")
            return False

        if not status_files.get_started_by_framework_file(state_dir=state_dir).exists():
            self.log(f"c{instance_num}: cluster instance was not started by framework")
            return False

        return True

    def save_all_clusters_artifacts(self) -> None:
        """Save artifacts of all cluster instances."""
        self.log("called `save_all_clusters_artifacts`")

        if configuration.DEV_CLUSTER_RUNNING:
            LOGGER.warning(
                "Ignoring request to save all clusters artifacts as 'DEV_CLUSTER_RUNNING' is set."
            )
            return

        work_dir = cluster_nodes.get_cluster_env().work_dir

        for instance_num in range(self.num_of_instances):
            state_dir = work_dir / f"{cluster_nodes.STATE_CLUSTER}{instance_num}"
            if not self._is_valid_cluster_instance(work_dir=work_dir, instance_num=instance_num):
                continue

            artifacts.save_start_script_coverage(
                log_file=state_dir / common.START_CLUSTER_LOG,
                pytest_config=self.pytest_config,
            )
            artifacts.save_cluster_artifacts(save_dir=self.pytest_tmp_dir, state_dir=state_dir)

    def stop_all_clusters(self) -> None:
        """Stop all cluster instances."""
        self.log("called `stop_all_clusters`")

        # Don't stop cluster if it was started outside of test framework
        if configuration.DEV_CLUSTER_RUNNING:
            LOGGER.warning("Ignoring request to stop clusters as 'DEV_CLUSTER_RUNNING' is set.")
            return

        work_dir = cluster_nodes.get_cluster_env().work_dir

        for instance_num in range(self.num_of_instances):
            state_dir = work_dir / f"{cluster_nodes.STATE_CLUSTER}{instance_num}"
            if not self._is_valid_cluster_instance(work_dir=work_dir, instance_num=instance_num):
                continue

            stop_script = state_dir / cluster_scripts.STOP_SCRIPT
            if not stop_script.exists():
                self.log(f"c{instance_num}: stop script doesn't exist!")
                continue

            self.log(f"c{instance_num}: stopping cluster instance with `{stop_script}`")
            try:
                helpers.run_command(str(stop_script))
            except Exception as err:
                self.log(f"c{instance_num}: failed to stop cluster:\n{err}")

            shutil.rmtree(state_dir, ignore_errors=True)

            status_files.create_cluster_stopped_file(instance_num=instance_num)
            self.log(f"c{instance_num}: stopped cluster instance")

    def set_needs_respin(self) -> None:
        """Indicate that the cluster instance needs respin."""
        with locking.FileLockIfXdist(self.cluster_lock):
            self.log(f"c{self.cluster_instance_num}: called `set_needs_respin`")
            status_files.create_respin_needed_file(
                instance_num=self.cluster_instance_num, worker_id=self.worker_id
            )

    @contextlib.contextmanager
    def respin_on_failure(self) -> tp.Iterator[None]:
        """Indicate that the cluster instance needs respin if command failed - context manager."""
        try:
            yield
        except Exception:
            self.set_needs_respin()
            raise

    @contextlib.contextmanager
    def cache_fixture(self, key: str = "") -> tp.Iterator[FixtureCache[tp.Any]]:
        """Cache fixture value - context manager."""
        key_str = key or _get_manager_fixture_line_str()
        key_hash = int(hashlib.sha1(key_str.encode("utf-8")).hexdigest(), 16)
        cached_value = self.cache.test_data.get(key_hash)
        container: FixtureCache[tp.Any] = FixtureCache(value=cached_value)

        yield container

        if container.value != cached_value:
            self.cache.test_data[key_hash] = container.value

    def get_logfiles_errors(self) -> str:
        """Get errors found in cluster artifacts."""
        if self._cluster_instance_num == -1:
            return ""

        self.log(f"c{self._cluster_instance_num}: called `get_logfiles_errors`")
        return logfiles.get_logfiles_errors()

    def on_test_stop(self) -> None:
        """Perform actions after a test is finished."""
        if self._cluster_instance_num == -1:
            return

        current_test = os.environ.get("PYTEST_CURRENT_TEST") or ""
        self.log(f"c{self._cluster_instance_num}: called `on_test_stop` for '{current_test}'")

        with locking.FileLockIfXdist(self.cluster_lock):
            # Delete an "ignore errors" rules file that was created for the current pytest worker.
            # There's only one test running on a worker at a time. Deleting the corresponding rules
            # file right after a test is finished is therefore safe. The effect is that the rules
            # apply only from the time they were added (by `logfiles.add_ignore_rule`) until the end
            # of the test.
            # However sometimes we don't want to remove the rules file. Imagine situation when test
            # failed and cluster instance needs to be respun. The failed test already finished,
            # but other tests are still running and need to finish first before respin can happen.
            # If the ignored error continues to get printed into log file, tests that are still
            # running on the cluster instance would report that error. Therefore if the cluster
            # instance is scheduled for respin, don't delete the rules file.
            if not status_files.list_respin_needed_files(instance_num=self.cluster_instance_num):
                logfiles.clean_ignore_rules(ignore_file_id=self.worker_id)

            # Remove "resource locked" files created by the worker, ignore resources that have mark
            status_files.rm_resource_locked_files(
                instance_num=self.cluster_instance_num, worker_id=self.worker_id, mark=""
            )

            # Remove "resource used" files created by the worker, ignore resources that have mark
            status_files.rm_resource_used_files(
                instance_num=self.cluster_instance_num, worker_id=self.worker_id, mark=""
            )

            # Remove file that indicates that a test is running on the worker
            status_files.rm_test_running_files(
                instance_num=self.cluster_instance_num, worker_id=self.worker_id
            )

            # Log names of tests that keep running on the cluster instance
            tnames = status_files.get_test_names(instance_num=self.cluster_instance_num)
            self.log(f"c{self._cluster_instance_num}: running tests: {tnames}")

    def _get_resources_from_paths(
        self,
        paths: tp.Iterable[pl.Path],
        from_set: tp.Iterable[str] | None = None,
    ) -> list[str]:
        if from_set is not None and isinstance(from_set, str):
            msg = "`from_set` cannot be a string"
            raise TypeError(msg)

        resources = set(status_files.get_resources_from_path(paths=paths))

        if from_set is not None:
            return list(resources.intersection(from_set))

        return list(resources)

    def get_locked_resources(
        self,
        from_set: tp.Iterable[str] | None = None,
        worker_id: str | None = None,
    ) -> list[str]:
        """Get resources locked by worker.

        It is possible to use glob patterns for `worker_id` (e.g. `worker_id="*"`).
        """
        paths = status_files.list_resource_locked_files(
            instance_num=self.cluster_instance_num, worker_id=worker_id or self.worker_id
        )
        return self._get_resources_from_paths(paths=paths, from_set=from_set)

    def get_used_resources(
        self,
        from_set: tp.Iterable[str] | None = None,
        worker_id: str | None = None,
    ) -> list[str]:
        """Get resources used by worker.

        It is possible to use glob patterns for `worker_id` (e.g. `worker_id="*"`).
        """
        paths = status_files.list_resource_used_files(
            instance_num=self.cluster_instance_num, worker_id=worker_id or self.worker_id
        )
        return self._get_resources_from_paths(paths=paths, from_set=from_set)

    def _save_cli_coverage(self) -> None:
        """Save CLI coverage info collected by this `cluster_obj` instance."""
        self.log("called `_save_cli_coverage`")
        cluster_obj = self.cache.cluster_obj
        if not cluster_obj:
            return

        artifacts.save_cli_coverage(cluster_obj=cluster_obj, pytest_config=self.pytest_config)

    def _reload_cluster_obj(self, state_dir: pl.Path) -> None:
        """Reload cluster instance data if necessary."""
        addrs_data_checksum = helpers.checksum(state_dir / cluster_nodes.ADDRS_DATA)
        # The checksum will not match when cluster was respun
        if addrs_data_checksum == self.cache.last_checksum:
            return

        # Save CLI coverage collected by the old `cluster_obj` instance
        self._save_cli_coverage()

        # Replace the old `cluster_obj` instance and reload data
        self.cache.cluster_obj = cluster_nodes.get_cluster_type().get_cluster_obj()
        self.cache.test_data = {}
        self.cache.addrs_data = cluster_nodes.load_addrs_data()
        self.cache.last_checksum = addrs_data_checksum

    def init(
        self,
        mark: str = "",
        lock_resources: resources_management.ResourcesType = (),
        use_resources: resources_management.ResourcesType = (),
        prio: bool = False,
        cleanup: bool = False,
        scriptsdir: ttypes.FileType = "",
    ) -> None:
        """Get an initialized cluster instance.

        This method will wait until a cluster instance is ready to be used.

        **IMPORTANT**: This method must be called before any other method of this class.
        """
        # Get number of initialized cluster instance once it is possible to start a test
        instance_num = cluster_getter.ClusterGetter(
            worker_id=self.worker_id,
            pytest_config=self.pytest_config,
            num_of_instances=self.num_of_instances,
            log_func=self.log,
        ).get_cluster_instance(
            mark=mark,
            lock_resources=lock_resources,
            use_resources=use_resources,
            prio=prio,
            cleanup=cleanup,
            scriptsdir=scriptsdir,
        )
        self._cluster_instance_num = instance_num

        # Reload cluster instance data if necessary
        state_dir = cluster_nodes.get_cluster_env().state_dir
        self._reload_cluster_obj(state_dir=state_dir)

        # Initialize `cardano_clusterlib.ClusterLib` object
        cluster_obj = self.cache.cluster_obj
        if not cluster_obj:
            msg = "`cluster_obj` not available, that cannot happen"
            raise RuntimeError(msg)
        cluster_obj.cluster_id = self.cluster_instance_num
        cluster_obj._cluster_manager = self  # type: ignore
        self._initialized = True

    def get(
        self,
        mark: str = "",
        lock_resources: resources_management.ResourcesType = (),
        use_resources: resources_management.ResourcesType = (),
        prio: bool = False,
        cleanup: bool = False,
        scriptsdir: ttypes.FileType = "",
        check_initialized: bool = True,
    ) -> clusterlib.ClusterLib:
        """Get `cardano_clusterlib.ClusterLib` object on an initialized cluster instance.

        Convenience method that calls `init`.
        """
        # Most of the time the initialization should be done just once per test. There is a risk of
        # using multiple `cluster` fixtures by single test and having a deadlock when different
        # fixtures want to lock the same resources.
        # If you've ran into this issue, check that all the fixtures you use in the test are using
        # the same `cluster` fixture.
        if check_initialized and self._initialized:
            msg = "Manager is already initialized"
            raise RuntimeError(msg)

        self.init(
            mark=mark,
            lock_resources=lock_resources,
            use_resources=use_resources,
            prio=prio,
            cleanup=cleanup,
            scriptsdir=scriptsdir,
        )

        cluster_obj = self.cache.cluster_obj
        if not cluster_obj:
            msg = "`cluster_obj` not available, that cannot happen"
            raise RuntimeError(msg)
        return cluster_obj
