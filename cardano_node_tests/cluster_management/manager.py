"""Functionality for managing cluster instances."""
import contextlib
import dataclasses
import datetime
import hashlib
import inspect
import logging
import os
import shutil
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Optional

from _pytest.config import Config
from _pytest.tmpdir import TempPathFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cache
from cardano_node_tests.cluster_management import cluster_getter
from cardano_node_tests.cluster_management import common
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.utils import artifacts
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import cluster_scripts
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)

if configuration.CLUSTERS_COUNT > 1 and configuration.DEV_CLUSTER_RUNNING:
    raise RuntimeError("Cannot run multiple cluster instances when 'DEV_CLUSTER_RUNNING' is set.")


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
class FixtureCache:
    """Cache for a fixture."""

    value: Any


class ClusterManager:
    """Set of management methods for cluster instances."""

    def __init__(
        self, tmp_path_factory: TempPathFactory, worker_id: str, pytest_config: Config
    ) -> None:
        self.worker_id = worker_id
        self.pytest_config = pytest_config
        self.tmp_path_factory = tmp_path_factory
        self.pytest_tmp_dir = temptools.get_pytest_root_tmp(tmp_path_factory)

        if configuration.IS_XDIST:
            self.range_num = 5
            self.num_of_instances = configuration.CLUSTERS_COUNT
        else:
            self.range_num = 1
            self.num_of_instances = 1

        self.cluster_lock = f"{self.pytest_tmp_dir}/{common.CLUSTER_LOCK}"
        self.log_lock = f"{self.pytest_tmp_dir}/{common.LOG_LOCK}"

        self._cluster_instance_num = -1

    @property
    def cluster_instance_num(self) -> int:
        if self._cluster_instance_num == -1:
            raise RuntimeError("Cluster instance not set.")
        return self._cluster_instance_num

    @property
    def cache(self) -> cache.ClusterManagerCache:
        return cache.CacheManager.get_instance_cache(self.cluster_instance_num)

    @property
    def instance_dir(self) -> Path:
        instance_dir = (
            self.pytest_tmp_dir / f"{common.CLUSTER_DIR_TEMPLATE}{self.cluster_instance_num}"
        )
        return instance_dir

    @property
    def ports(self) -> cluster_scripts.InstancePorts:
        """Return port mappings for current cluster instance."""
        return cluster_nodes.get_cluster_type().cluster_scripts.get_instance_ports(
            self.cluster_instance_num
        )

    def log(self, msg: str) -> None:
        """Log a message."""
        if not configuration.SCHEDULING_LOG:
            return

        with locking.FileLockIfXdist(self.log_lock), open(
            configuration.SCHEDULING_LOG, "a", encoding="utf-8"
        ) as logfile:
            logfile.write(f"{datetime.datetime.now()} on {self.worker_id}: {msg}\n")

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

    def stop_all_clusters(self) -> None:
        """Stop all cluster instances."""
        self.log("called `stop_all_clusters`")

        # don't stop cluster if it was started outside of test framework
        if configuration.DEV_CLUSTER_RUNNING:
            LOGGER.warning("Ignoring request to stop clusters as 'DEV_CLUSTER_RUNNING' is set.")
            return

        work_dir = cluster_nodes.get_cluster_env().work_dir

        for instance_num in range(self.num_of_instances):
            instance_dir = self.pytest_tmp_dir / f"{common.CLUSTER_DIR_TEMPLATE}{instance_num}"
            cluster_stopped = (instance_dir / common.CLUSTER_STOPPED_FILE).exists()
            cluster_started_or_dead = (instance_dir / common.CLUSTER_RUNNING_FILE).exists() or (
                instance_dir / common.CLUSTER_DEAD_FILE
            ).exists()

            if cluster_stopped or not cluster_started_or_dead:
                self.log(f"c{instance_num}: cluster instance not running")
                continue

            state_dir = work_dir / f"{cluster_nodes.STATE_CLUSTER}{instance_num}"

            if not state_dir.exists():
                self.log(f"c{instance_num}: cluster instance state dir '{state_dir}' doesn't exist")
                continue

            if not (state_dir / common.CLUSTER_STARTED_BY_FRAMEWORK).exists():
                self.log(f"c{instance_num}: cluster instance was not started by framework")
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

            artifacts.save_start_script_coverage(
                log_file=state_dir / common.CLUSTER_START_CMDS_LOG,
                pytest_config=self.pytest_config,
            )
            artifacts.save_cluster_artifacts(save_dir=self.pytest_tmp_dir, state_dir=state_dir)

            shutil.rmtree(state_dir, ignore_errors=True)

            (instance_dir / common.CLUSTER_STOPPED_FILE).touch()
            self.log(f"c{instance_num}: stopped cluster instance")

    def set_needs_respin(self) -> None:
        """Indicate that the cluster instance needs respin."""
        with locking.FileLockIfXdist(self.cluster_lock):
            self.log(f"c{self.cluster_instance_num}: called `set_needs_respin`")
            (self.instance_dir / f"{common.RESPIN_NEEDED_GLOB}_{self.worker_id}").touch()

    @contextlib.contextmanager
    def respin_on_failure(self) -> Iterator[None]:
        """Indicate that the cluster instance needs respin if command failed - context manager."""
        try:
            yield
        except Exception:
            self.set_needs_respin()
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
            if not list(self.instance_dir.glob(f"{common.RESPIN_NEEDED_GLOB}_*")):
                logfiles.clean_ignore_rules(ignore_file_id=self.worker_id)

            # remove resource locking files created by the worker, ignore resources that have mark
            resource_locking_files = list(
                self.instance_dir.glob(f"{common.RESOURCE_LOCKED_GLOB}_@@*@@_{self.worker_id}")
            )
            for f in resource_locking_files:
                f.unlink()

            # remove "resource in use" files created by the worker, ignore resources that have mark
            resource_in_use_files = list(
                self.instance_dir.glob(f"{common.RESOURCE_IN_USE_GLOB}_@@*@@_{self.worker_id}")
            )
            for f in resource_in_use_files:
                f.unlink()

            # remove file that indicates that a test is running on the worker
            list(self.instance_dir.glob(f"{common.TEST_RUNNING_GLOB}*_{self.worker_id}"))[0].unlink(
                missing_ok=True
            )

            # log names of tests that keep running on the cluster instance
            tnames = [
                tf.read_text().strip()
                for tf in self.instance_dir.glob(f"{common.TEST_RUNNING_GLOB}*")
            ]
            self.log(f"c{self._cluster_instance_num}: running tests: {tnames}")

    def _get_resources_by_glob(
        self,
        glob: str,
        from_set: Optional[Iterable[str]] = None,
    ) -> List[str]:
        if from_set is not None and isinstance(from_set, str):
            raise AssertionError("`from_set` cannot be a string")

        resources_locked = set(common._get_resources_from_paths(paths=self.instance_dir.glob(glob)))

        if from_set is not None:
            return list(resources_locked.intersection(from_set))

        return list(resources_locked)

    def get_locked_resources(
        self,
        from_set: Optional[Iterable[str]] = None,
        worker_id: Optional[str] = None,
    ) -> List[str]:
        """Get resources locked by worker.

        It is possible to use glob patterns for `worker_id` (e.g. `worker_id="*"`).
        """
        glob = f"{common.RESOURCE_LOCKED_GLOB}_@@*@@_*{worker_id or self.worker_id}"
        return self._get_resources_by_glob(glob=glob, from_set=from_set)

    def get_used_resources(
        self,
        from_set: Optional[Iterable[str]] = None,
        worker_id: Optional[str] = None,
    ) -> List[str]:
        """Get resources used by worker.

        It is possible to use glob patterns for `worker_id` (e.g. `worker_id="*"`).
        """
        glob = f"{common.RESOURCE_IN_USE_GLOB}_@@*@@_*{worker_id or self.worker_id}"
        return self._get_resources_by_glob(glob=glob, from_set=from_set)

    def _save_cli_coverage(self) -> None:
        """Save CLI coverage info collected by this `cluster_obj` instance."""
        self.log("called `_save_cli_coverage`")
        cluster_obj = self.cache.cluster_obj
        if not cluster_obj:
            return

        artifacts.save_cli_coverage(cluster_obj=cluster_obj, pytest_config=self.pytest_config)

    def _reload_cluster_obj(self, state_dir: Path) -> None:
        """Reload cluster instance data if necessary."""
        addrs_data_checksum = helpers.checksum(state_dir / cluster_nodes.ADDRS_DATA)
        # the checksum will not match when cluster was respun
        if addrs_data_checksum == self.cache.last_checksum:
            return

        # save CLI coverage collected by the old `cluster_obj` instance
        self._save_cli_coverage()

        # replace the old `cluster_obj` instance and reload data
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
        start_cmd: str = "",
    ) -> None:
        """Get an initialized cluster instance.

        This method will wait until a cluster instance is ready to be used.

        **IMPORTANT**: This method must be called before any other method of this class.
        """
        # get number of initialized cluster instance once it is possible to start a test
        instance_num = cluster_getter.ClusterGetter(
            tmp_path_factory=self.tmp_path_factory,
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
            start_cmd=start_cmd,
        )
        self._cluster_instance_num = instance_num

        # reload cluster instance data if necessary
        state_dir = cluster_nodes.get_cluster_env().state_dir
        self._reload_cluster_obj(state_dir=state_dir)

        # initialize `cardano_clusterlib.ClusterLib` object
        cluster_obj = self.cache.cluster_obj
        if not cluster_obj:
            raise AssertionError("`cluster_obj` not available, that cannot happen")
        cluster_obj.cluster_id = self.cluster_instance_num
        cluster_obj._cluster_manager = self  # type: ignore

    def get(
        self,
        mark: str = "",
        lock_resources: resources_management.ResourcesType = (),
        use_resources: resources_management.ResourcesType = (),
        prio: bool = False,
        cleanup: bool = False,
        start_cmd: str = "",
    ) -> clusterlib.ClusterLib:
        """Get `cardano_clusterlib.ClusterLib` object on an initialized cluster instance.

        Convenience method that calls `init`.
        """
        self.init(
            mark=mark,
            lock_resources=lock_resources,
            use_resources=use_resources,
            prio=prio,
            cleanup=cleanup,
            start_cmd=start_cmd,
        )

        cluster_obj = self.cache.cluster_obj
        assert cluster_obj
        return cluster_obj
