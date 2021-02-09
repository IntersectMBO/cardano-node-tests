"""Functionality for cluster setup and interaction with cluster nodes."""
import logging
import os
import pickle
import shutil
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional

from _pytest.config import Config

from cardano_node_tests.utils import cluster_scripts
from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType

LOGGER = logging.getLogger(__name__)

ADDRS_DATA = "addrs_data.pickle"


class ClusterEnv(NamedTuple):
    socket_path: Path
    state_dir: Path
    repo_dir: Path
    work_dir: Path
    instance_num: int
    cluster_era: str
    tx_era: str


class ClusterType:
    """Generic cluster type."""

    DEVOPS = "devops"
    LOCAL = "local"
    TESTNET = "testnet"
    TESTNET_NOPOOLS = "testnet_nopools"
    test_addr_records = ("user1",)

    def __init__(self) -> None:
        self.type = "unknown"
        self.cluster_scripts = cluster_scripts.ScriptsTypes()

    def get_cluster_obj(self) -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        raise NotImplementedError(f"Not implemented for cluster type '{self.type}'.")

    def create_addrs_data(
        self, cluster_obj: clusterlib.ClusterLib, destination_dir: FileType = "."
    ) -> Dict[str, Dict[str, Any]]:
        """Create addresses and their keys for usage in tests."""
        raise NotImplementedError(f"Not implemented for cluster type '{self.type}'.")


class DevopsCluster(ClusterType):
    """DevOps cluster type (shelley-only mode)."""

    def __init__(self) -> None:
        super().__init__()
        self.type = ClusterType.DEVOPS
        self.cluster_scripts = cluster_scripts.DevopsScripts()

    def get_cluster_obj(self) -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        cluster_obj = clusterlib.ClusterLib(get_cluster_env().state_dir)
        return cluster_obj

    def create_addrs_data(
        self, cluster_obj: clusterlib.ClusterLib, destination_dir: FileType = "."
    ) -> Dict[str, Dict[str, Any]]:
        """Create addresses and their keys for usage in tests."""
        destination_dir = Path(destination_dir).expanduser()
        destination_dir.mkdir(parents=True, exist_ok=True)
        cluster_env = get_cluster_env()
        instance_num = cluster_env.instance_num

        addrs_data: Dict[str, Dict[str, Any]] = {}
        for addr_name in self.test_addr_records:
            addr_name_instance = f"{addr_name}_ci{instance_num}"
            payment = cluster_obj.gen_payment_addr_and_keys(
                name=addr_name_instance,
                destination_dir=destination_dir,
            )
            addrs_data[addr_name] = {
                "payment": payment,
            }

        LOGGER.debug("Funding created addresses.")
        # fund from shelley genesis
        clusterlib_utils.fund_from_genesis(
            *[d["payment"].address for d in addrs_data.values()],
            cluster_obj=cluster_obj,
            amount=6_000_000_000_000,
            destination_dir=destination_dir,
        )

        return addrs_data


class LocalCluster(ClusterType):
    """Local cluster type (full cardano mode)."""

    def __init__(self) -> None:
        super().__init__()
        self.type = ClusterType.LOCAL
        self.cluster_scripts = cluster_scripts.LocalScripts()

    def get_cluster_obj(self) -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        cluster_env = get_cluster_env()
        cluster_obj = clusterlib.ClusterLib(
            state_dir=cluster_env.state_dir,
            protocol=clusterlib.Protocols.CARDANO,
            era=cluster_env.cluster_era,
            tx_era=cluster_env.tx_era,
        )
        return cluster_obj

    def create_addrs_data(
        self, cluster_obj: clusterlib.ClusterLib, destination_dir: FileType = "."
    ) -> Dict[str, Dict[str, Any]]:
        """Create addresses and their keys for usage in tests."""
        destination_dir = Path(destination_dir).expanduser()
        destination_dir.mkdir(parents=True, exist_ok=True)
        cluster_env = get_cluster_env()
        instance_num = cluster_env.instance_num

        addrs_data: Dict[str, Dict[str, Any]] = {}
        for addr_name in self.test_addr_records:
            addr_name_instance = f"{addr_name}_ci{instance_num}"
            payment = cluster_obj.gen_payment_addr_and_keys(
                name=addr_name_instance,
                destination_dir=destination_dir,
            )
            addrs_data[addr_name] = {
                "payment": payment,
            }

        LOGGER.debug("Funding created addresses.")
        # update `addrs_data` with byron addresses
        byron_dir = get_cluster_env().state_dir / "byron"
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
        to_fund = [d["payment"] for d in addrs_data.values()]
        clusterlib_utils.fund_from_faucet(
            *to_fund,
            cluster_obj=cluster_obj,
            faucet_data=addrs_data["byron000"],
            amount=6_000_000_000_000,
            destination_dir=destination_dir,
            force=True,
        )

        return addrs_data


class TestnetCluster(ClusterType):
    """Testnet cluster type (full cardano mode)."""

    def __init__(self) -> None:
        super().__init__()
        self.type = ClusterType.TESTNET
        self.cluster_scripts: cluster_scripts.TestnetScripts = cluster_scripts.TestnetScripts()

    def get_cluster_obj(self) -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        cluster_env = get_cluster_env()
        cluster_obj = clusterlib.ClusterLib(
            state_dir=cluster_env.state_dir,
            protocol=clusterlib.Protocols.CARDANO,
            era=cluster_env.cluster_era,
            tx_era=cluster_env.tx_era,
        )
        return cluster_obj

    def create_addrs_data(
        self, cluster_obj: clusterlib.ClusterLib, destination_dir: FileType = "."
    ) -> Dict[str, Dict[str, Any]]:
        """Create addresses and their keys for usage in tests."""
        shelley_dir = get_cluster_env().state_dir / "shelley"

        addrs_data: Dict[str, Dict[str, Any]] = {}
        for addr_name in self.test_addr_records:
            faucet_addr = {
                "payment": clusterlib.AddressRecord(
                    address=clusterlib.read_address_from_file(shelley_dir / "faucet.addr"),
                    vkey_file=shelley_dir / "faucet.vkey",
                    skey_file=shelley_dir / "faucet.skey",
                )
            }
            addrs_data[addr_name] = faucet_addr

        return addrs_data


class TestnetNopoolsCluster(TestnetCluster):
    """Testnet cluster type (full cardano mode)."""

    def __init__(self) -> None:
        super().__init__()
        self.type = ClusterType.TESTNET_NOPOOLS
        self.cluster_scripts: cluster_scripts.TestnetNopoolsScripts = (
            cluster_scripts.TestnetNopoolsScripts()
        )


def get_cluster_type() -> ClusterType:
    """Return instance of the cluster type indicated by configuration."""
    if configuration.BOOTSTRAP_DIR and configuration.NOPOOLS:
        return TestnetNopoolsCluster()
    if configuration.BOOTSTRAP_DIR:
        return TestnetCluster()
    if configuration.CLUSTER_ERA == "shelley":
        return DevopsCluster()
    return LocalCluster()


# cluster type doesn't change during test run, so it can be used as constant
CLUSTER_TYPE = get_cluster_type()


def _get_cardano_node_socket_path(instance_num: int) -> Path:
    """Return path to socket file in the given cluster instance."""
    socket_path = Path(os.environ["CARDANO_NODE_SOCKET_PATH"]).resolve()
    state_cluster_dirname = f"state-cluster{instance_num}"
    state_cluster = socket_path.parent.parent / state_cluster_dirname
    new_socket_path = state_cluster / socket_path.name
    return new_socket_path


def set_cardano_node_socket_path(instance_num: int) -> None:
    """Set the `CARDANO_NODE_SOCKET_PATH` env variable for the given cluster instance."""
    socket_path = _get_cardano_node_socket_path(instance_num)
    os.environ["CARDANO_NODE_SOCKET_PATH"] = str(socket_path)


def get_cluster_env() -> ClusterEnv:
    """Get cardano cluster environment."""
    socket_path = Path(os.environ["CARDANO_NODE_SOCKET_PATH"]).expanduser().resolve()
    state_dir = socket_path.parent
    work_dir = state_dir.parent
    repo_dir = Path(os.environ.get("CARDANO_NODE_REPO_PATH") or work_dir)
    instance_num = int(state_dir.name.replace("state-cluster", "") or 0)

    cluster_env = ClusterEnv(
        socket_path=socket_path,
        state_dir=state_dir,
        repo_dir=repo_dir,
        work_dir=work_dir,
        instance_num=instance_num,
        cluster_era=configuration.CLUSTER_ERA,
        tx_era=configuration.TX_ERA,
    )
    return cluster_env


def start_cluster(cmd: str, args: List[str]) -> clusterlib.ClusterLib:
    """Start cluster."""
    args_str = " ".join(args)
    args_str = f" {args_str}" if args_str else ""
    LOGGER.info(f"Starting cluster with `{cmd}{args_str}`.")
    helpers.run_shell_command(f"{cmd}{args_str}", workdir=get_cluster_env().work_dir)
    LOGGER.info("Cluster started.")
    return CLUSTER_TYPE.get_cluster_obj()


def stop_cluster(cmd: str) -> None:
    """Stop cluster."""
    LOGGER.info(f"Stopping cluster with `{cmd}`.")
    try:
        helpers.run_shell_command(cmd, workdir=get_cluster_env().work_dir)
    except Exception as exc:
        LOGGER.debug(f"Failed to stop cluster: {exc}")


def restart_node(node_name: str) -> None:
    """Restart single node of the running cluster."""
    LOGGER.info(f"Restarting cluster node `{node_name}`.")
    cluster_env = get_cluster_env()
    supervisor_port = CLUSTER_TYPE.cluster_scripts.get_instance_ports(
        cluster_env.instance_num
    ).supervisor
    try:
        helpers.run_command(
            f"supervisorctl -s http://localhost:{supervisor_port} restart {node_name}",
            workdir=cluster_env.work_dir,
        )
    except Exception as exc:
        LOGGER.debug(f"Failed to restart cluster node `{node_name}`: {exc}")


def load_pools_data(cluster_obj: clusterlib.ClusterLib) -> dict:
    """Load data for pools existing in the cluster environment."""
    data_dir = get_cluster_env().state_dir / "nodes"
    pools = ("node-pool1", "node-pool2")

    pools_data = {}
    for pool_name in pools:
        pool_data_dir = data_dir / pool_name
        if not pool_data_dir.exists():
            LOGGER.info(f"The data for pool '{pool_name}' doesn't exist.")
            continue

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
    """Set addresses and their keys up for usage in tests."""
    destination_dir = Path(destination_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    cluster_env = get_cluster_env()

    LOGGER.debug("Creating addresses and keys for tests.")
    addrs_data = CLUSTER_TYPE.create_addrs_data(
        cluster_obj=cluster_obj, destination_dir=destination_dir
    )

    pools_data = load_pools_data(cluster_obj)
    data_file = Path(cluster_env.state_dir) / ADDRS_DATA
    with open(data_file, "wb") as out_data:
        pickle.dump({**addrs_data, **pools_data}, out_data)

    return data_file


def load_addrs_data() -> dict:
    """Load data about addresses and their keys for usage in tests."""
    data_file = Path(get_cluster_env().state_dir) / ADDRS_DATA
    with open(data_file, "rb") as in_data:
        return pickle.load(in_data)  # type: ignore


def save_cluster_artifacts(artifacts_dir: Path, clean: bool = False) -> Optional[Path]:
    """Save cluster artifacts."""
    destdir = artifacts_dir / f"cluster_artifacts_{clusterlib.get_rand_str(8)}"
    destdir.mkdir(parents=True)

    state_dir = Path(get_cluster_env().state_dir)
    files_list = list(state_dir.glob("*.std*"))
    files_list.extend(list(state_dir.glob("*.json")))
    dirs_to_copy = ("nodes", "shelley")

    for fpath in files_list:
        shutil.copy(fpath, destdir)
    for dname in dirs_to_copy:
        src_dir = state_dir / dname
        if not src_dir.exists():
            continue
        shutil.copytree(src_dir, destdir / dname, symlinks=True, ignore_dangling_symlinks=True)

    if not os.listdir(destdir):
        destdir.rmdir()
        return None

    LOGGER.info(f"Cluster artifacts saved to '{destdir}'.")

    if clean:
        LOGGER.info(f"Cleaning cluster artifacts in '{state_dir}'.")
        shutil.rmtree(state_dir, ignore_errors=True)

    return destdir


def save_collected_artifacts(pytest_tmp_dir: Path, artifacts_dir: Path) -> Optional[Path]:
    """Save collected tests and cluster artifacts."""
    pytest_tmp_dir = pytest_tmp_dir.resolve()
    if not pytest_tmp_dir.is_dir():
        return None

    destdir = artifacts_dir / f"{pytest_tmp_dir.stem}-{clusterlib.get_rand_str(8)}"
    if destdir.resolve().is_dir():
        shutil.rmtree(destdir)
    shutil.copytree(pytest_tmp_dir, destdir, symlinks=True, ignore_dangling_symlinks=True)

    LOGGER.info(f"Collected artifacts saved to '{artifacts_dir}'.")
    return destdir


def save_artifacts(pytest_tmp_dir: Path, pytest_config: Config) -> None:
    """Save tests and cluster artifacts."""
    artifacts_base_dir = pytest_config.getoption("--artifacts-base-dir")
    if not artifacts_base_dir:
        return

    save_collected_artifacts(pytest_tmp_dir, Path(artifacts_base_dir))
