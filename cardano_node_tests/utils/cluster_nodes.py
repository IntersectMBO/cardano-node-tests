"""Functionality for cluster setup and interaction with cluster nodes."""
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
from packaging import version

from cardano_node_tests.utils import cluster_instances
from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType

LOGGER = logging.getLogger(__name__)

ADDRS_DATA = "addrs_data.pickle"


class Versions:
    """Cluster era and transaction era determined from env variables, and node version."""

    BYRON = 1
    SHELLEY = 2
    ALLEGRA = 3
    MARY = 4

    def __init__(self) -> None:
        cluster_era = configuration.CLUSTER_ERA
        # if not specified otherwise, transaction era is the same as cluster era
        transaction_era = configuration.TX_ERA or cluster_era

        self.cluster_era = getattr(self, cluster_era.upper(), 1)
        self.transaction_era = getattr(self, transaction_era.upper(), 1)
        self.node = version.parse(helpers.CARDANO_VERSION["cardano-node"])

    def __repr__(self) -> str:
        return (
            f"<Versions: cluster_era={self.cluster_era}, "
            f"transaction_era={self.transaction_era}, "
            f"node={helpers.CARDANO_VERSION['cardano-node']}>"
        )


# versions don't change during test run, so it can be used as constant
VERSIONS = Versions()


class StartupFiles(NamedTuple):
    start_script: Path
    genesis_spec: Path
    config_glob: str


class ClusterType:
    """Generic cluster type."""

    DEVOPS = "devops"
    LOCAL = "local"

    def __init__(self) -> None:
        self.version = VERSIONS
        self.type = "unknown"
        self.instances = cluster_instances.InstanceType()

    def copy_startup_files(self, destdir: Path) -> StartupFiles:
        """Make copy of cluster startup files."""
        raise NotImplementedError(f"Not implemented for cluster type '{self.type}'.")

    def get_cluster_obj(self) -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        raise NotImplementedError(f"Not implemented for cluster type '{self.type}'.")

    def fund_setup(
        self, cluster_obj: clusterlib.ClusterLib, addrs_data: dict, destination_dir: FileType = "."
    ) -> None:
        """Fund addresses created during cluster setup."""
        raise NotImplementedError(f"Not implemented for cluster type '{self.type}'.")


class DevopsCluster(ClusterType):
    """DevOps cluster type (shelley-only mode)."""

    def __init__(self) -> None:
        super().__init__()
        self.type = ClusterType.DEVOPS
        self.instances = cluster_instances.DevopsInstance()

    def _get_node_config_paths(self, start_script: Path) -> List[Path]:
        """Return path of node config files in nix."""
        with open(start_script) as infile:
            content = infile.read()

        node_config = re.findall(r"cp /nix/store/(.+\.json) ", content)
        nix_store = Path("/nix/store")
        node_config_paths = [nix_store / c for c in node_config]

        return node_config_paths

    def copy_startup_files(self, destdir: Path) -> StartupFiles:
        """Make copy of cluster startup files located on nix."""
        start_script_orig = helpers.get_cmd_path("start-cluster")
        shutil.copy(start_script_orig, destdir)
        start_script = destdir / "start-cluster"
        start_script.chmod(0o755)

        node_config_paths = self._get_node_config_paths(start_script)
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

        config_glob = "node.json"
        genesis_spec_json = destdir / "genesis.spec.json"
        assert genesis_spec_json.exists()

        return StartupFiles(
            start_script=start_script, genesis_spec=genesis_spec_json, config_glob=config_glob
        )

    def get_cluster_obj(self) -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        cluster_env = get_cluster_env()
        cluster_obj = clusterlib.ClusterLib(cluster_env["state_dir"])
        return cluster_obj

    def fund_setup(
        self, cluster_obj: clusterlib.ClusterLib, addrs_data: dict, destination_dir: FileType = "."
    ) -> None:
        """Fund addresses created during cluster setup."""
        # fund from shelley genesis
        clusterlib_utils.fund_from_genesis(
            *[d["payment"].address for d in addrs_data.values()],
            cluster_obj=cluster_obj,
            amount=6_000_000_000_000,
            destination_dir=destination_dir,
        )


class LocalCluster(ClusterType):
    """Local cluster type (full cardano mode)."""

    def __init__(self) -> None:
        super().__init__()
        self.type = ClusterType.LOCAL
        self.instances = cluster_instances.LocalInstance()

    def copy_startup_files(self, destdir: Path) -> StartupFiles:
        """Make copy of cluster startup files located in this repository."""
        scripts_dir = configuration.SCRIPTS_DIR
        shutil.copytree(
            scripts_dir, destdir, symlinks=True, ignore_dangling_symlinks=True, dirs_exist_ok=True
        )

        start_script = destdir / "start-cluster-hfc"
        config_glob = "config-*.json"
        genesis_spec_json = destdir / "genesis.spec.json"
        assert start_script.exists() and genesis_spec_json.exists()

        return StartupFiles(
            start_script=start_script, genesis_spec=genesis_spec_json, config_glob=config_glob
        )

    def get_cluster_obj(self) -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        cluster_env = get_cluster_env()
        cluster_obj = clusterlib.ClusterLib(
            cluster_env["state_dir"],
            protocol=clusterlib.Protocols.CARDANO,
            era=cluster_env["cluster_era"],
            tx_era=cluster_env["tx_era"],
        )
        return cluster_obj

    def fund_setup(
        self, cluster_obj: clusterlib.ClusterLib, addrs_data: dict, destination_dir: FileType = "."
    ) -> None:
        """Fund addresses created during cluster setup."""
        cluster_env = get_cluster_env()
        to_fund = [d["payment"] for d in addrs_data.values()]
        byron_dir = cluster_env["state_dir"] / "byron"
        for b in range(3):
            byron_addr = {
                "payment": clusterlib.AddressRecord(
                    address=clusterlib.read_address_from_file(
                        byron_dir / f"address-00{b}-converted"
                    ),
                    vkey_file=byron_dir / f"payment-keys.00{b}-converted.vkey",
                    skey_file=byron_dir / f"payment-keys.00{b}-converted.skey",
                )
            }
            addrs_data[f"byron00{b}"] = byron_addr

        # fund from converted byron address
        clusterlib_utils.fund_from_faucet(
            *to_fund,
            cluster_obj=cluster_obj,
            faucet_data=addrs_data["byron000"],
            amount=6_000_000_000_000,
            destination_dir=destination_dir,
            force=True,
        )


def get_cluster_type() -> ClusterType:
    if configuration.CLUSTER_ERA == "shelley":
        return DevopsCluster()
    return LocalCluster()


# cluster type doesn't change during test run, so it can be used as constant
CLUSTER_TYPE = get_cluster_type()


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
        "cluster_era": configuration.CLUSTER_ERA,
        "tx_era": configuration.TX_ERA,
    }
    return cluster_env


def start_cluster(cmd: str) -> clusterlib.ClusterLib:
    """Start cluster."""
    LOGGER.info(f"Starting cluster with `{cmd}`.")
    cluster_env = get_cluster_env()
    helpers.run_shell_command(cmd, workdir=cluster_env["work_dir"])
    LOGGER.info("Cluster started.")
    return CLUSTER_TYPE.get_cluster_obj()


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
    supervisor_port = CLUSTER_TYPE.instances.get_instance_ports(
        cluster_env["instance_num"]
    ).supervisor
    try:
        helpers.run_command(
            f"supervisorctl -s http://localhost:{supervisor_port} restart {node_name}",
            workdir=cluster_env["work_dir"],
        )
    except Exception as exc:
        LOGGER.debug(f"Failed to restart cluster node `{node_name}`: {exc}")


def load_pools_data(cluster_obj: clusterlib.ClusterLib) -> dict:
    """Load data for pools existing in the cluster environment."""
    data_dir = get_cluster_env()["state_dir"] / "nodes"
    pools = ("node-pool1", "node-pool2")

    pools_data = {}
    for pool_name in pools:
        pool_data_dir = data_dir / pool_name
        pools_data[pool_name] = {
            "payment": clusterlib.AddressRecord(
                address=clusterlib.read_address_from_file(pool_data_dir / "owner.addr"),
                vkey_file=pool_data_dir / "owner-utxo.vkey",
                skey_file=pool_data_dir / "owner-utxo.skey",
            ),
            "stake": clusterlib.AddressRecord(
                address=clusterlib.read_address_from_file(pool_data_dir / "owner-stake.addr"),
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
    CLUSTER_TYPE.fund_setup(
        cluster_obj=cluster_obj, addrs_data=addrs_data, destination_dir=destination_dir
    )

    pools_data = load_pools_data(cluster_obj)

    data_file = Path(cluster_env["state_dir"]) / ADDRS_DATA
    with open(data_file, "wb") as out_data:
        pickle.dump({**addrs_data, **pools_data}, out_data)
    return data_file


def load_addrs_data() -> dict:
    """Load data about addresses and their keys for usage in tests."""
    cluster_env = get_cluster_env()
    data_file = Path(cluster_env["state_dir"]) / ADDRS_DATA
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
