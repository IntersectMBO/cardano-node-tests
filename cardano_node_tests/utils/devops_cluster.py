"""Functionality for interacting with the DevOps cluster."""
import logging
import os
import pickle
import re
import shutil
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional

from _pytest.config import Config

from cardano_node_tests.utils import cluster_instances
from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType

LOGGER = logging.getLogger(__name__)

ADDR_DATA = "addr_data.pickle"


class StartupFiles(NamedTuple):
    start_script: Path
    config: Path
    genesis_spec: Path


def get_cluster_env() -> dict:
    """Get cardano cluster environment."""
    socket_path = Path(os.environ["CARDANO_NODE_SOCKET_PATH"]).expanduser().resolve()
    state_dir = socket_path.parent
    work_dir = state_dir.parent
    repo_dir = Path(os.environ.get("CARDANO_NODE_REPO_PATH") or work_dir)
    instance_num = int(state_dir.name.replace("state-cluster", "") or 0)

    cluster_env = {
        "socket_path": socket_path,
        "state_dir": state_dir,
        "repo_dir": repo_dir,
        "work_dir": work_dir,
        "instance_num": instance_num,
    }
    return cluster_env


def start_cluster(cmd: str) -> clusterlib.ClusterLib:
    """Start cluster."""
    LOGGER.info(f"Starting cluster with `{cmd}`.")
    cluster_env = get_cluster_env()
    helpers.run_shell_command(cmd, workdir=cluster_env["work_dir"])
    LOGGER.info("Cluster started.")

    return clusterlib.ClusterLib(cluster_env["state_dir"])


def stop_cluster(cmd: str) -> None:
    """Stop cluster."""
    LOGGER.info(f"Stopping cluster with `{cmd}`.")
    cluster_env = get_cluster_env()
    try:
        helpers.run_shell_command(cmd, workdir=cluster_env["work_dir"])
    except Exception as exc:
        LOGGER.debug(f"Failed to stop cluster: {exc}")


def restart_node(node_name: str) -> None:
    """Restart single node of the running cluster."""
    LOGGER.info(f"Restarting cluster node `{node_name}`.")
    cluster_env = get_cluster_env()
    supervisor_port = cluster_instances.get_instance_ports(cluster_env["instance_num"]).supervisor
    try:
        helpers.run_command(
            f"supervisorctl -s http://localhost:{supervisor_port} restart {node_name}",
            workdir=cluster_env["work_dir"],
        )
    except Exception as exc:
        LOGGER.debug(f"Failed to restart cluster node `{node_name}`: {exc}")


def load_devops_pools_data(cluster_obj: clusterlib.ClusterLib) -> dict:
    """Load data for pools existing in the devops environment."""
    data_dir = get_cluster_env()["state_dir"] / "nodes"
    pools = ("node-pool1", "node-pool2")

    pools_data = {}
    for pool_name in pools:
        pool_data_dir = data_dir / pool_name
        pools_data[pool_name] = {
            "payment": clusterlib.AddressRecord(
                address=cluster_obj.read_address_from_file(pool_data_dir / "owner.addr"),
                vkey_file=pool_data_dir / "owner-utxo.vkey",
                skey_file=pool_data_dir / "owner-utxo.skey",
            ),
            "stake": clusterlib.AddressRecord(
                address=cluster_obj.read_address_from_file(pool_data_dir / "owner-stake.addr"),
                vkey_file=pool_data_dir / "owner-stake.vkey",
                skey_file=pool_data_dir / "owner-stake.skey",
            ),
            "reward": clusterlib.AddressRecord(
                address=cluster_obj.gen_stake_addr(
                    addr_name="reward",
                    stake_vkey_file=pool_data_dir / "reward.vkey",
                    destination_dir=pool_data_dir,
                ),
                vkey_file=pool_data_dir / "reward.vkey",
                skey_file=pool_data_dir / "reward.skey",
            ),
            "stake_addr_registration_cert": pool_data_dir / "stake.reg.cert",
            "stake_addr_delegation_cert": pool_data_dir / "owner-stake.deleg.cert",
            "reward_addr_registration_cert": pool_data_dir / "stake-reward.reg.cert",
            "pool_registration_cert": pool_data_dir / "register.cert",
            "pool_operational_cert": pool_data_dir / "op.cert",
            "cold_key_pair": clusterlib.ColdKeyPair(
                vkey_file=pool_data_dir / "cold.vkey",
                skey_file=pool_data_dir / "cold.skey",
                counter_file=pool_data_dir / "cold.counter",
            ),
            "vrf_key_pair": clusterlib.KeyPair(
                vkey_file=pool_data_dir / "vrf.vkey",
                skey_file=pool_data_dir / "vrf.skey",
            ),
            "kes_key_pair": clusterlib.KeyPair(
                vkey_file=pool_data_dir / "kes.vkey",
                skey_file=pool_data_dir / "kes.skey",
            ),
        }

    return pools_data


def setup_test_addrs(cluster_obj: clusterlib.ClusterLib, destination_dir: FileType = ".") -> Path:
    """Create addresses and their keys for usage in tests."""
    destination_dir = Path(destination_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    cluster_env = get_cluster_env()
    instance_num = cluster_env["instance_num"]
    addrs = ("user1",)

    LOGGER.debug("Creating addresses and keys for tests.")
    addrs_data: Dict[str, Dict[str, Any]] = {}
    for addr_name in addrs:
        addr_name_instance = f"{addr_name}_ci{instance_num}"
        stake = cluster_obj.gen_stake_addr_and_keys(
            name=addr_name_instance, destination_dir=destination_dir
        )
        payment = cluster_obj.gen_payment_addr_and_keys(
            name=addr_name_instance,
            stake_vkey_file=stake.vkey_file,
            destination_dir=destination_dir,
        )
        stake_addr_registration_cert = cluster_obj.gen_stake_addr_registration_cert(
            addr_name=addr_name_instance,
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

    data_file = Path(cluster_env["state_dir"]) / ADDR_DATA
    with open(data_file, "wb") as out_data:
        pickle.dump({**addrs_data, **pools_data}, out_data)
    return data_file


def get_node_config_paths(start_script: Path) -> List[Path]:
    """Return path of node config files in nix."""
    with open(start_script) as infile:
        content = infile.read()

    node_config = re.findall(r"cp /nix/store/(.+\.json) ", content)
    nix_store = Path("/nix/store")
    node_config_paths = [nix_store / c for c in node_config]

    return node_config_paths


def copy_startup_files(destdir: Path) -> StartupFiles:
    """Make a copy of the "start-cluster" script and cluster config files."""
    start_script_orig = helpers.get_cmd_path("start-cluster")
    shutil.copy(start_script_orig, destdir)
    start_script = destdir / "start-cluster"
    start_script.chmod(0o755)

    node_config_paths = get_node_config_paths(start_script)
    for fpath in node_config_paths:
        conf_name_orig = str(fpath)
        if conf_name_orig.endswith("node.json"):
            conf_name = "node.json"
        elif conf_name_orig.endswith("genesis.spec.json"):
            conf_name = "genesis.spec.json"
        else:
            continue

        dest_file = destdir / conf_name
        shutil.copy(fpath, dest_file)
        dest_file.chmod(0o644)

        helpers.replace_str_in_file(
            infile=start_script,
            outfile=start_script,
            orig_str=str(fpath),
            new_str=str(dest_file),
        )

    config_json = destdir / "node.json"
    genesis_spec_json = destdir / "genesis.spec.json"
    assert config_json.exists() and genesis_spec_json.exists()

    return StartupFiles(
        start_script=start_script, config=config_json, genesis_spec=genesis_spec_json
    )


def load_addrs_data() -> dict:
    """Load data about addresses and their keys for usage in tests."""
    cluster_env = get_cluster_env()
    data_file = Path(cluster_env["state_dir"]) / ADDR_DATA
    with open(data_file, "rb") as in_data:
        return pickle.load(in_data)  # type: ignore


def save_cluster_artifacts(artifacts_dir: Path, clean: bool = False) -> Optional[Path]:
    """Save cluster artifacts."""
    cluster_env = get_cluster_env()
    if not cluster_env.get("state_dir"):
        return None

    dest_dir = artifacts_dir / f"cluster_artifacts_{clusterlib.get_rand_str(8)}"
    dest_dir.mkdir(parents=True)

    state_dir = Path(cluster_env["state_dir"])
    files_list = list(state_dir.glob("*.std*"))
    files_list.extend(list(state_dir.glob("*.json")))
    dirs_to_copy = ("nodes", "shelley")

    for fpath in files_list:
        shutil.copy(fpath, dest_dir)
    for dname in dirs_to_copy:
        src_dir = state_dir / dname
        if not src_dir.exists():
            continue
        shutil.copytree(src_dir, dest_dir / dname, symlinks=True, ignore_dangling_symlinks=True)

    if not os.listdir(dest_dir):
        dest_dir.rmdir()
        return None

    LOGGER.info(f"Cluster artifacts saved to '{dest_dir}'.")

    if clean:
        LOGGER.info(f"Cleaning cluster artifacts in '{state_dir}'.")
        shutil.rmtree(state_dir, ignore_errors=True)

    return dest_dir


def save_collected_artifacts(pytest_tmp_dir: Path, artifacts_dir: Path) -> Optional[Path]:
    """Save collected tests and cluster artifacts."""
    pytest_tmp_dir = pytest_tmp_dir.resolve()
    if not pytest_tmp_dir.is_dir():
        return None

    dest_dir = artifacts_dir / f"{pytest_tmp_dir.stem}-{clusterlib.get_rand_str(8)}"
    if dest_dir.resolve().is_dir():
        shutil.rmtree(dest_dir)
    shutil.copytree(pytest_tmp_dir, dest_dir, symlinks=True, ignore_dangling_symlinks=True)

    LOGGER.info(f"Collected artifacts saved to '{artifacts_dir}'.")
    return dest_dir


def save_artifacts(pytest_tmp_dir: Path, pytest_config: Config) -> None:
    """Save tests and cluster artifacts."""
    artifacts_base_dir = pytest_config.getoption("--artifacts-base-dir")
    if not artifacts_base_dir:
        return

    save_collected_artifacts(pytest_tmp_dir, Path(artifacts_base_dir))
