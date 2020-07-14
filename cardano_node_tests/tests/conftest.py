import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cardano_node_tests.utils.clusterlib import ClusterLib

LOGGER = logging.getLogger(__name__)


def pytest_configure(config):
    config.addinivalue_line("markers", "clean_cluster: mark that the test needs clean cluster.")


def run_command(command, workdir=None):
    cmd = f"bash -c '{command}'"
    cmd = cmd if not workdir else f"cd {workdir}; {cmd}"
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    __, stderr = p.communicate()
    if p.returncode != 0:
        raise AssertionError(f"An error occurred while running `{cmd}`: {stderr}")


def setup_cluster():
    socket_path = Path(os.environ["CARDANO_NODE_SOCKET_PATH"]).expanduser().resolve()
    os.environ["CARDANO_NODE_SOCKET_PATH"] = str(socket_path)
    state_dir = socket_path.parent
    work_dir = state_dir.parent
    repo_dir = Path(os.environ.get("CARDANO_NODE_REPO_PATH") or work_dir)

    LOGGER.info("Starting cluster.")
    run_command("start-cluster", workdir=work_dir)

    shutil.copy(
        repo_dir / "nix" / "supervisord-cluster" / "genesis-utxo.vkey",
        state_dir / "keys" / "genesis-utxo.vkey",
    )
    shutil.copy(
        repo_dir / "nix" / "supervisord-cluster" / "genesis-utxo.skey",
        state_dir / "keys" / "genesis-utxo.skey",
    )

    with open(state_dir / "keys" / "genesis.json") as in_json:
        genesis_json = json.load(in_json)

    cluster_data = {
        "socket_path": socket_path,
        "state_dir": state_dir,
        "repo_dir": repo_dir,
        "work_dir": work_dir,
        "genesis": genesis_json,
    }
    cluster_obj = ClusterLib(cluster_data["genesis"]["networkMagic"], state_dir)
    cluster_obj._data = cluster_data
    cluster_obj.refresh_pparams()

    return cluster_obj


@pytest.fixture(scope="session")
def cluster_session():
    cluster_obj = setup_cluster()
    yield cluster_obj
    LOGGER.info("Stopping cluster.")
    run_command("stop-cluster", workdir=cluster_obj._data["work_dir"])


@pytest.fixture
def cluster():
    cluster_obj = setup_cluster()
    yield cluster_obj
    LOGGER.info("Stopping cluster.")
    run_command("stop-cluster", workdir=cluster_obj._data["work_dir"])
