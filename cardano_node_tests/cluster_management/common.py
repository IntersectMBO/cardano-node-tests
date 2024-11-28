import pathlib as pl
import re
import typing as tp

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

ADDRS_DATA_DIRNAME = "addrs_data"

RE_RESNAME = re.compile("_@@(.+)@@_")


def _get_res(path: pl.Path) -> str:
    out = RE_RESNAME.search(str(path))
    if out is None:
        err = f"Resource name not found in path: {path}"
        raise ValueError(err)
    return out.group(1)


def get_resources_from_path(paths: tp.Iterator[pl.Path]) -> tp.List[str]:
    """Get resources names from status files path."""
    resources = [_get_res(p) for p in paths]
    return resources
