"""Functionality for cluster setup and interaction with cluster nodes."""

import dataclasses
import datetime
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
# Prefix of the supervisor names of cardano-node services (e.g. "nodes:pool1")
NODES_SERVICE_PREFIX = "nodes:"


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


class ClusterKind(enum.StrEnum):
    LOCAL = "local"
    TESTNET = "testnet"


TEST_ADDR_RECORDS: tp.Final[tuple[str, ...]] = (
    "user1",
    "user2",
    "user3",
    "user4",
    "user5",
)

# The message is a module-level constant so that each abstract protocol method body stays
# a single `raise` statement and type checkers keep treating the methods as abstract (see
# the same pattern in `cluster_scripts`).
_NOT_IMPLEMENTED_MSG: tp.Final[str] = "Not implemented for this cluster type."


class ClusterType(tp.Protocol):
    """Protocol for cluster types."""

    NODES: tp.ClassVar[frozenset[str]]

    type: ClusterKind
    cluster_scripts: "cluster_scripts.ScriptsTypes"

    @property
    def is_local(self) -> bool:
        """Check if the cluster runs on a local testnet."""
        return self.type is ClusterKind.LOCAL

    @property
    def is_testnet(self) -> bool:
        """Check if the cluster runs on a long-running public network (preview, mainnet, etc.)."""
        return self.type is ClusterKind.TESTNET

    @property
    def testnet_type(self) -> str:
        """Return testnet type (preview, preprod, etc.).

        Returns an empty string on local cluster and "unknown" when the testnet is not
        recognized.
        """
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    @property
    def uses_shortcut(self) -> bool:
        """Check if cluster uses shortcut to go from Byron to last supported era."""
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def get_cluster_obj(self, *, command_era: str = "") -> clusterlib.ClusterLib:
        """Return instance of `ClusterLib` (cluster_obj)."""
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def create_addrs_data(
        self, *, cluster_obj: clusterlib.ClusterLib, destination_dir: clusterlib.FileType = "."
    ) -> dict[str, dict[str, tp.Any]]:
        """Create addresses and their keys for usage in tests."""
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)


class LocalCluster(ClusterType):
    """Local cluster type (full cardano mode)."""

    NODES: tp.ClassVar[frozenset[str]] = frozenset(
        {"bft1", *(f"pool{i}" for i in range(1, configuration.NUM_POOLS + 1))}
    )

    def __init__(self) -> None:
        self.type = ClusterKind.LOCAL
        self.cluster_scripts = cluster_scripts.LocalScripts()

    @property
    def testnet_type(self) -> str:
        """Return empty string, local cluster is not a testnet."""
        return ""

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
        for addr_name in TEST_ADDR_RECORDS:
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
        amount_per_address = 100_000_000_000_000 // len(TEST_ADDR_RECORDS)
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

    NODES: tp.ClassVar[frozenset[str]] = frozenset({"relay1"})

    def __init__(self) -> None:
        self.type = ClusterKind.TESTNET
        self.cluster_scripts = cluster_scripts.TestnetScripts()

        # Cached values
        self._testnet_type = ""

    @property
    def uses_shortcut(self) -> bool:
        """Check if cluster uses shortcut to go from Byron to last supported era."""
        return False

    @property
    def testnet_type(self) -> str:
        """Return testnet type (preview, preprod, etc.)."""
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
            TEST_ADDR_RECORDS[1]: {"payment": faucet_rec}
        }

        # Create new addresses
        new_addrs_data: dict[str, dict[str, tp.Any]] = {}
        for addr_name in TEST_ADDR_RECORDS[1:]:
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
        amount_per_address = faucet_balance // len(TEST_ADDR_RECORDS)
        faucet.fund_from_faucet(
            *to_fund,
            cluster_obj=cluster_obj,
            faucet_data=faucet_addrs_data[TEST_ADDR_RECORDS[1]],
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
    # VERSIONS executes cardano-node and cardano-cli, and the binaries may not be available.
    # Importing VERSIONS here allows to delay the execution until it's really needed, and avoid
    # potential issues with missing binaries when the module is imported.
    from cardano_node_tests.utils.versions import VERSIONS  # noqa: PLC0415

    socket_path = pl.Path(os.environ["CARDANO_NODE_SOCKET_PATH"])
    state_dir = socket_path.parent
    work_dir = state_dir.parent
    instance_num = int(state_dir.name.replace(STATE_CLUSTER, "") or 0)

    cluster_env = ClusterEnv(
        socket_path=socket_path,
        state_dir=state_dir,
        work_dir=work_dir,
        instance_num=instance_num,
        cluster_era=VERSIONS.cluster_era_name,
        command_era=VERSIONS.command_era_name,
    )
    return cluster_env


def get_instance_state_dir(*, instance_num: int | None = None) -> pl.Path:
    """Return the state dir of the cluster instance (the current one by default)."""
    if instance_num is None:
        return get_cluster_env().state_dir
    socket_path = pl.Path(os.environ["CARDANO_NODE_SOCKET_PATH"])
    return socket_path.parent.parent / f"{STATE_CLUSTER}{instance_num}"


def run_supervisorctl(
    args: list[str], *, instance_num: int | None = None, ignore_fail: bool = False
) -> bytes:
    """Run `supervisorctl` command."""
    script = get_instance_state_dir(instance_num=instance_num) / "supervisorctl_local"
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
    helpers.run_command([cmd, *args], workdir=get_cluster_env().work_dir, merge_stderr=True)
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
        except Exception as exc:
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
    service_names_arg = service_names or ["all"]

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


def _get_uptime_sec(service_status: ServiceStatus) -> float | None:
    """Return the uptime of a running service in seconds, None when it is not known.

    Supervisor reports the uptime as "H:MM:SS", or "N day(s), H:MM:SS" - the day count
    then ends up in `uptime` and the rest in `message`.
    """
    uptime, message = service_status.uptime or "", service_status.message
    try:
        if message.startswith("day"):
            days, hms = int(uptime), message.split()[-1]
        else:
            days, hms = 0, uptime
        hours, minutes, seconds = (int(p) for p in hms.split(":"))
    except ValueError:
        return None
    return float(((days * 24 + hours) * 60 + minutes) * 60 + seconds)


@functools.lru_cache(maxsize=configuration.CLUSTERS_COUNT)
def _read_stall_params(genesis_file: pl.Path, _mtime_ns: int) -> tuple[float, float]:
    """Return the forecast horizon in seconds and the system start as a Unix timestamp.

    The file mtime is part of the cache key, so a respun instance with a new genesis
    is read again.
    """
    with open(genesis_file, encoding="utf-8") as in_json:
        genesis = json.load(in_json)
    horizon_sec = (
        3
        * int(genesis["securityParam"])
        / float(genesis["activeSlotsCoeff"])
        * float(genesis["slotLength"])
    )
    system_start = datetime.datetime.fromisoformat(genesis["systemStart"]).timestamp()
    return horizon_sec, system_start


def _get_last_write(volatile_dir: pl.Path) -> float | None:
    """Return the time of the newest write to the volatile DB.

    The node creates the volatile DB dir right when it opens its DB, so a missing dir
    means the DB is elsewhere (e.g. `--volatile-database-path`), not that it is empty.

    Returns:
        float | None: The time as a Unix timestamp, None when the DB cannot be found
            or read.
    """
    try:
        # The dir mtime changes when a new blocks file is created or an old one is removed
        last_write = volatile_dir.stat().st_mtime
        files = list(volatile_dir.iterdir())
    except FileNotFoundError:
        LOGGER.debug(f"Cannot check node for stall, '{volatile_dir}' doesn't exist.")
        return None
    except OSError as exc:
        LOGGER.warning(f"Cannot check node for stall, failed to read '{volatile_dir}': {exc}")
        return None

    for f in files:
        try:
            last_write = max(last_write, f.stat().st_mtime)
        except FileNotFoundError:
            # Removed by the volatile DB garbage collection meanwhile
            continue
        except OSError as exc:
            LOGGER.warning(f"Cannot check node for stall, failed to read '{f}': {exc}")
            return None

    return last_write


def get_stalled_nodes(
    statuses: tp.Iterable[ServiceStatus], *, state_dir: pl.Path, now: float | None = None
) -> list[str]:
    """Return names of nodes whose chain stopped growing for longer than the forecast horizon.

    Once the tip of a node is older than the forecast horizon (`3k/f` slots), the node has
    no ledger view for the current slot and cannot forge anymore. When that happens to all
    the nodes, the chain is halted for good. A single node in that state is usually stuck on
    a fork deeper than `k`, which it cannot switch away from.

    The time of the last block is the newest write to the node's volatile DB, which covers
    both forged and received blocks, including the blocks downloaded during a sync. A node
    is measured at the earliest from the system start, and from the start of its process,
    so a node that was restarted after a long stop has time to replay its ledger and catch
    up.

    Only running nodes are checked, a node that was stopped on purpose is not stalled.

    Args:
        statuses: Statuses of the cluster services (see `services_status`).
        state_dir: The state dir of the cluster instance.
        now: Current time as a Unix timestamp (optional, the current time by default).

    Returns:
        list[str]: Names of the stalled nodes. Empty when the genesis file can't be read. A node
            whose volatile DB can't be found at `<state_dir>/db-<node>/volatile`, or can't be
            read, is not reported.
    """
    genesis_file = state_dir / "shelley" / "genesis.json"
    try:
        horizon_sec, system_start = _read_stall_params(
            genesis_file, genesis_file.stat().st_mtime_ns
        )
    except FileNotFoundError:
        # Not a local cluster instance, or it was not started yet
        return []
    except (OSError, ValueError, KeyError, ZeroDivisionError) as exc:
        LOGGER.warning(f"Cannot check nodes for stall, failed to read '{genesis_file}': {exc}")
        return []

    now = time.time() if now is None else now

    stalled = []
    for service_status in statuses:
        if service_status.status != "RUNNING" or not service_status.name.startswith(
            NODES_SERVICE_PREFIX
        ):
            continue
        node_name = service_status.name.removeprefix(NODES_SERVICE_PREFIX)

        uptime_sec = _get_uptime_sec(service_status)
        started = now - uptime_sec if uptime_sec is not None else 0.0
        last_write = _get_last_write(state_dir / f"db-{node_name}" / "volatile")
        # The time of the last block is not known, don't guess
        if last_write is None:
            continue

        if now - max(last_write, started, system_start) > horizon_sec:
            stalled.append(node_name)

    return stalled


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
            # BLS keys are created only in the Dijkstra+ eras
            "bls_key_pair": clusterlib.KeyPair(
                vkey_file=pool_data_dir / "bls.vkey",
                skey_file=pool_data_dir / "bls.skey",
            )
            if (pool_data_dir / "bls.skey").exists()
            else None,
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
