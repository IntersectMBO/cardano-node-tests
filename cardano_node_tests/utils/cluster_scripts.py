"""Functionality for cluster scripts (starting and stopping clusters).

* copying scripts and their configuration, so it can be altered by tests
* setup of scripts and their configuration for starting of multiple cluster instances
"""

import contextlib
import dataclasses
import itertools
import pathlib as pl
import random
import shutil
import socket
import typing as tp

import cardonnay_scripts
from cardonnay import local_scripts as cardonnay_local

import cardano_node_tests.utils.types as ttypes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers

LOCAL_HOSTNAME = "node.local.gd"
STOP_SCRIPT = "stop-cluster"

COMMON_DIR = pl.Path(str(cardonnay_scripts.SCRIPTS_ROOT)) / "common"
LOCAL_SCRIPTS_DIR = pl.Path(__file__).parent.parent / "cluster_scripts"


@dataclasses.dataclass(frozen=True, order=True)
class StartupFiles:
    start_script: pl.Path
    genesis_spec: pl.Path
    config_glob: str


def get_testnet_dirs(base: pl.Path) -> set[str]:
    return {
        d.name
        for d in base.iterdir()
        if d.is_dir() and "egg-info" not in d.name and d.name != "common"
    }


def get_testnet_variants() -> list[str]:
    """Get list of testnet variants."""
    local = get_testnet_dirs(LOCAL_SCRIPTS_DIR)
    external = get_testnet_dirs(pl.Path(str(cardonnay_scripts.SCRIPTS_ROOT)))
    return sorted(local | external)


def get_testnet_variant_scriptdir(testnet_variant: str) -> pl.Path | None:
    """Get path to testnet variant scripts directory."""
    if testnet_variant in get_testnet_dirs(LOCAL_SCRIPTS_DIR):
        return LOCAL_SCRIPTS_DIR / testnet_variant

    cscripts_root = pl.Path(str(cardonnay_scripts.SCRIPTS_ROOT))
    if testnet_variant in get_testnet_dirs(cscripts_root):
        return cscripts_root / testnet_variant

    return None


def get_testnet_variant_scriptdir2(testnet_variant: str) -> pl.Path:
    """Get path to testnet variant scripts directory.

    Fails if the directory is not found.
    """
    scriptdir = get_testnet_variant_scriptdir(testnet_variant=testnet_variant)
    if not scriptdir:
        err = f"Testnet variant '{testnet_variant}' scripts directory not found."
        raise RuntimeError(err)

    return scriptdir


class CustomCardonnayScripts(cardonnay_local.LocalScripts):
    """Scripts for starting local cluster.

    Re-definition of Cardonnay Local Scripts that allows randomly selected localhost addresses.
    """

    _has_dns_rebinding_protection: tp.ClassVar[bool | None] = None

    @classmethod
    def _check_dns_rebinding_protection(cls) -> bool:
        """Check for DNS rebinding protection.

        The DNS rebinding protection may result in failure to resolve local and private
        IP addresses.
        """
        if cls._has_dns_rebinding_protection is not None:
            return cls._has_dns_rebinding_protection

        addr = ""
        with contextlib.suppress(socket.gaierror):
            addr = socket.gethostbyname(LOCAL_HOSTNAME)

        cls._has_dns_rebinding_protection = addr != "127.0.0.1"
        return cls._has_dns_rebinding_protection

    def _get_rand_addr(self) -> str:
        """Return randomly selected localhost address."""
        localhost_addrs = ["127.0.0.1", LOCAL_HOSTNAME]
        return random.choice(localhost_addrs)

    def _preselect_addr(self, instance_num: int) -> str:
        """Pre-select localhost address.

        When empty string is selected, a randomly selected form of localhost address will be used
        for each peer entry.

        The goal is to have some topology files where all peers use only IP addresses, some where
        all peers use only hostnames and some where peers use both IP addresses and hostnames.
        """
        # If DNS rebinding protection is enabled, we need to use just 127.0.0.1
        if self._check_dns_rebinding_protection():
            return "127.0.0.1"

        if instance_num == 0:
            return ""
        if instance_num == 1 or instance_num % 4 == 0:
            return LOCAL_HOSTNAME
        if instance_num == 2 or instance_num % 5 == 0:
            return "127.0.0.1"

        return ""

    def _gen_legacy_topology(self, addr: str, ports: tp.Iterable[int]) -> dict:
        """Generate legacy topology for given ports."""
        producers = [
            {
                "addr": addr or self._get_rand_addr(),
                "port": port,
                "valency": 1,
            }
            for port in ports
        ]
        topology = {"Producers": producers}
        return topology

    def _gen_p2p_topology(self, addr: str, ports: list[int], fixed_ports: list[int]) -> dict:
        """Generate p2p topology for given ports."""
        # Select fixed ports and several randomly selected ports
        sample_ports = random.sample(ports, 3) if len(ports) > 3 else ports
        selected_ports = set(fixed_ports + sample_ports)
        access_points = [
            {"address": addr or self._get_rand_addr(), "port": port} for port in selected_ports
        ]
        topology = {
            "localRoots": [
                {"accessPoints": access_points, "advertise": False, "valency": len(access_points)},
            ],
            "publicRoots": [],
            "useLedgerAfterSlot": -1,
        }
        return topology

    def _reconfigure_local(self, indir: pl.Path, destdir: pl.Path, instance_num: int) -> None:
        """Reconfigure cluster scripts and config files."""
        instance_ports = self.get_instance_ports(instance_num=instance_num)
        ports_per_node = instance_ports.pool1 - instance_ports.bft1
        addr = self._preselect_addr(instance_num=instance_num)

        # The `common` dir should be copied only if it was not copied already.
        common_dir = COMMON_DIR
        if (indir / "common.sh").exists():
            common_dir = pl.Path("/nonexistent")

        # Reconfigure cluster instance files
        for infile in itertools.chain(common_dir.glob("*"), indir.glob("*")):
            fname = infile.name

            # Skip template files
            if fname.startswith("template-"):
                continue

            outfile = destdir / fname
            dest_content = self._replace_instance_files(
                infile=infile,
                instance_ports=instance_ports,
                instance_num=instance_num,
                ports_per_node=ports_per_node,
            )
            outfile.unlink(missing_ok=True)
            outfile.write_text(f"{dest_content}\n", encoding="utf-8")

            # Make `*.sh` files and files without extension executable
            if "." not in fname or fname.endswith(".sh"):
                outfile.chmod(0o755)

        # Generate config and topology files from templates
        for node_rec in instance_ports.node_ports:
            if node_rec.num != 0:
                supervisor_script = destdir / f"cardano-node-pool{node_rec.num}"
                supervisor_script_content = self._replace_node_template(
                    template_file=indir / "template-cardano-node-pool",
                    node_rec=node_rec,
                    instance_num=instance_num,
                )
                supervisor_script.unlink(missing_ok=True)
                supervisor_script.write_text(f"{supervisor_script_content}\n", encoding="utf-8")
                supervisor_script.chmod(0o755)

            node_name = "bft1" if node_rec.num == 0 else f"pool{node_rec.num}"
            node_config = destdir / f"config-{node_name}.json"
            node_config_content = self._replace_node_template(
                template_file=indir / "template-config.json",
                node_rec=node_rec,
                instance_num=instance_num,
            )
            node_config.unlink(missing_ok=True)
            node_config.write_text(f"{node_config_content}\n", encoding="utf-8")

        self._gen_topology_files(destdir=destdir, addr=addr, nodes=instance_ports.node_ports)

        supervisor_conf_file = destdir / "supervisor.conf"
        supervisor_conf_content = self._gen_supervisor_conf(
            instance_num=instance_num, instance_ports=instance_ports
        )
        supervisor_conf_file.unlink(missing_ok=True)
        supervisor_conf_file.write_text(f"{supervisor_conf_content}\n", encoding="utf-8")


class ScriptsTypes:
    """Generic cluster scripts."""

    LOCAL: tp.Final[str] = "local"
    TESTNET: tp.Final[str] = "testnet"

    def __init__(self) -> None:
        self.type = "unknown"

    def get_instance_ports(self, instance_num: int) -> cardonnay_local.InstancePorts:
        """Return ports mapping for given cluster instance."""
        msg = f"Not implemented for cluster instance type '{self.type}'."
        raise NotImplementedError(msg)

    def copy_scripts_files(self, destdir: ttypes.FileType) -> StartupFiles:
        """Make copy of cluster scripts files.

        Testnet files and scripts can be copied and modified by tests before using them.
        E.g. we might want to change KES period, pool cost, etc.
        """
        msg = f"Not implemented for cluster instance type '{self.type}'."
        raise NotImplementedError(msg)

    def prepare_scripts_files(
        self,
        destdir: ttypes.FileType,
        instance_num: int,
        start_script: ttypes.FileType = "",
        stop_script: ttypes.FileType = "",
    ) -> cardonnay_local.InstanceFiles:
        """Prepare scripts files for starting and stopping cluster instance."""
        msg = f"Not implemented for cluster instance type '{self.type}'."
        raise NotImplementedError(msg)

    def gen_split_topology_files(
        self, destdir: ttypes.FileType, instance_num: int, offset: int = 0
    ) -> None:
        """Generate topology files for split network."""
        msg = f"Not implemented for cluster instance type '{self.type}'."
        raise NotImplementedError(msg)


class LocalScripts(ScriptsTypes):
    """Scripts for starting local cluster."""

    def __init__(self, num_pools: int = -1) -> None:
        super().__init__()
        self.type = ScriptsTypes.LOCAL
        self.num_pools = num_pools
        if num_pools == -1:
            self.num_pools = configuration.NUM_POOLS

        self.scripts_dir = get_testnet_variant_scriptdir2(
            testnet_variant=configuration.TESTNET_VARIANT
        )
        self.custom_cardonnay_scripts = CustomCardonnayScripts(
            num_pools=self.num_pools,
            scripts_dir=self.scripts_dir,
            ports_base=configuration.PORTS_BASE,
        )

    def get_instance_ports(self, instance_num: int) -> cardonnay_local.InstancePorts:
        """Return ports mapping for given cluster instance."""
        return self.custom_cardonnay_scripts.get_instance_ports(instance_num=instance_num)

    def copy_scripts_files(self, destdir: ttypes.FileType) -> StartupFiles:
        """Make copy of cluster scripts files located in this repository."""
        destdir = pl.Path(destdir).expanduser().resolve()

        shutil.copytree(
            COMMON_DIR, destdir, symlinks=True, ignore_dangling_symlinks=True, dirs_exist_ok=True
        )
        shutil.copytree(
            self.scripts_dir,
            destdir,
            symlinks=True,
            ignore_dangling_symlinks=True,
            dirs_exist_ok=True,
        )

        start_script = destdir / "start-cluster"
        config_glob = "config-*.json"
        genesis_spec_json = destdir / "genesis.spec.json"
        if not (start_script.exists() and genesis_spec_json.exists()):
            err = (
                f"Start script '{start_script}' or genesis spec file '{genesis_spec_json}' "
                "not found in the copied cluster scripts directory."
            )
            raise FileNotFoundError(err)

        return StartupFiles(
            start_script=start_script, genesis_spec=genesis_spec_json, config_glob=config_glob
        )

    def prepare_scripts_files(
        self,
        destdir: ttypes.FileType,
        instance_num: int,
        start_script: ttypes.FileType = "",
        stop_script: ttypes.FileType = "",
    ) -> cardonnay_local.InstanceFiles:
        """Prepare scripts files for starting and stopping cluster instance."""
        return self.custom_cardonnay_scripts.prepare_scripts_files(
            destdir=pl.Path(destdir),
            instance_num=instance_num,
            start_script=start_script,
            stop_script=stop_script,
        )

    def gen_split_topology_files(
        self, destdir: ttypes.FileType, instance_num: int, offset: int = 0
    ) -> None:
        """Generate topology files for split network."""
        if self.num_pools < 4:
            msg = (
                "There must be at least 4 pools for split topology "
                f"(current number: {self.num_pools})"
            )
            raise ValueError(msg)

        destdir = pl.Path(destdir).expanduser().resolve()
        instance_ports = self.get_instance_ports(instance_num=instance_num)
        addr = self.custom_cardonnay_scripts._preselect_addr(instance_num=instance_num)
        nodes = instance_ports.node_ports

        all_nodes = [p.node for p in nodes]

        # Split nodes index (+1 for bft node, which is not block producer)
        split_idx = len(all_nodes) // 2 + 1 + offset
        first_half = all_nodes[:split_idx]
        second_half = all_nodes[split_idx:]

        if min(len(first_half), len(second_half)) < 2:
            msg = (
                "There must be at least 2 nodes on each side of split "
                f"(number of pools: {self.num_pools})"
            )
            raise ValueError(msg)

        for node_rec in nodes:
            ports_group = first_half if node_rec.node in first_half else second_half
            all_except = ports_group[:]
            all_except.remove(node_rec.node)
            node_name = "bft1" if node_rec.num == 0 else f"pool{node_rec.num}"

            # Legacy topology
            topology = self.custom_cardonnay_scripts._gen_legacy_topology(
                addr=addr, ports=all_except
            )
            helpers.write_json(
                out_file=destdir / f"split-topology-{node_name}.json", content=topology
            )

            # P2P topology
            fixed_ports = all_except[:4]
            p2p_topology = self.custom_cardonnay_scripts._gen_p2p_topology(
                addr=addr, ports=all_except, fixed_ports=fixed_ports
            )
            helpers.write_json(
                out_file=destdir / f"p2p-split-topology-{node_name}.json", content=p2p_topology
            )


class TestnetScripts(ScriptsTypes):
    """Scripts for starting a node on testnet."""

    TESTNET_GLOBS: tp.ClassVar[tuple[str, ...]] = (
        "config*.json",
        "genesis-*.json",
        "topology-*.json",
        "dbsync-config.*",
        "submit-api-config.*",
    )
    BOOTSTRAP_CONF: tp.Final[str] = "testnet_conf"

    def __init__(self) -> None:
        super().__init__()
        self.type = ScriptsTypes.TESTNET
        self.scripts_dir = get_testnet_variant_scriptdir2(
            testnet_variant=configuration.TESTNET_VARIANT
        )

    def get_instance_ports(self, instance_num: int) -> cardonnay_local.InstancePorts:
        """Return ports mapping for given cluster instance."""
        ports_per_instance = 10
        offset = instance_num * ports_per_instance
        base = configuration.PORTS_BASE + offset
        last_port = base + ports_per_instance - 1

        relay1_ports = cardonnay_local.NodePorts(
            num=0,
            node=base,
            ekg=base + 1,
            prometheus=base + 2,
        )

        ports = cardonnay_local.InstancePorts(
            base=base,
            webserver=last_port,
            metrics_submit_api=last_port - 1,
            submit_api=last_port - 2,
            smash=last_port - 3,
            supervisor=12001 + instance_num,
            relay1=relay1_ports.node,
            ekg_relay1=relay1_ports.ekg,
            prometheus_relay1=relay1_ports.prometheus,
            bft1=0,
            ekg_bft1=0,
            prometheus_bft1=0,
            pool1=0,
            ekg_pool1=0,
            prometheus_pool1=0,
            pool2=0,
            ekg_pool2=0,
            prometheus_pool2=0,
            pool3=0,
            ekg_pool3=0,
            prometheus_pool3=0,
            node_ports=(relay1_ports,),
        )
        return ports

    def copy_scripts_files(self, destdir: ttypes.FileType) -> StartupFiles:
        """Make copy of cluster scripts files located in this repository."""
        destdir = pl.Path(destdir).expanduser().resolve()

        shutil.copytree(
            COMMON_DIR, destdir, symlinks=True, ignore_dangling_symlinks=True, dirs_exist_ok=True
        )
        shutil.copytree(
            self.scripts_dir,
            destdir,
            symlinks=True,
            ignore_dangling_symlinks=True,
            dirs_exist_ok=True,
        )

        start_script = destdir / "start-cluster"
        if not start_script.exists():
            err = (
                f"Start script '{start_script}' not found in the copied cluster scripts directory."
            )
            raise RuntimeError(err)

        bootstrap_conf_dir = self._get_bootstrap_conf_dir(bootstrap_dir=destdir)
        destdir_bootstrap = destdir / self.BOOTSTRAP_CONF
        destdir_bootstrap.mkdir()
        infiles = [bootstrap_conf_dir.glob(g) for g in self.TESTNET_GLOBS]
        for infile in itertools.chain(*infiles):
            shutil.copy(infile, destdir_bootstrap)

        config_glob = f"{self.BOOTSTRAP_CONF}/config-*.json"
        # TODO: it's not really a spec file in case of a testnet
        genesis_json = destdir / self.BOOTSTRAP_CONF / "genesis-shelley.json"

        return StartupFiles(
            start_script=start_script, genesis_spec=genesis_json, config_glob=config_glob
        )

    def _reconfigure_testnet(
        self, indir: pl.Path, destdir: pl.Path, instance_num: int, globs: list[str]
    ) -> None:
        """Reconfigure cluster scripts and config files."""
        instance_ports = self.get_instance_ports(instance_num=instance_num)
        infiles = [indir.glob(g) for g in globs]

        # The `common` dir should be copied only if it was not copied already.
        common_dir = COMMON_DIR
        if (indir / "common.sh").exists():
            common_dir = pl.Path("/nonexistent")

        for infile in itertools.chain(common_dir.glob("*"), *infiles):
            fname = infile.name
            outfile = destdir / fname

            content = infile.read_text(encoding="utf-8")
            # Replace cluster instance number
            new_content = content.replace("%%INSTANCE_NUM%%", str(instance_num))
            # Replace node port number strings
            new_content = new_content.replace("%%NODE_PORT_RELAY1%%", str(instance_ports.relay1))
            # Reconfigure supervisord port
            new_content = new_content.replace("%%SUPERVISOR_PORT%%", str(instance_ports.supervisor))
            # Reconfigure submit-api port
            new_content = new_content.replace("%%SUBMIT_API_PORT%%", str(instance_ports.submit_api))
            # Reconfigure submit-api metrics port
            new_content = new_content.replace(
                "%%METRICS_SUBMIT_API_PORT%%", str(instance_ports.metrics_submit_api)
            )
            # Reconfigure smash port
            new_content = new_content.replace("%%SMASH_PORT%%", str(instance_ports.smash))
            # Reconfigure EKG metrics port
            new_content = new_content.replace("%%EKG_PORT_RELAY1%%", str(instance_ports.ekg_relay1))
            # Reconfigure prometheus metrics port
            new_content = new_content.replace(
                "%%PROMETHEUS_PORT_RELAY1%%", str(instance_ports.prometheus_relay1)
            )
            # Reconfigure webserver port
            new_content = new_content.replace("%%WEBSERVER_PORT%%", str(instance_ports.webserver))

            outfile.unlink(missing_ok=True)
            outfile.write_text(f"{new_content}\n", encoding="utf-8")

            # Make `*.sh` files and files without extension executable
            if "." not in fname or fname.endswith(".sh"):
                outfile.chmod(0o755)

    def _reconfigure_submit_api_config(self, infile: pl.Path, outfile: pl.Path) -> None:
        """Reconfigure submit-api config file."""
        with open(infile, encoding="utf-8") as in_fp:
            content = in_fp.readlines()

        # Delete the line that contains "PrometheusPort"
        new_content = [line for line in content if "PrometheusPort" not in line]

        with open(outfile, "w", encoding="utf-8") as out_fp:
            out_fp.write("".join(new_content))

    def _reconfigure_bootstrap(self, indir: pl.Path, destdir: pl.Path, globs: list[str]) -> None:
        """Copy and reconfigure config files from bootstrap dir."""
        infiles = [indir.glob(g) for g in globs]
        for infile in itertools.chain(*infiles):
            fname = infile.name
            outfile = destdir / fname

            if "submit-api-config" in fname:
                self._reconfigure_submit_api_config(infile=infile, outfile=outfile)
                continue

            outfile.unlink(missing_ok=True)
            shutil.copy(infile, outfile)

    def _is_bootstrap_conf_dir(self, bootstrap_dir: pl.Path) -> bool:
        return all(list(bootstrap_dir.glob(g)) for g in self.TESTNET_GLOBS)

    def _get_bootstrap_conf_dir(self, bootstrap_dir: pl.Path) -> pl.Path:
        bootstrap_conf_dir = bootstrap_dir / self.BOOTSTRAP_CONF
        if not self._is_bootstrap_conf_dir(bootstrap_conf_dir):
            if not configuration.BOOTSTRAP_DIR:
                msg = "The 'BOOTSTRAP_DIR' env variable is not set."
                raise RuntimeError(msg)
            bootstrap_conf_dir = pl.Path(configuration.BOOTSTRAP_DIR).expanduser().resolve()
        if not self._is_bootstrap_conf_dir(bootstrap_conf_dir):
            msg = "The 'BOOTSTRAP_DIR' doesn't contain all the needed files."
            raise RuntimeError(msg)
        return bootstrap_conf_dir

    def prepare_scripts_files(
        self,
        destdir: ttypes.FileType,
        instance_num: int,
        start_script: ttypes.FileType = "",
        stop_script: ttypes.FileType = "",
    ) -> cardonnay_local.InstanceFiles:
        """Prepare scripts files for starting and stopping cluster instance.

        There is just one cluster instance running for a given testnet. We keep the `instance_num`
        support anyway, as this makes it possible to run multiple testnets on the same machine.
        """
        destdir = pl.Path(destdir).expanduser().resolve()
        destdir_bootstrap = destdir / self.BOOTSTRAP_CONF
        destdir_bootstrap.mkdir(exist_ok=True)

        _start_script = start_script or self.scripts_dir / "start-cluster"
        _stop_script = stop_script or self.scripts_dir / "stop-cluster"

        start_script = pl.Path(_start_script).expanduser().resolve()
        stop_script = pl.Path(_stop_script).expanduser().resolve()

        bootstrap_conf_dir = self._get_bootstrap_conf_dir(bootstrap_dir=start_script.parent)

        self._reconfigure_testnet(
            indir=start_script.parent, destdir=destdir, instance_num=instance_num, globs=["*"]
        )
        new_start_script = destdir / start_script.name
        new_stop_script = destdir / stop_script.name

        self._reconfigure_bootstrap(
            indir=bootstrap_conf_dir,
            destdir=destdir_bootstrap,
            globs=list(self.TESTNET_GLOBS),
        )

        return cardonnay_local.InstanceFiles(
            start_script=new_start_script,
            stop_script=new_stop_script,
            start_script_args=[configuration.BOOTSTRAP_DIR],
            dir=destdir,
        )
