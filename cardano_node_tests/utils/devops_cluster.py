import logging
import os
import pickle
import re
import shutil
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import pytest
from _pytest.fixtures import FixtureRequest

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType

LOGGER = logging.getLogger(__name__)

ADDR_DATA = "addr_data.pickle"

ERRORS_RE = re.compile(":error:|failed|failure", re.IGNORECASE)
ERRORS_IGNORED_RE = re.compile("EKGServerStartupError|WithIPList SubscriptionTrace")


def get_cluster_env() -> dict:
    """Get cardano cluster environment."""
    socket_path = Path(os.environ["CARDANO_NODE_SOCKET_PATH"]).expanduser().resolve()
    state_dir = socket_path.parent
    work_dir = state_dir.parent
    repo_dir = Path(os.environ.get("CARDANO_NODE_REPO_PATH") or work_dir)

    cluster_env = {
        "socket_path": socket_path,
        "state_dir": state_dir,
        "repo_dir": repo_dir,
        "work_dir": work_dir,
    }
    return cluster_env


def start_cluster() -> clusterlib.ClusterLib:
    """Start cluster."""
    LOGGER.info("Starting cluster.")
    cluster_env = get_cluster_env()
    helpers.run_shell_command("start-cluster", workdir=cluster_env["work_dir"])
    LOGGER.info("Cluster started.")

    return clusterlib.ClusterLib(cluster_env["state_dir"])


def stop_cluster() -> None:
    """Stop cluster."""
    LOGGER.info("Stopping cluster.")
    cluster_env = get_cluster_env()
    try:
        helpers.run_shell_command("stop-cluster", workdir=cluster_env["work_dir"])
    except Exception as exc:
        LOGGER.debug(f"Failed to stop cluster: {exc}")


def load_devops_pools_data(cluster_obj: clusterlib.ClusterLib) -> dict:
    """Load data for pools existing in the devops environment."""
    data_dir = get_cluster_env()["state_dir"] / "nodes"
    pools = ("node-pool1", "node-pool2")

    addrs_data = {}
    for addr_name in pools:
        addr_data_dir = data_dir / addr_name
        addrs_data[addr_name] = {
            "payment": clusterlib.AddressRecord(
                address=helpers.read_address_from_file(addr_data_dir / "owner.addr"),
                vkey_file=addr_data_dir / "owner-utxo.vkey",
                skey_file=addr_data_dir / "owner-utxo.skey",
            ),
            "stake": clusterlib.AddressRecord(
                address=helpers.read_address_from_file(addr_data_dir / "owner-stake.addr"),
                vkey_file=addr_data_dir / "owner-stake.vkey",
                skey_file=addr_data_dir / "owner-stake.skey",
            ),
            "reward": clusterlib.AddressRecord(
                address=cluster_obj.gen_stake_addr(stake_vkey_file=addr_data_dir / "reward.vkey"),
                vkey_file=addr_data_dir / "reward.vkey",
                skey_file=addr_data_dir / "reward.skey",
            ),
            "stake_addr_registration_cert": addr_data_dir / "stake.reg.cert",
            "stake_addr_delegation_cert": addr_data_dir / "owner-stake.deleg.cert",
            "reward_addr_registration_cert": addr_data_dir / "stake-reward.reg.cert",
            "cold_key_pair": clusterlib.ColdKeyPair(
                vkey_file=addr_data_dir / "cold.vkey",
                skey_file=addr_data_dir / "cold.skey",
                counter_file=addr_data_dir / "cold.counter",
            ),
            "vrf_key_pair": clusterlib.KeyPair(
                vkey_file=addr_data_dir / "vrf.vkey",
                skey_file=addr_data_dir / "vrf.skey",
            ),
        }

    return addrs_data


def setup_test_addrs(cluster_obj: clusterlib.ClusterLib, destination_dir: FileType = ".") -> Path:
    """Create addresses and their keys for usage in tests."""
    destination_dir = Path(destination_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    addrs = ("user1",)

    LOGGER.debug("Creating addresses and keys for tests.")
    addrs_data: Dict[str, Dict[str, Any]] = {}
    for addr_name in addrs:
        stake = cluster_obj.gen_stake_addr_and_keys(name=addr_name, destination_dir=destination_dir)
        payment = cluster_obj.gen_payment_addr_and_keys(
            name=addr_name, stake_vkey_file=stake.vkey_file, destination_dir=destination_dir
        )
        stake_addr_registration_cert = cluster_obj.gen_stake_addr_registration_cert(
            addr_name=addr_name,
            stake_vkey_file=stake.vkey_file,
            destination_dir=destination_dir,
        )

        addrs_data[addr_name] = {
            "payment": payment,
            "stake": stake,
            "stake_addr_registration_cert": stake_addr_registration_cert,
        }

    LOGGER.debug("Funding created addresses.")
    clusterlib_utils.fund_from_genesis(
        *[d["payment"].address for d in addrs_data.values()],
        cluster_obj=cluster_obj,
        amount=6_000_000_000_000,
        destination_dir=destination_dir,
    )

    pools_data = load_devops_pools_data(cluster_obj)

    cluster_env = get_cluster_env()
    data_file = Path(cluster_env["state_dir"]) / ADDR_DATA
    with open(data_file, "wb") as out_data:
        pickle.dump({**addrs_data, **pools_data}, out_data)
    return data_file


def load_addrs_data() -> dict:
    """Load data about addresses and their keys for usage in tests."""
    cluster_env = get_cluster_env()
    data_file = Path(cluster_env["state_dir"]) / ADDR_DATA
    with open(data_file, "rb") as in_data:
        return pickle.load(in_data)  # type: ignore


def save_tests_artifacts(pytest_tmp_dir: Path, artifacts_dir: Path) -> Optional[Path]:
    """Save tests artifacts."""
    pytest_tmp_dir = pytest_tmp_dir.resolve()
    if not pytest_tmp_dir.is_dir():
        return None

    dest_dir = artifacts_dir / f"{pytest_tmp_dir.stem}-{clusterlib.get_rand_str(8)}"
    if dest_dir.resolve().is_dir():
        shutil.rmtree(dest_dir)
    shutil.copytree(pytest_tmp_dir, dest_dir, symlinks=True, ignore_dangling_symlinks=True)

    LOGGER.info(f"Tests artifacts saved to '{artifacts_dir}'.")
    return dest_dir


def save_cluster_artifacts(artifacts_dir: Path) -> Optional[Path]:
    """Save cluster artifacts."""
    cluster_env = get_cluster_env()
    if not cluster_env.get("state_dir"):
        return None

    dest_dir = artifacts_dir / f"cluster_artifacts_{clusterlib.get_rand_str(8)}"
    os.mkdir(dest_dir)

    state_dir = Path(cluster_env["state_dir"])
    files_list = list(state_dir.glob("*.std*"))
    files_list.extend(list(state_dir.glob("*.json")))
    dirs_to_copy = ("nodes", "shelley")

    for fpath in files_list:
        shutil.copy(fpath, dest_dir)
    for dname in dirs_to_copy:
        shutil.copytree(
            state_dir / dname, dest_dir / dname, symlinks=True, ignore_dangling_symlinks=True
        )

    LOGGER.info(f"Cluster artifacts saved to '{dest_dir}'.")
    return dest_dir


def search_cluster_artifacts(artifacts_dir: Path) -> List[Tuple[Path, str]]:
    """Search cluster artifacts for errors."""
    errors = []
    for fpath in artifacts_dir.glob("**/cluster_artifacts_*/*.std*"):
        with open(fpath) as infile:
            content = infile.readlines()
            for line in content:
                if ERRORS_RE.search(line) and not ERRORS_IGNORED_RE.search(line):
                    errors.append((fpath, line))
    return errors


def report_artifacts_errors(errors: List[Tuple[Path, str]]) -> None:
    """Report errors found in artifacts."""
    err = [f"{e[0]}: {e[1]}" for e in errors]
    err_joined = "\n".join(err)
    pytest.fail(f"Errors found in cluster log files:\n{err_joined}")


def process_artifacts(pytest_tmp_dir: Path, request: FixtureRequest) -> None:
    """Process tests and cluster artifacts."""
    artifacts_dir = request.config.getoption("--artifacts-base-dir")
    if artifacts_dir:
        artifacts_dir = Path(artifacts_dir)
        # copy tests artifacts only when base directory was passed
        save_tests_artifacts(pytest_tmp_dir, artifacts_dir)
    else:
        artifacts_dir = pytest_tmp_dir

    errors = search_cluster_artifacts(artifacts_dir)
    if errors:
        report_artifacts_errors(errors)
