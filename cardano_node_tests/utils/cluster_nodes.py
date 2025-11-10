"""Functionality for cluster setup and interaction with cluster nodes."""

import dataclasses
import enum
import functools
import json
import logging
import os
import pathlib as pl
import pickle
import time
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_scripts
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import custom_clusterlib
from cardano_node_tests.utils import faucet
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

ADDRS_DATA = "addrs_data.pickle"
STATE_CLUSTER = "state-cluster"


@dataclasses.dataclass(frozen=True, order=True)
class ClusterEnv:
    socket_path: pl.Path
    state_dir: pl.Path
    work_dir: pl.Path
    instance_num: int
    cluster_era: str
    command_era: str


@dataclasses.dataclass(frozen=True, order=True)
class ServiceStatus:
    name: str
    status: str
    pid: int | None
    uptime: str | None
    message: str = ""


class Testnets(enum.StrEnum):
    preview = "preview"
    preprod = "preprod"
    mainnet = "mainnet"


class ClusterType:
    """Generic cluster type."""

    LOCAL: tp.Final[str] = "local"
    TESTNET: tp.Final[str] = "testnet"
    test_addr_records: tp.ClassVar[tuple[str, ...]] = (
        "user1",
        "user2",
        "user3",
        "user4",
        "user5",
    )

    NODES: tp.ClassVar[set[str]] = set()

    def __init__(self) -> None:
        self.type = "unknown"
        self.cluster_scripts: (
            cluster_scripts.ScriptsTypes
            | cluster_scripts.TestnetScripts
            | cluster_scripts.LocalScripts
        ) = cluster_scripts.ScriptsTypes()

    @property
    def testnet_type(self) -> str:
        return ""

    @property
    def uses_shortcut(self) -> bool:
        """Check if cluster uses shortcut to go from Byron to last supported era."""
        msg = f"Not implemented for cluster type '{self.type}'."
        raise NotImplementedError(msg)

    def get_cluster_obj(self, *, command_era: str = "") -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        msg = f"Not implemented for cluster type '{self.type}'."
        raise NotImplementedError(msg)

    def create_addrs_data(
        self, *, cluster_obj: clusterlib.ClusterLib, destination_dir: clusterlib.FileType = "."
    ) -> dict[str, dict[str, tp.Any]]:
        """Create addresses and their keys for usage in tests."""
        msg = f"Not implemented for cluster type '{self.type}'."
        raise NotImplementedError(msg)


class LocalCluster(ClusterType):
    """Local cluster type (full cardano mode)."""

    NODES: tp.ClassVar[set[str]] = {
        "bft1",
        *(f"pool{i}" for i in range(1, configuration.NUM_POOLS + 1)),
    }

    def __init__(self) -> None:
        super().__init__()
        self.type = ClusterType.LOCAL
        self.cluster_scripts = cluster_scripts.LocalScripts()

    @property
    def uses_shortcut(self) -> bool:
        """Check if cluster uses shortcut to go from Byron to last supported era."""
        byron_dir = get_cluster_env().state_dir / "byron"
        if not byron_dir.exists():
            msg = "Can't check, cluster instance was not started yet."
            raise RuntimeError(msg)

        _uses_shortcut = not (byron_dir / "address-000-converted").exists()
        return _uses_shortcut

    def get_cluster_obj(self, *, command_era: str = "") -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        cluster_env = get_cluster_env()
        cluster_obj = custom_clusterlib.ClusterLib(
            state_dir=cluster_env.state_dir,
            command_era=command_era or cluster_env.command_era or clusterlib.CommandEras.LATEST,
        )
        cluster_obj.overwrite_outfiles = not (configuration.DONT_OVERWRITE_OUTFILES)
        # Overwrite default settings for number of new blocks before the Tx is considered confirmed
        if configuration.CONFIRM_BLOCKS_NUM:
            cluster_obj.confirm_blocks = configuration.CONFIRM_BLOCKS_NUM
        # TODO: hardcoded `minUTxOValue`
        cluster_obj._min_change_value = 2_000_000
        return cluster_obj

    def create_addrs_data(
        self, *, cluster_obj: clusterlib.ClusterLib, destination_dir: clusterlib.FileType = "."
    ) -> dict[str, dict[str, tp.Any]]:
        """Create addresses and their keys for usage in tests."""
        destination_dir = pl.Path(destination_dir).expanduser()
        destination_dir.mkdir(parents=True, exist_ok=True)
        cluster_env = get_cluster_env()
        instance_num = cluster_env.instance_num

        # Create new addresses
        new_addrs_data: dict[str, dict[str, tp.Any]] = {}
        for addr_name in self.test_addr_records:
            addr_name_instance = f"{addr_name}_ci{instance_num}"
            payment = cluster_obj.g_address.gen_payment_addr_and_keys(
                name=addr_name_instance,
                destination_dir=destination_dir,
            )
            new_addrs_data[addr_name] = {
                "payment": payment,
            }

        # Create records for existing addresses
        faucet_addrs_data: dict[str, dict[str, tp.Any]] = {"faucet": {"payment": None}}
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
            msg = "Faucet address file doesn't exist."
            raise RuntimeError(msg)

        # Fund new addresses from faucet address
        LOGGER.debug("Funding created addresses.")
        to_fund = [d["payment"] for d in new_addrs_data.values()]
        amount_per_address = 100_000_000_000_000 // len(self.test_addr_records)
        faucet.fund_from_faucet(
            *to_fund,
            cluster_obj=cluster_obj,
            faucet_data=faucet_addrs_data["faucet"],
            amount=amount_per_address,
            destination_dir=destination_dir,
            force=True,
        )

        addrs_data = {**new_addrs_data, **faucet_addrs_data}
        return addrs_data


class TestnetCluster(ClusterType):
    """Testnet cluster type (full cardano mode)."""

    TESTNETS: tp.ClassVar[dict[int, dict]] = {
        1506203091: {"type": Testnets.mainnet, "shelley_start": "2020-07-29T21:44:51Z"},
        1654041600: {"type": Testnets.preprod, "byron_epochs": 4},
        1666656000: {"type": Testnets.preview, "byron_epochs": 0},
    }

    NODES: tp.ClassVar[set[str]] = {"relay1"}

    def __init__(self) -> None:
        super().__init__()
        self.type = ClusterType.TESTNET
        self.cluster_scripts = cluster_scripts.TestnetScripts()

        # Cached values
        self._testnet_type = ""

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

    def get_cluster_obj(self, *, command_era: str = "") -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        cluster_env = get_cluster_env()
        cluster_obj = custom_clusterlib.ClusterLib(
            state_dir=cluster_env.state_dir,
            command_era=command_era or cluster_env.command_era or clusterlib.CommandEras.LATEST,
        )
        cluster_obj.overwrite_outfiles = not (configuration.DONT_OVERWRITE_OUTFILES)
        # Increase default number of new blocks before the Tx is considered confirmed
        cluster_obj.confirm_blocks = configuration.CONFIRM_BLOCKS_NUM or 3
        # TODO: hardcoded `minUTxOValue`
        cluster_obj._min_change_value = 2_000_000
        return cluster_obj

    def create_addrs_data(
        self, *, cluster_obj: clusterlib.ClusterLib, destination_dir: clusterlib.FileType = "."
    ) -> dict[str, dict[str, tp.Any]]:
        """Create addresses and their keys for usage in tests."""
        # Store record of the original faucet address
        shelley_dir = get_cluster_env().state_dir / "shelley"
        faucet_rec = clusterlib.AddressRecord(
            address=clusterlib.read_address_from_file(shelley_dir / "faucet.addr"),
            vkey_file=shelley_dir / "faucet.vkey",
            skey_file=shelley_dir / "faucet.skey",
        )
        faucet_addrs_data: dict[str, dict[str, tp.Any]] = {
            self.test_addr_records[1]: {"payment": faucet_rec}
        }

        # Create new addresses
        new_addrs_data: dict[str, dict[str, tp.Any]] = {}
        for addr_name in self.test_addr_records[1:]:
            payment = cluster_obj.g_address.gen_payment_addr_and_keys(
                name=addr_name,
                destination_dir=destination_dir,
            )
            new_addrs_data[addr_name] = {
                "payment": payment,
            }

        faucet_balance = cluster_obj.g_query.get_address_balance(address=faucet_rec.address)
        LOGGER.info(f"Initial faucet balance: {faucet_balance}")

        # Fund new addresses from faucet address
        LOGGER.debug("Funding created addresses.")
        to_fund = [d["payment"] for d in new_addrs_data.values()]
        amount_per_address = faucet_balance // len(self.test_addr_records)
        faucet.fund_from_faucet(
            *to_fund,
            cluster_obj=cluster_obj,
            faucet_data=faucet_addrs_data[self.test_addr_records[1]],
            amount=amount_per_address,
            destination_dir=destination_dir,
            force=True,
        )

        addrs_data = {**new_addrs_data, **faucet_addrs_data}
        return addrs_data


@functools.cache
def get_cluster_type() -> ClusterType:
    """Return instance of the cluster type indicated by configuration."""
    if configuration.BOOTSTRAP_DIR:
        return TestnetCluster()
    return LocalCluster()


def get_cardano_node_socket_path(*, instance_num: int, socket_file_name: str = "") -> pl.Path:
    """Return path to socket file in the given cluster instance."""
    socket_file_name = socket_file_name or configuration.STARTUP_CARDANO_NODE_SOCKET_PATH.name
    state_cluster_dirname = f"{STATE_CLUSTER}{instance_num}"
    state_cluster = (
        configuration.STARTUP_CARDANO_NODE_SOCKET_PATH.parent.parent / state_cluster_dirname
    )
    new_socket_path = state_cluster / socket_file_name
    return new_socket_path


def set_cluster_env(*, instance_num: int, socket_file_name: str = "") -> None:
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
    socket_path = pl.Path(os.environ["CARDANO_NODE_SOCKET_PATH"])
    instance_num = int(socket_path.parent.name.replace(STATE_CLUSTER, "") or 0)
    return instance_num


def get_cluster_env() -> ClusterEnv:
    """Get cardano cluster environment."""
    socket_path = pl.Path(os.environ["CARDANO_NODE_SOCKET_PATH"])
    state_dir = socket_path.parent
    work_dir = state_dir.parent
    instance_num = int(state_dir.name.replace(STATE_CLUSTER, "") or 0)

    cluster_env = ClusterEnv(
        socket_path=socket_path,
        state_dir=state_dir,
        work_dir=work_dir,
        instance_num=instance_num,
        cluster_era=configuration.CLUSTER_ERA,
        command_era=configuration.COMMAND_ERA,
    )
    return cluster_env


def run_supervisorctl(
    args: list[str], *, instance_num: int | None = None, ignore_fail: bool = False
) -> bytes:
    """Run `supervisorctl` command."""
    if instance_num is None:
        state_dir = get_cluster_env().state_dir
    else:
        socket_path = pl.Path(os.environ["CARDANO_NODE_SOCKET_PATH"])
        state_cluster_dirname = f"{STATE_CLUSTER}{instance_num}"
        state_dir = socket_path.parent.parent / state_cluster_dirname
    script = state_dir / "supervisorctl"
    return helpers.run_command([str(script), *args], ignore_fail=ignore_fail)


def reload_supervisor_config(
    *, instance_num: int | None = None, delay: int = configuration.TX_SUBMISSION_DELAY
) -> None:
    """Reload supervisor configuration."""
    LOGGER.info("Reloading supervisor configuration.")

    try:
        run_supervisorctl(args=["update"], instance_num=instance_num)
    except Exception as exc:
        msg = "Failed to reload configuration."
        raise Exception(msg) from exc

    # Wait for potential nodes restart
    if delay > 0:
        time.sleep(delay)


def start_cluster(cmd: str, args: list[str]) -> clusterlib.ClusterLib:
    """Start cluster."""
    args_str = " ".join(args)
    args_str = f" {args_str}" if args_str else ""
    LOGGER.info(f"Starting cluster with `{cmd}{args_str}`.")
    helpers.run_command(f"{cmd}{args_str}", workdir=get_cluster_env().work_dir)
    LOGGER.info("Cluster started.")
    return get_cluster_type().get_cluster_obj()


def restart_all_nodes(
    *, instance_num: int | None = None, delay: int = configuration.TX_SUBMISSION_DELAY
) -> None:
    """Restart all Cardano nodes of the running cluster."""
    LOGGER.info("Restarting all cluster nodes.")

    try:
        run_supervisorctl(args=["restart", "nodes:"], instance_num=instance_num)
    except Exception as exc:
        msg = "Failed to restart cluster nodes."
        raise Exception(msg) from exc

    # Wait for nodes to start
    if delay > 0:
        time.sleep(delay)


def services_action(
    service_names: list[str], *, action: str, instance_num: int | None = None
) -> None:
    """Perform action on services on the running cluster."""
    LOGGER.info(f"Performing '{action}' action on services {service_names}.")

    for service_name in service_names:
        try:
            run_supervisorctl(args=[action, service_name], instance_num=instance_num)
        except Exception as exc:  # noqa: PERF203
            msg = f"Failed to {action} service `{service_name}`"
            raise Exception(msg) from exc


def start_nodes(node_names: list[str], *, instance_num: int | None = None) -> None:
    """Start list of Cardano nodes of the running cluster."""
    service_names = [f"nodes:{n}" for n in node_names]
    services_action(service_names=service_names, action="start", instance_num=instance_num)


def stop_nodes(node_names: list[str], *, instance_num: int | None = None) -> None:
    """Stop list of Cardano nodes of the running cluster."""
    service_names = [f"nodes:{n}" for n in node_names]
    services_action(service_names=service_names, action="stop", instance_num=instance_num)


def restart_nodes(
    node_names: list[str],
    *,
    instance_num: int | None = None,
    delay: int = configuration.TX_SUBMISSION_DELAY,
) -> None:
    """Restart list of Cardano nodes of the running cluster."""
    service_names = [f"nodes:{n}" for n in node_names]
    services_action(service_names=service_names, action="restart", instance_num=instance_num)

    # Wait for nodes to start
    if delay > 0:
        time.sleep(delay)


def services_status(
    service_names: list[str] | None = None, *, instance_num: int | None = None
) -> list[ServiceStatus]:
    """Return status info for list of services running on the running cluster (all by default)."""
    service_names_arg = service_names if service_names else ["all"]

    try:
        status_out = (
            run_supervisorctl(
                args=["status", *service_names_arg], instance_num=instance_num, ignore_fail=True
            )
            .decode()
            .strip()
            .split("\n")
        )
    except Exception as exc:
        msg = "Failed to get services status."
        raise Exception(msg) from exc

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


def load_pools_data(*, cluster_obj: clusterlib.ClusterLib) -> dict:
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


def setup_test_addrs(
    *, cluster_obj: clusterlib.ClusterLib, destination_dir: clusterlib.FileType = "."
) -> pl.Path:
    """Set addresses and their keys up for usage in tests."""
    destination_dir = pl.Path(destination_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    cluster_env = get_cluster_env()

    LOGGER.debug("Creating addresses and keys for tests.")
    addrs_data = get_cluster_type().create_addrs_data(
        cluster_obj=cluster_obj, destination_dir=destination_dir
    )

    pools_data = load_pools_data(cluster_obj=cluster_obj)
    data_file = pl.Path(cluster_env.state_dir) / ADDRS_DATA
    with open(data_file, "wb") as out_data:
        pickle.dump({**addrs_data, **pools_data}, out_data)

    return data_file


def load_addrs_data() -> dict:
    """Load data about addresses and their keys for usage in tests."""
    data_file = pl.Path(get_cluster_env().state_dir) / ADDRS_DATA
    with open(data_file, "rb") as in_data:
        return tp.cast(dict, pickle.load(in_data))
