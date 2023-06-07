import tempfile
from pathlib import Path
from typing import Optional

from _pytest.tmpdir import TempPathFactory

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers


class PytestTempDirs:
    """Pytest temporary directories that are used accross the framework.

    The class is initialized in `conftest.py` where we have access to the `tmp_path_factory`
    fixture.
    """

    pytest_worker_tmp: Optional[Path] = None
    pytest_root_tmp: Optional[Path] = None
    pytest_shared_tmp: Optional[Path] = None

    _err_init_str = "PytestTempDirs are not initialized"

    @classmethod
    def init(cls, tmp_path_factory: TempPathFactory) -> None:
        worker_tmp = Path(tmp_path_factory.getbasetemp())
        cls.pytest_worker_tmp = worker_tmp

        root_tmp = worker_tmp.parent if configuration.IS_XDIST else worker_tmp
        cls.pytest_root_tmp = root_tmp

        shared_tmp = root_tmp / "tmp"
        shared_tmp.mkdir(parents=True, exist_ok=True)
        cls.pytest_shared_tmp = shared_tmp


def get_pytest_worker_tmp() -> Path:
    """Return Pytest temporary directory for the current worker.

    When running pytest with multiple workers, each worker has it's own base temporary
    directory inside the "root" temporary directory.
    """
    if PytestTempDirs.pytest_worker_tmp is None:
        raise RuntimeError(PytestTempDirs._err_init_str)
    return PytestTempDirs.pytest_worker_tmp


def get_pytest_root_tmp() -> Path:
    """Return root of the Pytest temporary directory for a single Pytest run."""
    if PytestTempDirs.pytest_root_tmp is None:
        raise RuntimeError(PytestTempDirs._err_init_str)
    return PytestTempDirs.pytest_root_tmp


def get_pytest_shared_tmp() -> Path:
    """Return shared temporary directory for a single Pytest run.

    Temporary directory that can be shared by multiple Pytest workers, e.g. for creating
    lock files.
    """
    if PytestTempDirs.pytest_shared_tmp is None:
        raise RuntimeError(PytestTempDirs._err_init_str)
    return PytestTempDirs.pytest_shared_tmp


@helpers.callonce
def get_basetemp() -> Path:
    """Return base temporary directory for tests artifacts."""
    basetemp = Path(tempfile.gettempdir()) / "cardano-node-tests"
    basetemp.mkdir(mode=0o700, parents=True, exist_ok=True)
    return basetemp
