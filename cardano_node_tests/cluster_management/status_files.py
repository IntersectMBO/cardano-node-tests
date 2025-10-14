"""Cluster instance status files.

Status files are used for communication and synchronization between pytest workers.

All status files are created in the single temp directory shared by all workers
(the directory returned by `temptools.get_pytest_root_tmp()`). This allows all
workers to see status files created by other workers.

Common components of status file names:
* `_@@<resource_name>@@_`: resource name
* `_%%<mark>%%_`: test mark
* `_<worker_id>`: pytest worker ID
"""

import pathlib as pl
import re
import typing as tp

from cardano_node_tests.utils import temptools

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

RE_RESNAME = re.compile("_@@(.+)@@_")


def get_instance_dir(instance_num: int) -> pl.Path:
    """Return cluster instance directory for the given instance number."""
    pytest_tmp_dir = temptools.get_pytest_root_tmp()
    instance_dir = pytest_tmp_dir / f"{CLUSTER_DIR_TEMPLATE}{instance_num}"
    return instance_dir


def get_cluster_running_file(instance_num: int) -> pl.Path:
    """Return the status file that indicates that the cluster instance is running."""
    instance_dir = get_instance_dir(instance_num=instance_num)
    return instance_dir / CLUSTER_RUNNING_FILE


def get_cluster_stopped_file(instance_num: int) -> pl.Path:
    """Return the status file that indicates that the cluster instance is stopped."""
    instance_dir = get_instance_dir(instance_num=instance_num)
    return instance_dir / CLUSTER_STOPPED_FILE


def get_started_by_framework_file(state_dir: pl.Path) -> pl.Path:
    """Return the status file that indicates the cluster instance was started by test framework."""
    return state_dir / CLUSTER_STARTED_BY_FRAMEWORK


def get_cluster_dead_file(instance_num: int) -> pl.Path:
    """Return the status file that indicates that the cluster instance is in broken state."""
    instance_dir = get_instance_dir(instance_num=instance_num)
    return instance_dir / CLUSTER_DEAD_FILE


def get_respin_needed_file(instance_num: int, worker_id: str) -> pl.Path:
    """Return the status file that indicates that the cluster instance needs respin."""
    instance_dir = get_instance_dir(instance_num=instance_num)
    return instance_dir / f"{RESPIN_NEEDED_GLOB}_{worker_id}"


def get_respin_progress_file(instance_num: int, worker_id: str) -> pl.Path:
    """Return the status file that indicates that respin is in progress."""
    instance_dir = get_instance_dir(instance_num=instance_num)
    return instance_dir / f"{RESPIN_IN_PROGRESS_GLOB}_{worker_id}"


def get_curr_mark_file(instance_num: int, worker_id: str, mark: str) -> pl.Path:
    """Return the status file that indicates presence of marked test on a pytest worker."""
    instance_dir = get_instance_dir(instance_num=instance_num)
    return instance_dir / f"{TEST_CURR_MARK_GLOB}_@@{mark}@@_{worker_id}"


def get_respin_after_mark_file(instance_num: int, worker_id: str, mark: str) -> pl.Path:
    """Return the status file that indicates that the cluster instance needs respin.

    The respin will happen after marked tests are finished on the dedicated cluster instance.
    """
    instance_dir = get_instance_dir(instance_num=instance_num)
    return instance_dir / f"{RESPIN_AFTER_MARK_GLOB}_@@{mark}@@_{worker_id}"


def get_prio_in_progress_file(worker_id: str) -> pl.Path:
    """Return the status file that indicates that priority test is in progress."""
    pytest_tmp_dir = temptools.get_pytest_root_tmp()
    return pytest_tmp_dir / f"{PRIO_IN_PROGRESS_GLOB}_{worker_id}"


def get_test_running_file(instance_num: int, worker_id: str, mark: str = "") -> pl.Path:
    """Return the status file that indicates that a test is running on a pytest worker."""
    mark_str = f"_@@{mark}@@" if mark else ""
    instance_dir = get_instance_dir(instance_num=instance_num)
    return instance_dir / f"{TEST_RUNNING_GLOB}{mark_str}_{worker_id}"


def get_marks_in_progress(instance_num: int | None = None, worker_id: str = "*") -> list[str]:
    """Return list of marks that are in progress."""
    instance_num_str = str(instance_num) if instance_num is not None else "*"
    worker_id_str = "" if worker_id == "*" else f"_{worker_id}"
    pytest_tmp_dir = temptools.get_pytest_root_tmp()
    files = pytest_tmp_dir.glob(
        f"{CLUSTER_DIR_TEMPLATE}{instance_num_str}/{TEST_RUNNING_GLOB}_@@*{worker_id_str}"
    )
    marks_in_progress = [f.name.split("@@")[1] for f in files]
    return marks_in_progress


def list_test_running_files(
    instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[pl.Path]:
    """List all "test running" status files.

    If `mark` is `None`, list all status files regarless whether any mark is present or not.
    If `mark` is `*`, list all status files that have any mark.
    If `mark` is an empty string, list all status files that don't have mark.
    """
    if mark == "":
        mark_str = ""
    elif mark is None:
        mark_str = "*"
    else:
        mark_str = f"_@@{mark}@@"

    instance_num_str = str(instance_num) if instance_num is not None else "*"
    pytest_tmp_dir = temptools.get_pytest_root_tmp()

    files = list(
        pytest_tmp_dir.glob(
            f"{CLUSTER_DIR_TEMPLATE}{instance_num_str}/{TEST_RUNNING_GLOB}{mark_str}_{worker_id}"
        )
    )

    if mark == "" and worker_id == "*":
        # Filter out all paths that contain marks
        files = [f for f in files if "_@@" not in f.name]

    return files


def get_test_names(
    instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[str]:
    """Return list of test names that are currently running.

    If `mark` is `None`, list all status files regarless whether any mark is present or not.
    If `mark` is `*`, list all status files that have any mark.
    If `mark` is an empty string, list all status files that don't have mark.
    """
    tnames = [
        tf.read_text().strip()
        for tf in list_test_running_files(instance_num=instance_num, worker_id=worker_id, mark=mark)
    ]
    return tnames


def list_prio_in_progress_files(worker_id: str = "*") -> list[pl.Path]:
    """List all "priority test in progress" status files."""
    pytest_tmp_dir = temptools.get_pytest_root_tmp()
    files = list(pytest_tmp_dir.glob(f"{PRIO_IN_PROGRESS_GLOB}_{worker_id}"))
    return files


def list_cluster_dead_files(instance_num: int | None = None) -> list[pl.Path]:
    """List all "cluster dead" status files."""
    instance_num_str = str(instance_num) if instance_num is not None else "*"
    pytest_tmp_dir = temptools.get_pytest_root_tmp()
    files = list(
        pytest_tmp_dir.glob(f"{CLUSTER_DIR_TEMPLATE}{instance_num_str}/{CLUSTER_DEAD_FILE}")
    )
    return files


def list_respin_needed_files(
    instance_num: int | None = None, worker_id: str = "*"
) -> list[pl.Path]:
    """List all "needs respin" status files."""
    instance_num_str = str(instance_num) if instance_num is not None else "*"
    pytest_tmp_dir = temptools.get_pytest_root_tmp()
    files = list(
        pytest_tmp_dir.glob(
            f"{CLUSTER_DIR_TEMPLATE}{instance_num_str}/{RESPIN_NEEDED_GLOB}_{worker_id}"
        )
    )
    return files


def list_respin_progress_files(
    instance_num: int | None = None, worker_id: str = "*"
) -> list[pl.Path]:
    """List all "respin in progress" status files."""
    instance_num_str = str(instance_num) if instance_num is not None else "*"
    pytest_tmp_dir = temptools.get_pytest_root_tmp()
    files = list(
        pytest_tmp_dir.glob(
            f"{CLUSTER_DIR_TEMPLATE}{instance_num_str}/{RESPIN_IN_PROGRESS_GLOB}_{worker_id}"
        )
    )
    return files


def list_respin_after_mark_files(
    instance_num: int | None = None, worker_id: str = "*", mark: str = "*"
) -> list[pl.Path]:
    """List all "respin after mark" status files."""
    instance_num_str = str(instance_num) if instance_num is not None else "*"
    pytest_tmp_dir = temptools.get_pytest_root_tmp()
    files = list(
        pytest_tmp_dir.glob(
            f"{CLUSTER_DIR_TEMPLATE}{instance_num_str}/"
            f"{RESPIN_AFTER_MARK_GLOB}_@@{mark}@@_{worker_id}"
        )
    )
    return files


def list_resource_locked_files(
    instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[pl.Path]:
    """List all "resource locked" status files.

    If `mark` is `None`, list all status files regarless whether any mark is present or not.
    If `mark` is `*`, list all status files that have any mark.
    If `mark` is an empty string, list all status files that don't have mark.
    """
    if mark == "":
        mark_str = ""
    elif mark is None:
        mark_str = "*"
    else:
        mark_str = f"_%%{mark}%%"

    instance_num_str = str(instance_num) if instance_num is not None else "*"
    pytest_tmp_dir = temptools.get_pytest_root_tmp()

    files = list(
        pytest_tmp_dir.glob(
            f"{CLUSTER_DIR_TEMPLATE}{instance_num_str}/"
            f"{RESOURCE_LOCKED_GLOB}_@@*@@{mark_str}_{worker_id}"
        )
    )

    if mark == "" and worker_id == "*":
        # Filter out all paths that contain marks
        files = [f for f in files if "_%%" not in f.name]

    return files


def list_resource_used_files(
    instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[pl.Path]:
    """List all "resource used" status files.

    If `mark` is `None`, list all status files regarless whether any mark is present or not.
    If `mark` is `*`, list all status files that have any mark.
    If `mark` is an empty string, list all status files that don't have mark.
    """
    if mark == "":
        mark_str = ""
    elif mark is None:
        mark_str = "*"
    else:
        mark_str = f"_%%{mark}%%"

    instance_num_str = str(instance_num) if instance_num is not None else "*"
    pytest_tmp_dir = temptools.get_pytest_root_tmp()

    files = list(
        pytest_tmp_dir.glob(
            f"{CLUSTER_DIR_TEMPLATE}{instance_num_str}/"
            f"{RESOURCE_IN_USE_GLOB}_@@*@@{mark_str}_{worker_id}"
        )
    )

    if mark == "" and worker_id == "*":
        # Filter out all paths that contain marks
        files = [f for f in files if "_%%" not in f.name]

    return files


def list_curr_mark_files(
    instance_num: int | None = None, worker_id: str = "*", mark: str = "*"
) -> list[pl.Path]:
    """List all "current mark" status files."""
    instance_num_str = str(instance_num) if instance_num is not None else "*"
    pytest_tmp_dir = temptools.get_pytest_root_tmp()
    files = list(
        pytest_tmp_dir.glob(
            f"{CLUSTER_DIR_TEMPLATE}{instance_num_str}/{TEST_CURR_MARK_GLOB}_@@{mark}@@_{worker_id}"
        )
    )
    return files


def create_respin_needed_file(instance_num: int, worker_id: str) -> pl.Path:
    """Create the status file that indicates that the cluster instance needs respin."""
    file = get_respin_needed_file(instance_num=instance_num, worker_id=worker_id)
    file.touch()
    return file


def create_respin_progress_file(instance_num: int, worker_id: str) -> pl.Path:
    """Create the status file that indicates that respin is in progress."""
    file = get_respin_progress_file(instance_num=instance_num, worker_id=worker_id)
    file.touch()
    return file


def create_curr_mark_file(instance_num: int, worker_id: str, mark: str) -> pl.Path:
    """Create the status file that indicates presence of marked test on a pytest worker."""
    file = get_curr_mark_file(instance_num=instance_num, worker_id=worker_id, mark=mark)
    file.touch()
    return file


def create_respin_after_mark_file(instance_num: int, worker_id: str, mark: str) -> pl.Path:
    """Create the status file that indicates that the cluster instance needs respin.

    The respin will happen after marked tests are finished on the dedicated cluster instance.
    """
    file = get_respin_after_mark_file(instance_num=instance_num, worker_id=worker_id, mark=mark)
    file.touch()
    return file


def create_prio_in_progress_file(worker_id: str) -> pl.Path:
    """Create the status file that indicates that priority test is in progress."""
    file = get_prio_in_progress_file(worker_id=worker_id)
    file.touch()
    return file


def create_cluster_dead_file(instance_num: int) -> pl.Path:
    """Create the status file that indicates that the cluster instance is in broken state."""
    file = get_cluster_dead_file(instance_num=instance_num)
    file.touch()
    return file


def create_cluster_stopped_file(instance_num: int) -> pl.Path:
    """Create the status file that indicates that the cluster instance is stopped."""
    file = get_cluster_stopped_file(instance_num=instance_num)
    file.touch()
    return file


def create_started_by_framework_file(state_dir: pl.Path) -> pl.Path:
    """Create the status file that indicates the cluster instance was started by test framework."""
    file = get_started_by_framework_file(state_dir=state_dir)
    file.touch()
    return file


def create_test_running_file(
    instance_num: int, worker_id: str, test_id: str, mark: str = ""
) -> pl.Path:
    """Create the status file that indicates that a test is running on a pytest worker.

    Save the test name in the status file.
    """
    file = get_test_running_file(instance_num=instance_num, worker_id=worker_id, mark=mark)
    file.write_text(test_id)
    return file


def create_resource_locked_files(
    instance_num: int, worker_id: str, lock_names: tp.Iterable[str], mark: str = ""
) -> list[pl.Path]:
    """Create status files that indicate that the given resources are locked."""
    instance_dir = get_instance_dir(instance_num=instance_num)
    mark_str = f"_%%{mark}%%" if mark else ""
    files = [
        (instance_dir / f"{RESOURCE_LOCKED_GLOB}_@@{r}@@{mark_str}_{worker_id}") for r in lock_names
    ]
    for f in files:
        f.touch()
    return files


def create_resource_used_files(
    instance_num: int, worker_id: str, use_names: tp.Iterable[str], mark: str = ""
) -> list[pl.Path]:
    """Create status files that indicate that the given resources are in use."""
    instance_dir = get_instance_dir(instance_num=instance_num)
    mark_str = f"_%%{mark}%%" if mark else ""
    files = [
        (instance_dir / f"{RESOURCE_IN_USE_GLOB}_@@{r}@@{mark_str}_{worker_id}") for r in use_names
    ]
    for f in files:
        f.touch()
    return files


def _get_res(path: pl.Path) -> str:
    out = RE_RESNAME.search(str(path))
    if out is None:
        err = f"Resource name not found in path: {path}"
        raise ValueError(err)
    return out.group(1)


def get_resources_from_path(paths: tp.Iterable[pl.Path]) -> list[str]:
    """Get resources names from status files path."""
    resources = [_get_res(p) for p in paths]
    return resources


def rm_resource_locked_files(
    instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[pl.Path]:
    """Delete all "resource locked" status files.

    If `mark` is `None`, list all status files regarless whether any mark is present or not.
    If `mark` is `*`, list all status files that have any mark.
    If `mark` is an empty string, list all status files that don't have mark.
    """
    files = list_resource_locked_files(instance_num=instance_num, worker_id=worker_id, mark=mark)
    for f in files:
        f.unlink()
    return files


def rm_resource_used_files(
    instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[pl.Path]:
    """Delete all "resource used" status files.

    If `mark` is `None`, list all status files regarless whether any mark is present or not.
    If `mark` is `*`, list all status files that have any mark.
    If `mark` is an empty string, list all status files that don't have mark.
    """
    files = list_resource_used_files(instance_num=instance_num, worker_id=worker_id, mark=mark)
    for f in files:
        f.unlink()
    return files


def rm_test_running_files(
    instance_num: int | None = None, worker_id: str = "*", mark: str | None = None
) -> list[pl.Path]:
    """Delete all "test running" status files.

    If `mark` is `None`, list all status files regarless whether any mark is present or not.
    If `mark` is `*`, list all status files that have any mark.
    If `mark` is an empty string, list all status files that don't have mark.
    """
    files = list_test_running_files(instance_num=instance_num, worker_id=worker_id, mark=mark)
    for f in files:
        f.unlink()
    return files


def rm_respin_after_mark_files(
    instance_num: int | None = None, worker_id: str = "*", mark: str = "*"
) -> list[pl.Path]:
    """Delete all "respin after mark" status files."""
    files = list_respin_after_mark_files(instance_num=instance_num, worker_id=worker_id, mark=mark)
    for f in files:
        f.unlink()
    return files


def rm_curr_mark_files(
    instance_num: int | None = None, worker_id: str = "*", mark: str = "*"
) -> list[pl.Path]:
    """Delete all "current mark" status files."""
    files = list_curr_mark_files(instance_num=instance_num, worker_id=worker_id, mark=mark)
    for f in files:
        f.unlink()
    return files


def rm_prio_in_progress_files(worker_id: str = "*") -> list[pl.Path]:
    """Delete all "priority test in progress" status files."""
    files = list_prio_in_progress_files(worker_id=worker_id)
    for f in files:
        f.unlink()
    return files


def rm_respin_progress_files(
    instance_num: int | None = None, worker_id: str = "*"
) -> list[pl.Path]:
    """Delete all "respin in progress" status files."""
    files = list_respin_progress_files(instance_num=instance_num, worker_id=worker_id)
    for f in files:
        f.unlink()
    return files


def rm_respin_needed_files(instance_num: int | None = None, worker_id: str = "*") -> list[pl.Path]:
    """Delete all "needs respin" status files."""
    files = list_respin_needed_files(instance_num=instance_num, worker_id=worker_id)
    for f in files:
        f.unlink()
    return files
