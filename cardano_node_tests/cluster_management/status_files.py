"""Cluster instance status files.

Most of the status records used for communication and synchronization between pytest workers
are kept in a SQLite database (see the `status_db` module). This module handles the few
remaining file-based pieces:

* Cluster instance directories inside the single temp directory shared by all workers
  (the directory returned by `temptools.get_pytest_root_tmp()`).
* The "started by framework" status file that is created in the cluster instance state
  directory. Unlike the per-run status database, the state directory persists across pytest
  runs, so a later run can tell whether an existing cluster instance was started by the
  test framework.
"""

import pathlib as pl

from cardano_node_tests.utils import temptools

CLUSTER_DIR_TEMPLATE = "cluster"
CLUSTER_STARTED_BY_FRAMEWORK = ".cluster_started_by_cnt"


def get_instance_dir(instance_num: int) -> pl.Path:
    """Return cluster instance directory for the given instance number."""
    pytest_tmp_dir = temptools.get_pytest_root_tmp()
    instance_dir = pytest_tmp_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
    return instance_dir


def get_started_by_framework_file(state_dir: pl.Path) -> pl.Path:
    """Return the status file that indicates the cluster instance was started by test framework."""
    return state_dir / CLUSTER_STARTED_BY_FRAMEWORK


def create_started_by_framework_file(state_dir: pl.Path) -> pl.Path:
    """Create the status file that indicates the cluster instance was started by test framework."""
    file = get_started_by_framework_file(state_dir=state_dir)
    file.touch()
    return file
