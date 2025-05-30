"""Functionality for cluster scripts (starting and stopping clusters).

* copying scripts and their configuration, so it can be atered by tests
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

import cardano_node_tests.utils.types as ttypes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers

LOCAL_HOSTNAME = "node.local.gd"
STOP_SCRIPT = "supervisord_stop"


@dataclasses.dataclass(frozen=True, order=True)
class InstanceFiles:
    start_script: pl.Path
    stop_script: pl.Path
    start_script_args: list[str]
    dir: pl.Path


@dataclasses.dataclass(frozen=True, order=True)
class StartupFiles:
    start_script: pl.Path
    genesis_spec: pl.Path
    config_glob: str


@dataclasses.dataclass(frozen=True, order=True)
class NodePorts:
    num: int
    node: int
    ekg: int
    prometheus: int


@dataclasses.dataclass(frozen=True, order=True)
class InstancePorts:
    base: int
    webserver: int
    metrics_submit_api: int
    submit_api: int
    smash: int
    supervisor: int
    relay1: int
    ekg_relay1: int
    prometheus_relay1: int
    bft1: int
    ekg_bft1: int
    prometheus_bft1: int
    pool1: int
    ekg_pool1: int
    prometheus_pool1: int
    pool2: int
    ekg_pool2: int
    prometheus_pool2: int
    pool3: int
    ekg_pool3: int
    prometheus_pool3: int
    node_ports: tuple[NodePorts, ...]


class ScriptsTypes:
    """Generic cluster scripts."""

    LOCAL: tp.Final[str] = "local"
    TESTNET: tp.Final[str] = "testnet"

    def __init__(self) -> None:
        self.type = "unknown"

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        msg = f"Not implemented for cluster instance type '{self.type}'."
        raise NotImplementedError(msg)

    def copy_scripts_files(self, destdir: ttypes.FileType) -> StartupFiles:
        """Make copy of cluster scripts files."""
        msg = f"Not implemented for cluster instance type '{self.type}'."
        raise NotImplementedError(msg)

    def prepare_scripts_files(
        self,
        destdir: ttypes.FileType,
        instance_num: int,
        start_script: ttypes.FileType = "",
        stop_script: ttypes.FileType = "",
    ) -> InstanceFiles:
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

    _has_dns_rebinding_protection: tp.ClassVar[bool | None] = None

    def __init__(self, num_pools: int = -1) -> None:
        super().__init__()
        self.type = ScriptsTypes.LOCAL
        self.num_pools = num_pools
        if num_pools == -1:
            self.num_pools = configuration.NUM_POOLS

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

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        # Allocate 100 ports per each 18 pools
        ports_per_instance = ((self.num_pools - 1) // 18 + 1) * 100
        offset = instance_num * ports_per_instance
        base = configuration.PORTS_BASE + offset
        last_port = base + ports_per_instance - 1
        ports_per_node = 5

        def _get_node_ports(num: int) -> NodePorts:
            rec_base = base + (num * ports_per_node)
            return NodePorts(
                num=num,
                node=rec_base,
                ekg=rec_base + 1,
                prometheus=rec_base + 2,
            )

        node_ports = tuple(_get_node_ports(i) for i in range(self.num_pools + 1))  # +1 for BFT node

        ports = InstancePorts(
            base=base,
            webserver=last_port,
            metrics_submit_api=last_port - 1,
            submit_api=last_port - 2,
            smash=last_port - 3,
            supervisor=12001 + instance_num,
            # Relay1
            relay1=0,
            ekg_relay1=0,
            prometheus_relay1=0,
            # Bft1
            bft1=base,
            ekg_bft1=base + 1,
            prometheus_bft1=base + 2,
            # Pool1
            pool1=base + 5,
            ekg_pool1=base + 6,
            prometheus_pool1=base + 7,
            # Pool2
            pool2=base + 10,
            ekg_pool2=base + 11,
            prometheus_pool2=base + 12,
            # Pool3
            pool3=base + 15,
            ekg_pool3=base + 16,
            prometheus_pool3=base + 17,
            # All nodes
            node_ports=node_ports,
        )
        return ports

    def copy_scripts_files(self, destdir: ttypes.FileType) -> StartupFiles:
        """Make copy of cluster scripts files located in this repository."""
        destdir = pl.Path(destdir).expanduser().resolve()
        scripts_dir = configuration.SCRIPTS_DIR
        common_dir = scripts_dir.parent / "common"

        shutil.copytree(
            scripts_dir, destdir, symlinks=True, ignore_dangling_symlinks=True, dirs_exist_ok=True
        )
        shutil.copytree(
            common_dir, destdir, symlinks=True, ignore_dangling_symlinks=True, dirs_exist_ok=True
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

    def _replace_node_template(
        self, template_file: pl.Path, node_rec: NodePorts, instance_num: int
    ) -> str:
        """Replace template variables in given content."""
        content = template_file.read_text()
        new_content = content.replace("%%POOL_NUM%%", str(node_rec.num))
        new_content = new_content.replace("%%INSTANCE_NUM%%", str(instance_num))
        new_content = new_content.replace("%%NODE_PORT%%", str(node_rec.node))
        new_content = new_content.replace("%%EKG_PORT%%", str(node_rec.ekg))
        new_content = new_content.replace("%%PROMETHEUS_PORT%%", str(node_rec.prometheus))
        return new_content

    def _replace_instance_files(
        self, infile: pl.Path, instance_ports: InstancePorts, instance_num: int, ports_per_node: int
    ) -> str:
        """Replace instance variables in given content."""
        content = infile.read_text()
        # Replace cluster instance number
        new_content = content.replace("%%INSTANCE_NUM%%", str(instance_num))
        # Replace number of pools
        new_content = new_content.replace("%%NUM_POOLS%%", str(self.num_pools))
        # Replace node port number strings
        new_content = new_content.replace("%%NODE_PORT_BASE%%", str(instance_ports.base))
        # Replace number of reserved ports per node
        new_content = new_content.replace("%%PORTS_PER_NODE%%", str(ports_per_node))
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
        # Reconfigure webserver port
        new_content = new_content.replace("%%WEBSERVER_PORT%%", str(instance_ports.webserver))
        return new_content

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

    def _gen_supervisor_conf(self, instance_num: int, instance_ports: InstancePorts) -> str:
        """Generate supervisor configuration for given instance."""
        lines = [
            "# [inet_http_server]",
            f"# port=127.0.0.1:{instance_ports.supervisor}",
        ]

        programs = []
        for node_rec in instance_ports.node_ports:
            node_name = "bft1" if node_rec.num == 0 else f"pool{node_rec.num}"

            programs.append(node_name)

            lines.extend(
                [
                    f"\n[program:{node_name}]",
                    f"command=./state-cluster{instance_num}/cardano-node-{node_name}",
                    f"stderr_logfile=./state-cluster{instance_num}/{node_name}.stderr",
                    f"stdout_logfile=./state-cluster{instance_num}/{node_name}.stdout",
                    "startsecs=5",
                ]
            )

        lines.extend(
            [
                "\n[group:nodes]",
                f"programs={','.join(programs)}",
                "\n[program:webserver]",
                f"command=python -m http.server --bind 127.0.0.1 {instance_ports.webserver}",
                f"directory=./state-cluster{instance_num}/webserver",
                "\n[rpcinterface:supervisor]",
                "supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface",
                "\n[supervisorctl]",
                "\n[supervisord]",
                f"logfile=./state-cluster{instance_num}/supervisord.log",
                f"pidfile=./state-cluster{instance_num}/supervisord.pid",
            ]
        )

        return "\n".join(lines)

    def _gen_topology_files(
        self, destdir: pl.Path, addr: str, nodes: tp.Sequence[NodePorts]
    ) -> None:
        """Generate topology files for all nodes."""
        all_nodes = [p.node for p in nodes]

        for node_rec in nodes:
            all_except = all_nodes[:]
            all_except.remove(node_rec.node)
            node_name = "bft1" if node_rec.num == 0 else f"pool{node_rec.num}"

            # Legacy topology

            topology = self._gen_legacy_topology(addr=addr, ports=all_except)
            helpers.write_json(out_file=destdir / f"topology-{node_name}.json", content=topology)

            # P2P topology

            # Bft1 and first three pools
            fixed_ports = all_except[:4]

            p2p_topology = self._gen_p2p_topology(
                addr=addr, ports=all_except, fixed_ports=fixed_ports
            )

            helpers.write_json(
                out_file=destdir / f"p2p-topology-{node_name}.json", content=p2p_topology
            )

    def _reconfigure_local(self, indir: pl.Path, destdir: pl.Path, instance_num: int) -> None:
        """Reconfigure cluster scripts and config files."""
        instance_ports = self.get_instance_ports(instance_num=instance_num)
        ports_per_node = instance_ports.pool1 - instance_ports.bft1
        addr = self._preselect_addr(instance_num=instance_num)

        # The `common` dir is available only if cluster scripts are not taken from custom
        # `indir`, so it needs to be optional.
        common_dir = indir.parent / "common"

        # Reconfigure cluster instance files
        for infile in itertools.chain(indir.glob("*"), common_dir.glob("*")):
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
            outfile.write_text(f"{dest_content}\n")

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
                supervisor_script.write_text(f"{supervisor_script_content}\n")
                supervisor_script.chmod(0o755)

            node_name = "bft1" if node_rec.num == 0 else f"pool{node_rec.num}"
            node_config = destdir / f"config-{node_name}.json"
            node_config_content = self._replace_node_template(
                template_file=indir / "template-config.json",
                node_rec=node_rec,
                instance_num=instance_num,
            )
            node_config.write_text(f"{node_config_content}\n")

        self._gen_topology_files(destdir=destdir, addr=addr, nodes=instance_ports.node_ports)

        supervisor_conf_file = destdir / "supervisor.conf"
        supervisor_conf_content = self._gen_supervisor_conf(
            instance_num=instance_num, instance_ports=instance_ports
        )
        supervisor_conf_file.write_text(f"{supervisor_conf_content}\n")

    def prepare_scripts_files(
        self,
        destdir: ttypes.FileType,
        instance_num: int,
        start_script: ttypes.FileType = "",
        stop_script: ttypes.FileType = "",
    ) -> InstanceFiles:
        """Prepare scripts files for starting and stopping cluster instance."""
        destdir = pl.Path(destdir).expanduser().resolve()

        _start_script = start_script or configuration.SCRIPTS_DIR / "start-cluster"
        _stop_script = stop_script or configuration.SCRIPTS_DIR / "stop-cluster"

        start_script = pl.Path(_start_script).expanduser().resolve()
        stop_script = pl.Path(_stop_script).expanduser().resolve()

        self._reconfigure_local(
            indir=start_script.parent, destdir=destdir, instance_num=instance_num
        )
        new_start_script = destdir / start_script.name
        new_stop_script = destdir / stop_script.name

        return InstanceFiles(
            start_script=new_start_script,
            stop_script=new_stop_script,
            start_script_args=[],
            dir=destdir,
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
        addr = self._preselect_addr(instance_num=instance_num)
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
            topology = self._gen_legacy_topology(addr=addr, ports=all_except)
            helpers.write_json(
                out_file=destdir / f"split-topology-{node_name}.json", content=topology
            )

            # P2P topology
            fixed_ports = all_except[:4]
            p2p_topology = self._gen_p2p_topology(
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

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        ports_per_instance = 10
        offset = instance_num * ports_per_instance
        base = configuration.PORTS_BASE + offset
        last_port = base + ports_per_instance - 1

        relay1_ports = NodePorts(
            num=0,
            node=base,
            ekg=base + 1,
            prometheus=base + 2,
        )

        ports = InstancePorts(
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
        scripts_dir = configuration.SCRIPTS_DIR
        common_dir = scripts_dir.parent / "common"

        shutil.copytree(
            scripts_dir, destdir, symlinks=True, ignore_dangling_symlinks=True, dirs_exist_ok=True
        )
        shutil.copytree(
            common_dir, destdir, symlinks=True, ignore_dangling_symlinks=True, dirs_exist_ok=True
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

        # The `common` dir is available only if cluster scripts are not taken from custom
        # `indir`, so it needs to be optional.
        common_dir = indir.parent / "common"

        for infile in itertools.chain(*infiles, common_dir.glob("*")):
            fname = infile.name
            outfile = destdir / fname

            with open(infile, encoding="utf-8") as in_fp:
                content = in_fp.read()

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

            with open(outfile, "w", encoding="utf-8") as out_fp:
                out_fp.write(new_content)

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
    ) -> InstanceFiles:
        """Prepare scripts files for starting and stopping cluster instance.

        There is just one cluster instance running for a given testnet. We keep the `instance_num`
        support anyway, as this makes it possible to run multiple testnets on the same machine.
        """
        destdir = pl.Path(destdir).expanduser().resolve()
        destdir_bootstrap = destdir / self.BOOTSTRAP_CONF
        destdir_bootstrap.mkdir(exist_ok=True)

        _start_script = start_script or configuration.SCRIPTS_DIR / "start-cluster"
        _stop_script = stop_script or configuration.SCRIPTS_DIR / "stop-cluster"

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

        return InstanceFiles(
            start_script=new_start_script,
            stop_script=new_stop_script,
            start_script_args=[configuration.BOOTSTRAP_DIR],
            dir=destdir,
        )
