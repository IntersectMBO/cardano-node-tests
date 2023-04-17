"""Functionality for cluster setup and interaction with cluster nodes."""
import json
import logging
import os
import pickle
import time
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Set
from typing import Union

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_scripts
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import slots_offset
from cardano_node_tests.utils.types import FileType

LOGGER = logging.getLogger(__name__)

ADDRS_DATA = "addrs_data.pickle"
STATE_CLUSTER = "state-cluster"


class ClusterEnv(NamedTuple):
    socket_path: Path
    state_dir: Path
    work_dir: Path
    instance_num: int
    cluster_era: str
    tx_era: str


class ServiceStatus(NamedTuple):
    name: str
    status: str
    pid: Optional[int]
    uptime: Optional[str]
    message: str = ""


class Testnets:
    shelley_qa = "shelley_qa"
    testnet = "testnet"
    staging = "staging"
    preview = "preview"
    preprod = "preprod"
    mainnet = "mainnet"


class ClusterType:
    """Generic cluster type."""

    LOCAL = "local"
    TESTNET = "testnet"
    TESTNET_NOPOOLS = "testnet_nopools"
    test_addr_records = ("user1",)

    NODES: Set[str] = set()

    def __init__(self) -> None:
        self.type = "unknown"
        self.cluster_scripts = cluster_scripts.ScriptsTypes()

    @property
    def testnet_type(self) -> str:
        return ""

    @property
    def uses_shortcut(self) -> bool:
        """Check if cluster uses shortcut to go from Byron to last supported era."""
        raise NotImplementedError(f"Not implemented for cluster type '{self.type}'.")

    def get_cluster_obj(
        self, protocol: str = "", tx_era: str = "", slots_offset: int = 0
    ) -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        raise NotImplementedError(f"Not implemented for cluster type '{self.type}'.")

    def create_addrs_data(
        self, cluster_obj: clusterlib.ClusterLib, destination_dir: FileType = "."
    ) -> Dict[str, Dict[str, Any]]:
        """Create addresses and their keys for usage in tests."""
        raise NotImplementedError(f"Not implemented for cluster type '{self.type}'.")


class LocalCluster(ClusterType):
    """Local cluster type (full cardano mode)."""

    NODES = {"bft1", *(f"pool{i}" for i in range(1, configuration.NUM_POOLS + 1))}

    def __init__(self) -> None:
        super().__init__()
        self.type = ClusterType.LOCAL
        self.cluster_scripts = cluster_scripts.LocalScripts()

    @property
    def uses_shortcut(self) -> bool:
        """Check if cluster uses shortcut to go from Byron to last supported era."""
        byron_dir = get_cluster_env().state_dir / "byron"
        if not byron_dir.exists():
            raise RuntimeError("Can't check, cluster instance was not started yet.")

        _uses_shortcut = not (byron_dir / "address-000-converted").exists()
        return _uses_shortcut

    def _get_slots_offset(self, state_dir: Path) -> int:
        """Get offset of blocks from Byron era vs current configuration.

        Unlike in `TestnetCluster`, don't cache slots offset value, we might
        test different configurations of slot length etc.
        """
        with open(state_dir / "config-pool1.json", encoding="utf-8") as in_fp:
            config_json = json.load(in_fp)

        shelley_hf_epoch = config_json.get("TestShelleyHardForkAtEpoch")

        # if the testnet wasn't HF to Shelley in config, assume there was single Byron epoch
        byron_epochs = int(shelley_hf_epoch) if shelley_hf_epoch is not None else 1

        # no slots offset if the testnet was started in Shelley-based era
        if byron_epochs == 0:
            return 0

        offset = slots_offset.get_slots_offset(
            genesis_byron=state_dir / "byron" / "genesis.json",
            genesis_shelley=state_dir / "shelley" / "genesis.json",
            byron_epochs=byron_epochs,
        )

        return offset

    def get_cluster_obj(
        self, protocol: str = "", tx_era: str = "", slots_offset: int = 0
    ) -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        cluster_env = get_cluster_env()
        cluster_obj = clusterlib.ClusterLib(
            state_dir=cluster_env.state_dir,
            protocol=protocol or clusterlib.Protocols.CARDANO,
            tx_era=tx_era or cluster_env.tx_era,
            slots_offset=slots_offset or self._get_slots_offset(cluster_env.state_dir),
        )
        cluster_obj.overwrite_outfiles = not (configuration.DONT_OVERWRITE_OUTFILES)
        cluster_obj._min_change_value = 2_000_000  # TODO: hardcoded `minUTxOValue`
        return cluster_obj

    def create_addrs_data(
        self, cluster_obj: clusterlib.ClusterLib, destination_dir: FileType = "."
    ) -> Dict[str, Dict[str, Any]]:
        """Create addresses and their keys for usage in tests."""
        destination_dir = Path(destination_dir).expanduser()
        destination_dir.mkdir(parents=True, exist_ok=True)
        cluster_env = get_cluster_env()
        instance_num = cluster_env.instance_num

        # create new addresses
        new_addrs_data: Dict[str, Dict[str, Any]] = {}
        for addr_name in self.test_addr_records:
            addr_name_instance = f"{addr_name}_ci{instance_num}"
            payment = cluster_obj.g_address.gen_payment_addr_and_keys(
                name=addr_name_instance,
                destination_dir=destination_dir,
            )
            new_addrs_data[addr_name] = {
                "payment": payment,
            }

        # create records for existing addresses
        faucet_addrs_data: Dict[str, Dict[str, Any]] = {"faucet": {"payment": None}}
        byron_dir = cluster_env.state_dir / "byron"
        shelley_dir = cluster_env.state_dir / "shelley"

        if (byron_dir / "address-000-converted").exists():
            faucet_addrs_data["faucet"]["payment"] = clusterlib.AddressRecord(
                address=clusterlib.read_address_from_file(byron_dir / "address-000-converted"),
                vkey_file=byron_dir / "payment-keys.000-converted.vkey",
                skey_file=byron_dir / "payment-keys.000-converted.skey",
            )
        elif (shelley_dir / "genesis-utxo.addr").exists():
            faucet_addrs_data["faucet"]["payment"] = clusterlib.AddressRecord(
                address=clusterlib.read_address_from_file(shelley_dir / "genesis-utxo.addr"),
                vkey_file=shelley_dir / "genesis-utxo.vkey",
                skey_file=shelley_dir / "genesis-utxo.skey",
            )
        else:
            raise RuntimeError("Faucet address file doesn't exist.")

        # fund new addresses from faucet address
        LOGGER.debug("Funding created addresses.")
        to_fund = [d["payment"] for d in new_addrs_data.values()]
        clusterlib_utils.fund_from_faucet(
            *to_fund,
            cluster_obj=cluster_obj,
            faucet_data=faucet_addrs_data["faucet"],
            amount=100_000_000_000_000,
            destination_dir=destination_dir,
            force=True,
        )

        addrs_data = {**new_addrs_data, **faucet_addrs_data}
        return addrs_data


class TestnetCluster(ClusterType):
    """Testnet cluster type (full cardano mode)."""

    TESTNETS: Dict[int, dict] = {
        1597669200: {"type": Testnets.shelley_qa, "shelley_start": "2020-08-17T17:00:00Z"},
        1563999616: {"type": Testnets.testnet, "shelley_start": "2020-07-28T20:20:16Z"},
        1506450213: {"type": Testnets.staging, "shelley_start": "2020-08-01T18:23:33Z"},
        1506203091: {"type": Testnets.mainnet, "shelley_start": "2020-07-29T21:44:51Z"},
        1654041600: {"type": Testnets.preprod, "byron_epochs": 4},
        1666656000: {"type": Testnets.preview, "byron_epochs": 0},
    }

    NODES = {"relay1", "pool1", "pool2"}

    def __init__(self) -> None:
        super().__init__()
        self.type = ClusterType.TESTNET
        self.cluster_scripts: Union[
            cluster_scripts.ScriptsTypes, cluster_scripts.TestnetScripts
        ] = cluster_scripts.TestnetScripts()

        # cached values
        self._testnet_type = ""
        self._slots_offset = -1

    @property
    def uses_shortcut(self) -> bool:
        """Check if cluster uses shortcut to go from Byron to last supported era."""
        return False

    @property
    def testnet_type(self) -> str:
        """Return testnet type (shelley_qa, etc.)."""
        if self._testnet_type:
            return self._testnet_type

        cluster_env = get_cluster_env()
        genesis_byron_json = cluster_env.state_dir / "genesis-byron.json"
        with open(genesis_byron_json, encoding="utf-8") as in_json:
            genesis_byron = json.load(in_json)

        start_timestamp: int = genesis_byron["startTime"]
        testnet_type: str = self.TESTNETS.get(start_timestamp, {}).get("type", "unknown")

        self._testnet_type = testnet_type
        return testnet_type

    def _get_slots_offset(self, state_dir: Path) -> int:
        """Get offset of blocks from Byron era vs current configuration."""
        if self._slots_offset != -1:
            return self._slots_offset

        genesis_byron = state_dir / "genesis-byron.json"
        genesis_shelley = state_dir / "genesis-shelley.json"

        with open(genesis_byron, encoding="utf-8") as in_json:
            byron_dict = json.load(in_json)
        start_timestamp: int = byron_dict["startTime"]

        testnet_dict: Dict[str, Any] = self.TESTNETS.get(start_timestamp) or {}
        shelley_start: str = testnet_dict.get("shelley_start") or ""
        byron_epochs: int = testnet_dict.get("byron_epochs") or 0
        if not (shelley_start or byron_epochs):
            self._slots_offset = 0
            return 0

        offset = slots_offset.get_slots_offset(
            genesis_byron=genesis_byron,
            genesis_shelley=genesis_shelley,
            shelley_start=shelley_start,
            byron_epochs=byron_epochs,
        )

        self._slots_offset = offset
        return offset

    def get_cluster_obj(
        self, protocol: str = "", tx_era: str = "", slots_offset: int = 0
    ) -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        cluster_env = get_cluster_env()
        cluster_obj = clusterlib.ClusterLib(
            state_dir=cluster_env.state_dir,
            protocol=protocol or clusterlib.Protocols.CARDANO,
            tx_era=tx_era or cluster_env.tx_era,
            slots_offset=slots_offset or self._get_slots_offset(cluster_env.state_dir),
        )
        cluster_obj.overwrite_outfiles = not (configuration.DONT_OVERWRITE_OUTFILES)
        cluster_obj._min_change_value = 2_000_000  # TODO: hardcoded `minUTxOValue`
        return cluster_obj

    def create_addrs_data(
        self, cluster_obj: clusterlib.ClusterLib, destination_dir: FileType = "."  # noqa: ARG002
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

    NODES = {"relay1"}

    def __init__(self) -> None:
        super().__init__()
        self.type = ClusterType.TESTNET_NOPOOLS
        self.cluster_scripts: cluster_scripts.TestnetNopoolsScripts = (
            cluster_scripts.TestnetNopoolsScripts()
        )


@helpers.callonce
def get_cluster_type() -> ClusterType:
    """Return instance of the cluster type indicated by configuration."""
    if configuration.BOOTSTRAP_DIR and configuration.NOPOOLS:
        return TestnetNopoolsCluster()
    if configuration.BOOTSTRAP_DIR:
        return TestnetCluster()
    return LocalCluster()


def get_cardano_node_socket_path(instance_num: int, socket_file_name: str = "") -> Path:
    """Return path to socket file in the given cluster instance."""
    socket_file_name = socket_file_name or configuration.STARTUP_CARDANO_NODE_SOCKET_PATH.name
    state_cluster_dirname = f"{STATE_CLUSTER}{instance_num}"
    state_cluster = (
        configuration.STARTUP_CARDANO_NODE_SOCKET_PATH.parent.parent / state_cluster_dirname
    )
    new_socket_path = state_cluster / socket_file_name
    return new_socket_path


def set_cluster_env(instance_num: int, socket_file_name: str = "") -> None:
    """Set env variables for the given cluster instance."""
    socket_path = get_cardano_node_socket_path(
        instance_num=instance_num, socket_file_name=socket_file_name
    )
    os.environ["CARDANO_NODE_SOCKET_PATH"] = str(socket_path)

    os.environ["PGPASSFILE"] = str(socket_path.parent / "pgpass")
    os.environ["PGDATABASE"] = f"{configuration.DBSYNC_DB}{instance_num}"
    if not os.environ.get("PGHOST"):
        os.environ["PGHOST"] = "localhost"
    if not os.environ.get("PGPORT"):
        os.environ["PGPORT"] = "5432"
    if not os.environ.get("PGUSER"):
        os.environ["PGUSER"] = "postgres"


def get_instance_num() -> int:
    """Get cardano cluster instance number."""
    socket_path = Path(os.environ["CARDANO_NODE_SOCKET_PATH"])
    instance_num = int(socket_path.parent.name.replace(STATE_CLUSTER, "") or 0)
    return instance_num


def get_cluster_env() -> ClusterEnv:
    """Get cardano cluster environment."""
    socket_path = Path(os.environ["CARDANO_NODE_SOCKET_PATH"])
    state_dir = socket_path.parent
    work_dir = state_dir.parent
    instance_num = int(state_dir.name.replace(STATE_CLUSTER, "") or 0)

    cluster_env = ClusterEnv(
        socket_path=socket_path,
        state_dir=state_dir,
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
    helpers.run_command(f"{cmd}{args_str}", workdir=get_cluster_env().work_dir)
    LOGGER.info("Cluster started.")
    return get_cluster_type().get_cluster_obj()


def restart_all_nodes(instance_num: Optional[int] = None) -> None:
    """Restart all Cardano nodes of the running cluster."""
    LOGGER.info("Restarting all cluster nodes.")

    if instance_num is None:
        instance_num = get_cluster_env().instance_num

    supervisor_port = get_cluster_type().cluster_scripts.get_instance_ports(instance_num).supervisor
    try:
        helpers.run_command(f"supervisorctl -s http://localhost:{supervisor_port} restart nodes:")
    except Exception as exc:
        raise Exception("Failed to restart cluster nodes.") from exc

    # wait for nodes to start
    time.sleep(5)


def services_action(
    service_names: List[str], action: str, instance_num: Optional[int] = None
) -> None:
    """Perform action on services on the running cluster."""
    LOGGER.info(f"Performing '{action}' action on services {service_names}.")

    if instance_num is None:
        instance_num = get_cluster_env().instance_num

    supervisor_port = get_cluster_type().cluster_scripts.get_instance_ports(instance_num).supervisor
    for service_name in service_names:
        try:
            helpers.run_command(
                f"supervisorctl -s http://localhost:{supervisor_port} {action} {service_name}"
            )
        except Exception as exc:
            raise Exception(f"Failed to restart service `{service_name}`") from exc


def start_nodes(node_names: List[str], instance_num: Optional[int] = None) -> None:
    """Start list of Cardano nodes of the running cluster."""
    service_names = [f"nodes:{n}" for n in node_names]
    services_action(service_names=service_names, action="start", instance_num=instance_num)


def stop_nodes(node_names: List[str], instance_num: Optional[int] = None) -> None:
    """Stop list of Cardano nodes of the running cluster."""
    service_names = [f"nodes:{n}" for n in node_names]
    services_action(service_names=service_names, action="stop", instance_num=instance_num)


def restart_nodes(node_names: List[str], instance_num: Optional[int] = None) -> None:
    """Restart list of Cardano nodes of the running cluster."""
    service_names = [f"nodes:{n}" for n in node_names]
    services_action(service_names=service_names, action="restart", instance_num=instance_num)

    # wait for nodes to start
    time.sleep(5)


def services_status(
    service_names: Optional[List[str]] = None, instance_num: Optional[int] = None
) -> List[ServiceStatus]:
    """Return status info for list of services running on the running cluster (all by default)."""
    if instance_num is None:
        instance_num = get_cluster_env().instance_num

    supervisor_port = get_cluster_type().cluster_scripts.get_instance_ports(instance_num).supervisor
    service_names_arg = " ".join(service_names) if service_names else "all"

    status_out = (
        helpers.run_command(
            f"supervisorctl -s http://localhost:{supervisor_port} status {service_names_arg}",
            ignore_fail=True,
        )
        .decode()
        .strip()
        .split("\n")
    )

    statuses = []
    for status_line in status_out:
        service_name, status, *running_status = status_line.split()
        if running_status and running_status[0] == "pid":
            _pid, pid, _uptime, uptime, *other = running_status
            message = " ".join(other)
        else:
            pid, uptime = "", ""
            message = " ".join(running_status)
        statuses.append(
            ServiceStatus(
                name=service_name,
                status=status,
                pid=int(pid.rstrip(",")) if pid else None,
                uptime=uptime or None,
                message=message,
            )
        )

    return statuses


def load_pools_data(cluster_obj: clusterlib.ClusterLib) -> dict:
    """Load data for pools existing in the cluster environment."""
    data_dir = get_cluster_env().state_dir / "nodes"

    pools_data = {}
    for pool_data_dir in data_dir.glob("node-pool*"):
        pools_data[pool_data_dir.name] = {
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
                address=cluster_obj.g_stake_address.gen_stake_addr(
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
    addrs_data = get_cluster_type().create_addrs_data(
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
