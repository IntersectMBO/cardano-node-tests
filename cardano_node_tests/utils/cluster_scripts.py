"""Functionality for cluster scripts (starting and stopping clusters).

* copying scripts and their configuration, so it can be atered by tests
* setup of scripts and their configuration for starting of multiple cluster instances
"""
# pylint: disable=abstract-method
import itertools
import json
import random
import shutil
from pathlib import Path
from typing import Iterable
from typing import List
from typing import NamedTuple
from typing import Sequence
from typing import Tuple
from typing import Union

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils.types import FileType

STOP_SCRIPT = "supervisord_stop"


class InstanceFiles(NamedTuple):
    start_script: Path
    stop_script: Path
    start_script_args: List[str]
    dir: Path


class StartupFiles(NamedTuple):
    start_script: Path
    genesis_spec: Path
    config_glob: str


class NodePorts(NamedTuple):
    num: int
    node: int
    ekg: int
    prometheus: int


class InstancePorts(NamedTuple):
    base: int
    webserver: int
    metrics_submit_api: int
    submit_api: int
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
    node_ports: Union[List[NodePorts], Tuple[()]] = ()


class ScriptsTypes:
    """Generic cluster scripts."""

    LOCAL = "local"
    TESTNET = "testnet"
    TESTNET_NOPOOLS = "testnet_nopools"

    def __init__(self) -> None:
        self.type = "unknown"

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        raise NotImplementedError(f"Not implemented for cluster instance type '{self.type}'.")

    def copy_scripts_files(self, destdir: FileType) -> StartupFiles:
        """Make copy of cluster scripts files."""
        raise NotImplementedError(f"Not implemented for cluster instance type '{self.type}'.")

    def prepare_scripts_files(
        self,
        destdir: FileType,
        instance_num: int,
        start_script: FileType = "",
        stop_script: FileType = "",
    ) -> InstanceFiles:
        """Prepare scripts files for starting and stopping cluster instance."""
        raise NotImplementedError(f"Not implemented for cluster instance type '{self.type}'.")

    def gen_split_topology_files(
        self, destdir: FileType, instance_num: int, offset: int = 0
    ) -> None:
        """Generate topology files for split network."""
        raise NotImplementedError(f"Not implemented for cluster instance type '{self.type}'.")


class LocalScripts(ScriptsTypes):
    """Local cluster scripts (full cardano mode)."""

    def __init__(self, num_pools: int = -1) -> None:
        super().__init__()
        self.type = ScriptsTypes.LOCAL
        self.num_pools = num_pools
        if num_pools == -1:
            self.num_pools = configuration.NUM_POOLS

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        ports_per_instance = 100
        ports_per_node = 5
        offset = instance_num * ports_per_instance
        base = 32000 + offset
        last_port = base + ports_per_instance - 1

        node_ports = []
        for i in range(self.num_pools + 1):  # +1 for BFT node
            rec_base = base + (i * ports_per_node)
            node_ports.append(
                NodePorts(
                    num=i,
                    node=rec_base,
                    ekg=rec_base + 1,
                    prometheus=rec_base + 2,
                )
            )

        ports = InstancePorts(
            base=base,
            webserver=last_port,
            metrics_submit_api=last_port - 1,
            submit_api=last_port - 2,
            supervisor=12001 + instance_num,
            # relay1
            relay1=0,
            ekg_relay1=0,
            prometheus_relay1=0,
            # bft1
            bft1=base,
            ekg_bft1=base + 1,
            prometheus_bft1=base + 2,
            # pool1
            pool1=base + 5,
            ekg_pool1=base + 6,
            prometheus_pool1=base + 7,
            # pool2
            pool2=base + 10,
            ekg_pool2=base + 11,
            prometheus_pool2=base + 12,
            # pool3
            pool3=base + 15,
            ekg_pool3=base + 16,
            prometheus_pool3=base + 17,
            # all nodes
            node_ports=node_ports,
        )
        return ports

    def copy_scripts_files(self, destdir: FileType) -> StartupFiles:
        """Make copy of cluster scripts files located in this repository."""
        destdir = Path(destdir).expanduser().resolve()
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

    def _replace_node_template(
        self, template_file: Path, node_rec: NodePorts, instance_num: int
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
        self, infile: Path, instance_ports: InstancePorts, instance_num: int, ports_per_node: int
    ) -> str:
        """Replace instance variables in given content."""
        content = infile.read_text()
        # replace cluster instance number
        new_content = content.replace("%%INSTANCE_NUM%%", str(instance_num))
        # replace number of pools
        new_content = new_content.replace("%%NUM_POOLS%%", str(self.num_pools))
        # replace node port number strings
        new_content = new_content.replace("%%NODE_PORT_BASE%%", str(instance_ports.base))
        # replace number of reserved ports per node
        new_content = new_content.replace("%%PORTS_PER_NODE%%", str(ports_per_node))
        # reconfigure supervisord port
        new_content = new_content.replace("%%SUPERVISOR_PORT%%", str(instance_ports.supervisor))
        # reconfigure submit-api port
        new_content = new_content.replace("%%SUBMIT_API_PORT%%", str(instance_ports.submit_api))
        # reconfigure submit-api metrics port
        new_content = new_content.replace(
            "%%METRICS_SUBMIT_API_PORT%%", str(instance_ports.metrics_submit_api)
        )
        # reconfigure webserver port
        new_content = new_content.replace("%%WEBSERVER_PORT%%", str(instance_ports.webserver))
        return new_content

    def _gen_legacy_topology(self, ports: Iterable[int]) -> dict:
        """Generate legacy topology for given ports."""
        producers = [{"addr": "127.0.0.1", "port": port, "valency": 1} for port in ports]
        topology = {"Producers": producers}
        return topology

    def _gen_p2p_topology(self, ports: List[int], fixed_ports: List[int]) -> dict:
        """Generate p2p topology for given ports."""
        # select fixed ports and several randomly selected ports
        sample_ports = random.sample(ports, 3) if len(ports) > 3 else ports
        selected_ports = set(fixed_ports + sample_ports)
        access_points = [{"address": "127.0.0.1", "port": port} for port in selected_ports]
        topology = {
            "localRoots": [
                {"accessPoints": access_points, "advertise": False, "valency": len(access_points)},
            ],
            "publicRoots": [],
        }
        return topology

    def _gen_p2p_topology_old(self, ports: List[int], fixed_ports: List[int]) -> dict:
        """Generate p2p topology for given ports in the old topology format."""
        # select fixed ports and several randomly selected ports
        selected_ports = set(fixed_ports + random.sample(ports, 3))
        access_points = [{"address": "127.0.0.1", "port": port} for port in selected_ports]
        topology = {
            "LocalRoots": {
                "groups": [
                    {
                        "localRoots": {"accessPoints": access_points, "advertise": False},
                        "valency": len(access_points),
                    }
                ]
            },
            "PublicRoots": [],
        }
        return topology

    def _gen_supervisor_conf(self, instance_num: int, instance_ports: InstancePorts) -> str:
        """Generate supervisor configuration for given instance."""
        lines = [
            "[inet_http_server]",
            f"port=127.0.0.1:{instance_ports.supervisor}",
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
                    "startsecs=3",
                ]
            )

        lines.extend(
            [
                "\n[group:nodes]",
                f"programs={','.join(programs)}",
                "\n[program:webserver]",
                f"command=python -m http.server {instance_ports.webserver}",
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

    def _gen_topology_files(self, destdir: Path, nodes: Sequence[NodePorts]) -> None:
        """Generate topology files for all nodes."""
        all_nodes = [p.node for p in nodes]

        for node_rec in nodes:
            all_except = all_nodes[:]
            all_except.remove(node_rec.node)
            node_name = "bft1" if node_rec.num == 0 else f"pool{node_rec.num}"

            # Legacy topology

            topology = self._gen_legacy_topology(ports=all_except)
            dest_legacy = destdir / f"topology-{node_name}.json"
            dest_legacy.write_text(f"{json.dumps(topology, indent=4)}\n")

            # P2P topology

            # bft1 and first three pools
            fixed_ports = all_except[:4]

            # Use both old and new format for P2P topology.
            # When testing mix of legacy and P2P topologies, odd numbered pools use legacy
            # topology. Here, for that reason, the decision cannot be based on oddity, otherwise
            # we would use just single P2P topology format for all pools. At the same time we
            # want the selection process to be deterministic, so we don't want to use random.
            if node_rec.num % 3 == 0:
                p2p_topology = self._gen_p2p_topology_old(ports=all_except, fixed_ports=fixed_ports)
            else:
                p2p_topology = self._gen_p2p_topology(ports=all_except, fixed_ports=fixed_ports)

            dest_p2p = destdir / f"p2p-topology-{node_name}.json"
            dest_p2p.write_text(f"{json.dumps(p2p_topology, indent=4)}\n")

    def _reconfigure_local(self, indir: Path, destdir: Path, instance_num: int) -> None:
        """Reconfigure cluster scripts and config files."""
        instance_ports = self.get_instance_ports(instance_num)
        ports_per_node = instance_ports.pool1 - instance_ports.bft1

        # reconfigure cluster instance files
        for infile in indir.glob("*"):
            fname = infile.name

            # skip template files
            if fname.startswith("template-"):
                continue

            dest_file = destdir / fname
            dest_content = self._replace_instance_files(
                infile=infile,
                instance_ports=instance_ports,
                instance_num=instance_num,
                ports_per_node=ports_per_node,
            )
            dest_file.write_text(f"{dest_content}\n")

            # make `*.sh` files and files without extension executable
            if "." not in fname or fname.endswith(".sh"):
                dest_file.chmod(0o755)

        # generate config and topology files from templates
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

        self._gen_topology_files(destdir=destdir, nodes=instance_ports.node_ports)

        supervisor_conf_file = destdir / "supervisor.conf"
        supervisor_conf_content = self._gen_supervisor_conf(
            instance_num=instance_num, instance_ports=instance_ports
        )
        supervisor_conf_file.write_text(f"{supervisor_conf_content}\n")

    def prepare_scripts_files(
        self,
        destdir: FileType,
        instance_num: int,
        start_script: FileType = "",
        stop_script: FileType = "",
    ) -> InstanceFiles:
        """Prepare scripts files for starting and stopping cluster instance."""
        destdir = Path(destdir).expanduser().resolve()

        _start_script = start_script or configuration.SCRIPTS_DIR / "start-cluster-hfc"
        _stop_script = stop_script or configuration.SCRIPTS_DIR / "stop-cluster-hfc"

        start_script = Path(_start_script).expanduser().resolve()
        stop_script = Path(_stop_script).expanduser().resolve()

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
        self, destdir: FileType, instance_num: int, offset: int = 0
    ) -> None:
        """Generate topology files for split network."""
        if self.num_pools < 4:
            raise ValueError(
                "There must be at least 4 pools for split topology "
                f"(current number: {self.num_pools})"
            )

        destdir = Path(destdir).expanduser().resolve()
        instance_ports = self.get_instance_ports(instance_num)
        nodes = instance_ports.node_ports

        all_nodes = [p.node for p in nodes]

        # Split nodes index (+1 for bft node, which is not block producer)
        split_idx = len(all_nodes) // 2 + 1 + offset
        first_half = all_nodes[:split_idx]
        second_half = all_nodes[split_idx:]

        if min(len(first_half), len(second_half)) < 2:
            raise ValueError(
                "There must be at least 2 nodes on each side of split "
                f"(number of pools: {self.num_pools})"
            )

        for node_rec in nodes:
            ports_group = first_half if node_rec.node in first_half else second_half
            all_except = ports_group[:]
            all_except.remove(node_rec.node)
            node_name = "bft1" if node_rec.num == 0 else f"pool{node_rec.num}"

            # Legacy topology
            topology = self._gen_legacy_topology(ports=all_except)
            dest_legacy = destdir / f"split-topology-{node_name}.json"
            dest_legacy.write_text(f"{json.dumps(topology, indent=4)}\n")

            # P2P topology
            fixed_ports = all_except[:4]
            p2p_topology = self._gen_p2p_topology(ports=all_except, fixed_ports=fixed_ports)
            dest_p2p = destdir / f"p2p-split-topology-{node_name}.json"
            dest_p2p.write_text(f"{json.dumps(p2p_topology, indent=4)}\n")


class TestnetScripts(ScriptsTypes):
    """Testnet cluster scripts (full cardano mode)."""

    TESTNET_GLOBS = (
        "config*.json",
        "genesis-*.json",
        "topology-*.json",
        "dbsync-config.*",
        "submit-api-config.*",
    )
    BOOTSTRAP_CONF = "testnet_conf"

    def __init__(self) -> None:
        super().__init__()
        self.type = ScriptsTypes.TESTNET

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        offset = (50 + instance_num) * 10
        base = 30000 + offset
        metrics_base = 30300 + offset

        ports = InstancePorts(
            base=base,
            webserver=base,
            metrics_submit_api=metrics_base,
            submit_api=base + 9,
            supervisor=12001 + instance_num,
            relay1=base + 1,
            ekg_relay1=metrics_base + 1,
            prometheus_relay1=metrics_base + 2,
            bft1=0,
            ekg_bft1=0,
            prometheus_bft1=0,
            pool1=base + 4,
            ekg_pool1=metrics_base + 6,
            prometheus_pool1=metrics_base + 7,
            pool2=base + 5,
            ekg_pool2=metrics_base + 8,
            prometheus_pool2=metrics_base + 9,
            pool3=0,
            ekg_pool3=0,
            prometheus_pool3=0,
        )
        return ports

    def copy_scripts_files(self, destdir: FileType) -> StartupFiles:
        """Make copy of cluster scripts files located in this repository."""
        destdir = Path(destdir).expanduser().resolve()
        scripts_dir = configuration.SCRIPTS_DIR
        shutil.copytree(
            scripts_dir, destdir, symlinks=True, ignore_dangling_symlinks=True, dirs_exist_ok=True
        )

        start_script = destdir / "start-cluster"
        assert start_script.exists()

        bootstrap_conf_dir = self.get_bootstrap_conf_dir(bootstrap_dir=destdir)
        destdir_bootstrap = destdir / self.BOOTSTRAP_CONF
        destdir_bootstrap.mkdir()
        _infiles = [list(bootstrap_conf_dir.glob(g)) for g in self.TESTNET_GLOBS]
        infiles = list(itertools.chain.from_iterable(_infiles))
        for infile in infiles:
            shutil.copy(infile, destdir_bootstrap)

        config_glob = f"{self.BOOTSTRAP_CONF}/config-*.json"
        # TODO: it's not really a spec file in case of a testnet
        genesis_json = destdir / self.BOOTSTRAP_CONF / "genesis-shelley.json"

        return StartupFiles(
            start_script=start_script, genesis_spec=genesis_json, config_glob=config_glob
        )

    def _reconfigure_testnet(
        self, indir: Path, destdir: Path, instance_num: int, globs: List[str]
    ) -> None:
        """Reconfigure cluster scripts and config files."""
        instance_ports = self.get_instance_ports(instance_num)
        _infiles = [list(indir.glob(g)) for g in globs]
        infiles = list(itertools.chain.from_iterable(_infiles))
        for infile in infiles:
            fname = infile.name
            dest_file = destdir / fname

            with open(infile, encoding="utf-8") as in_fp:
                content = in_fp.read()

            # replace cluster instance number
            new_content = content.replace(
                "/state-cluster%%INSTANCE_NUM%%", f"/state-cluster{instance_num}"
            )
            # replace node port number strings, omitting the last digit
            new_content = new_content.replace("%%NODE_PORT_BASE%%", str(instance_ports.base // 10))
            # reconfigure supervisord port
            new_content = new_content.replace("%%SUPERVISOR_PORT%%", str(instance_ports.supervisor))
            # reconfigure submit-api port
            new_content = new_content.replace("%%SUBMIT_API_PORT%%", str(instance_ports.submit_api))
            # reconfigure submit-api metrics port
            new_content = new_content.replace(
                "%%METRICS_SUBMIT_API_PORT%%", str(instance_ports.metrics_submit_api)
            )
            # replace metrics port number strings, omitting the last digit
            new_content = new_content.replace(
                "%%METRICS_PORT_BASE%%", str(instance_ports.ekg_relay1 // 10)
            )

            with open(dest_file, "w", encoding="utf-8") as out_fp:
                out_fp.write(new_content)

            # make `*.sh` files and files without extension executable
            if "." not in fname or fname.endswith(".sh"):
                dest_file.chmod(0o755)

    def _reconfigure_bootstrap(
        self, indir: Path, destdir: Path, instance_num: int, globs: List[str]
    ) -> None:
        """Copy and reconfigure config files from bootstrap dir.

        .. warning::
           This can be fragile as we are changing real port numbers, not just string tokens.
        """
        instance_ports = self.get_instance_ports(instance_num)
        _infiles = [list(indir.glob(g)) for g in globs]
        infiles = list(itertools.chain.from_iterable(_infiles))
        for infile in infiles:
            fname = infile.name
            dest_file = destdir / fname

            # copy genesis and topology files without changing them
            if "config" not in fname:
                shutil.copy(infile, dest_file)
                continue

            with open(infile, encoding="utf-8") as in_fp:
                content = in_fp.read()

            # replace node port number strings, omitting the last digit
            new_content = content.replace("3000", str(instance_ports.base // 10))
            # replace metrics port number strings, omitting the last digit
            new_content = new_content.replace("3030", str(instance_ports.ekg_relay1 // 10))
            # reconfigure submit-api port
            new_content = new_content.replace("8090", str(instance_ports.submit_api))

            with open(dest_file, "w", encoding="utf-8") as out_fp:
                out_fp.write(new_content)

    def _is_bootstrap_conf_dir(self, bootstrap_dir: Path) -> bool:
        return all(list(bootstrap_dir.glob(g)) for g in self.TESTNET_GLOBS)

    def get_bootstrap_conf_dir(self, bootstrap_dir: Path) -> Path:
        bootstrap_conf_dir = bootstrap_dir / self.BOOTSTRAP_CONF
        if not self._is_bootstrap_conf_dir(bootstrap_conf_dir):
            if not configuration.BOOTSTRAP_DIR:
                raise RuntimeError("The 'BOOTSTRAP_DIR' env variable is not set.")
            bootstrap_conf_dir = Path(configuration.BOOTSTRAP_DIR).expanduser().resolve()
        if not self._is_bootstrap_conf_dir(bootstrap_conf_dir):
            raise RuntimeError("The 'BOOTSTRAP_DIR' doesn't contain all the needed files.")
        return bootstrap_conf_dir

    def prepare_scripts_files(
        self,
        destdir: FileType,
        instance_num: int,
        start_script: FileType = "",
        stop_script: FileType = "",
    ) -> InstanceFiles:
        """Prepare scripts files for starting and stopping cluster instance."""
        destdir = Path(destdir).expanduser().resolve()
        destdir_bootstrap = destdir / self.BOOTSTRAP_CONF
        destdir_bootstrap.mkdir(exist_ok=True)

        _start_script = start_script or configuration.SCRIPTS_DIR / "start-cluster"
        _stop_script = stop_script or configuration.SCRIPTS_DIR / "stop-cluster"

        start_script = Path(_start_script).expanduser().resolve()
        stop_script = Path(_stop_script).expanduser().resolve()

        bootstrap_conf_dir = self.get_bootstrap_conf_dir(bootstrap_dir=start_script.parent)

        self._reconfigure_testnet(
            indir=start_script.parent, destdir=destdir, instance_num=instance_num, globs=["*"]
        )
        new_start_script = destdir / start_script.name
        new_stop_script = destdir / stop_script.name

        self._reconfigure_bootstrap(
            indir=bootstrap_conf_dir,
            destdir=destdir_bootstrap,
            instance_num=instance_num,
            globs=list(self.TESTNET_GLOBS),
        )

        return InstanceFiles(
            start_script=new_start_script,
            stop_script=new_stop_script,
            start_script_args=[configuration.BOOTSTRAP_DIR],
            dir=destdir,
        )


class TestnetNopoolsScripts(TestnetScripts):
    """Testnet cluster scripts (full cardano mode), no pools."""

    def __init__(self) -> None:
        super().__init__()
        self.type = ScriptsTypes.TESTNET_NOPOOLS

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        offset = (50 + instance_num) * 10
        base = 30000 + offset
        metrics_base = 30300 + offset

        ports = InstancePorts(
            base=base,
            webserver=0,
            metrics_submit_api=metrics_base,
            submit_api=base + 9,
            supervisor=12001 + instance_num,
            relay1=base + 1,
            ekg_relay1=metrics_base + 1,
            prometheus_relay1=metrics_base + 2,
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
        )
        return ports
