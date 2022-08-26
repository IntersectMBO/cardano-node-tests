import tempfile
from pathlib import Path

from _pytest.tmpdir import TempPathFactory

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers


@helpers.callonce
def get_pytest_worker_tmp(tmp_path_factory: TempPathFactory) -> Path:
    """Return pytest temporary directory for the current worker.

    When running pytest with multiple workers, each worker has it's own base temporary
    directory inside the "root" temporary directory.
    """
    worker_tmp = Path(tmp_path_factory.getbasetemp())
    return worker_tmp


@helpers.callonce
def get_pytest_root_tmp(tmp_path_factory: TempPathFactory) -> Path:
    """Return root of the pytest temporary directory for a single pytest run."""
    worker_tmp = get_pytest_worker_tmp(tmp_path_factory)
    root_tmp = worker_tmp.parent if configuration.IS_XDIST else worker_tmp
    return root_tmp


@helpers.callonce
def get_pytest_shared_tmp(tmp_path_factory: TempPathFactory) -> Path:
    """Return shared temporary directory for a single pytest run.

    Temporary directory that can be shared by multiple pytest workers, e.g. for creating lock files.
    """
    root_tmp = get_pytest_root_tmp(tmp_path_factory)
    shared_tmp = root_tmp / "tmp"
    shared_tmp.mkdir(parents=True, exist_ok=True)
    return shared_tmp


@helpers.callonce
def get_basetemp() -> Path:
    """Return base temporary directory for tests artifacts."""
    basetemp = Path(tempfile.gettempdir()) / "cardano-node-tests"
    basetemp.mkdir(mode=0o700, parents=True, exist_ok=True)
    return basetemp
