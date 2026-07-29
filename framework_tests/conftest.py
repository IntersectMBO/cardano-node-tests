import os
import pathlib as pl
import typing as tp

# The framework imports must come after this env setup - importing the framework modules
# resolves `CARDANO_NODE_SOCKET_PATH` and looks up binaries on `PATH`
if not os.environ.get("CARDANO_NODE_SOCKET_PATH"):
    os.environ["CARDANO_NODE_SOCKET_PATH"] = "/nonexistent/state-cluster/relay1.socket"
    mockdir = pl.Path(__file__).parent / "mocks"
    os.environ["PATH"] = f"{mockdir}:{os.environ['PATH']}"

import pytest

from cardano_node_tests.cluster_management import status_db
from cardano_node_tests.utils import temptools


@pytest.fixture
def db_dir(tmp_path: pl.Path, monkeypatch: pytest.MonkeyPatch) -> tp.Generator[pl.Path]:
    """Point the status database to a fresh temp dir and reset the cached connection."""
    monkeypatch.setattr(temptools.PytestTempDirs, "pytest_root_tmp", tmp_path)
    status_db._conn = None
    status_db._conn_pid = -1
    yield tmp_path
    # Close the connection created during the test and clear the cached connection object,
    # so a later `_get_conn()` call cannot reuse a closed connection.
    if status_db._conn is not None:
        status_db._conn.close()
    status_db._conn = None
    status_db._conn_pid = -1
