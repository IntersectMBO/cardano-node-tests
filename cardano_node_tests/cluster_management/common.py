import re
from pathlib import Path
from typing import Iterator
from typing import List

CLUSTER_LOCK = ".cluster.lock"
LOG_LOCK = ".manager_log.lock"

RESOURCE_LOCKED_GLOB = ".resource_locked"
RESOURCE_IN_USE_GLOB = ".resource_in_use"
TEST_CURR_MARK_GLOB = ".curr_test_mark"
RESPIN_NEEDED_GLOB = ".needs_respin"
RESPIN_IN_PROGRESS_GLOB = ".respin_in_progress"
RESPIN_AFTER_MARK_GLOB = ".respin_after_mark"
PRIO_IN_PROGRESS_GLOB = ".prio_in_progress"
TEST_RUNNING_GLOB = ".test_running"

CLUSTER_DIR_TEMPLATE = "cluster"
CLUSTER_RUNNING_FILE = ".cluster_running"
CLUSTER_STOPPED_FILE = ".cluster_stopped"
CLUSTER_DEAD_FILE = ".cluster_dead"
CLUSTER_STARTED_BY_FRAMEWORK = ".cluster_started_by_cnt"

CLUSTER_START_CMDS_LOG = "start_cluster_cmds.log"


def _get_resources_from_paths(paths: Iterator[Path]) -> List[str]:
    """Get resources names from status files path."""
    resources = [re.search("_@@(.+)@@_", str(r)).group(1) for r in paths]  # type: ignore
    return resources
