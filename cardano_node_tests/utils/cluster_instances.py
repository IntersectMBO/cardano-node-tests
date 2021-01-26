"""Functionality for setting up new cluster instance."""
import os
import re
from pathlib import Path
from typing import List
from typing import NamedTuple

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType


class InstanceFiles(NamedTuple):
    start_script: Path
    stop_script: Path
    dir: Path


class InstancePorts(NamedTuple):
    webserver: int
    bft1: int
    bft2: int
    bft3: int
    pool1: int
    pool2: int
    supervisor: int
    ekg_bft1: int
    ekg_bft2: int
    ekg_bft3: int
    ekg_pool1: int
    ekg_pool2: int
    prometheus_bft1: int
    prometheus_bft2: int
    prometheus_bft3: int
    prometheus_pool1: int
    prometheus_pool2: int


class InstanceType:
    """Generic cluster instance type."""

    DEVOPS = "devops"
    LOCAL = "local"

    def __init__(self) -> None:
        self.type = "unknown"

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        raise NotImplementedError(f"Not implemented for cluster instance type '{self.type}'.")

    def prepare_files(
        self,
        destdir: FileType,
        instance_num: int,
        start_script: FileType = "",
        stop_script: FileType = "",
    ) -> InstanceFiles:
        """Prepare files for starting and stoping cluster instance."""
        raise NotImplementedError(f"Not implemented for cluster instance type '{self.type}'.")

    def _get_cardano_node_socket_path(self, instance_num: int) -> Path:
        """Return path to socket file in the given cluster instance."""
        socket_path = Path(os.environ["CARDANO_NODE_SOCKET_PATH"]).resolve()
        state_cluster_dirname = f"state-cluster{instance_num}"
        state_cluster = socket_path.parent.parent / state_cluster_dirname
        new_socket_path = state_cluster / socket_path.name
        return new_socket_path

    def set_cardano_node_socket_path(self, instance_num: int) -> None:
        """Set the `CARDANO_NODE_SOCKET_PATH` env variable for the given cluster instance."""
        socket_path = self._get_cardano_node_socket_path(instance_num)
        os.environ["CARDANO_NODE_SOCKET_PATH"] = str(socket_path)


class DevopsInstance(InstanceType):
    """Instances for DevOps cluster type (shelley-only mode)."""

    def __init__(self) -> None:
        super().__init__()
        self.type = InstanceType.DEVOPS

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        offset = (50 + instance_num) * 10
        base = 30000 + offset

        ekg = 12588 + instance_num
        prometheus = 12898 + instance_num

        ports = InstancePorts(
            webserver=base,
            bft1=base + 1,
            bft2=0,
            bft3=0,
            pool1=base + 2,
            pool2=base + 3,
            supervisor=12001 + instance_num,
            ekg_bft1=ekg,
            ekg_bft2=ekg,
            ekg_bft3=ekg,
            ekg_pool1=ekg,
            ekg_pool2=ekg,
            prometheus_bft1=prometheus,
            prometheus_bft2=prometheus,
            prometheus_bft3=prometheus,
            prometheus_pool1=prometheus,
            prometheus_pool2=prometheus,
        )
        return ports

    def _get_nix_paths(self, infile: Path, search_str: str) -> List[Path]:
        """Return path of nix files."""
        with open(infile) as in_fp:
            content = in_fp.read()

        nix_files = re.findall(f"/nix/store/({search_str})", content)
        nix_store = Path("/nix/store")
        nix_paths = [nix_store / c for c in nix_files]

        return nix_paths

    def _reconfigure_nix_file(self, infile: Path, destdir: Path, instance_num: int) -> Path:
        """Reconfigure scripts and config files located on nix.

        Recursive function.
        """
        fname = infile.name

        with open(infile) as in_fp:
            content = in_fp.read()

        instance_ports = self.get_instance_ports(instance_num)
        new_content = content.replace("/state-cluster", f"/state-cluster{instance_num}")
        # replace node port number strings, omitting the last digit
        new_content = new_content.replace("3000", str(instance_ports.webserver // 10))
        new_content = new_content.replace("9001", str(instance_ports.supervisor))
        new_content = new_content.replace(
            "supervisorctl ", f"supervisorctl -s http://127.0.0.1:{instance_ports.supervisor} "
        )

        if fname == "start-cluster":
            # reconfigure pool margin
            new_content = new_content.replace('"$(jq -n $POOL_MARGIN_NUM/10)"', "0.35")
            # reconfigure pool cost
            new_content = new_content.replace('"$(($RANDOM % 100000000))"', "600")

            # reconfigure path to supervisor config
            supervisor_conf = self._get_nix_paths(infile, r"[^/]+-supervisor\.conf")[0]
            dest_supervisor_conf = (destdir / supervisor_conf.name).resolve()
            new_content = new_content.replace(str(supervisor_conf), str(dest_supervisor_conf))
            self._reconfigure_nix_file(
                infile=supervisor_conf, destdir=destdir, instance_num=instance_num
            )

            # reconfigure path to node.json file
            _node_json = re.search(
                r"cp (.*node\.json) \./state-cluster[0-9]?/config.json", new_content
            )
            if not _node_json:
                raise RuntimeError("Failed to find `node.json` in the `start-cluster` script.")
            node_json = Path(_node_json.group(1))
            dest_node_json = (destdir / node_json.name).resolve()
            new_content = new_content.replace(str(node_json), str(dest_node_json))
            self._reconfigure_nix_file(infile=node_json, destdir=destdir, instance_num=instance_num)
        elif fname.endswith("-supervisor.conf"):
            # reconfigure supervisor settings
            node_commands = self._get_nix_paths(infile, r"[^/]+-cardano-node")
            for ncmd in node_commands:
                dest_ncmd = (destdir / ncmd.name).resolve()
                new_content = new_content.replace(str(ncmd), str(dest_ncmd))
                self._reconfigure_nix_file(infile=ncmd, destdir=destdir, instance_num=instance_num)
        elif fname.endswith("-cardano-node"):
            # reconfigure path to topology config file
            topology_yaml = self._get_nix_paths(infile, r"[^/]+-topology\.yaml")[0]
            dest_topology_yaml = (destdir / topology_yaml.name).resolve()
            new_content = new_content.replace(str(topology_yaml), str(dest_topology_yaml))
            # reconfigure ports in topology config
            self._reconfigure_nix_file(
                infile=topology_yaml, destdir=destdir, instance_num=instance_num
            )
        elif fname.endswith("node.json"):
            # reconfigure metrics ports in node.json
            new_content = new_content.replace("12788", str(instance_ports.ekg_bft1))
            new_content = new_content.replace("12798", str(instance_ports.prometheus_bft1))

        dest_file = destdir / fname
        with open(dest_file, "w") as out_fp:
            out_fp.write(new_content)

        # make files without extension executable
        if "." not in fname:
            dest_file.chmod(0o755)

        return dest_file

    def prepare_files(
        self,
        destdir: FileType,
        instance_num: int,
        start_script: FileType = "",
        stop_script: FileType = "",
    ) -> InstanceFiles:
        """Prepare files for starting and stoping cluster instance."""
        destdir = Path(destdir).expanduser().resolve()

        _start_script = start_script or helpers.get_cmd_path("start-cluster")
        _stop_script = stop_script or helpers.get_cmd_path("stop-cluster")

        start_script = Path(_start_script).expanduser().resolve()
        stop_script = Path(_stop_script).expanduser().resolve()

        new_start_script = self._reconfigure_nix_file(
            infile=start_script, destdir=destdir, instance_num=instance_num
        )
        new_stop_script = self._reconfigure_nix_file(
            infile=stop_script, destdir=destdir, instance_num=instance_num
        )

        return InstanceFiles(
            start_script=new_start_script, stop_script=new_stop_script, dir=destdir
        )


class LocalInstance(InstanceType):
    """Instances for local cluster type (full cardano mode)."""

    def __init__(self) -> None:
        super().__init__()
        self.type = InstanceType.LOCAL

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        offset = (50 + instance_num) * 10
        base = 30000 + offset
        metrics_base = 30300 + offset

        ports = InstancePorts(
            webserver=base,
            bft1=base + 1,
            bft2=base + 2,
            bft3=base + 3,
            pool1=base + 4,
            pool2=base + 5,
            supervisor=12001 + instance_num,
            ekg_bft1=metrics_base,
            ekg_bft2=metrics_base + 2,
            ekg_bft3=metrics_base + 4,
            ekg_pool1=metrics_base + 6,
            ekg_pool2=metrics_base + 8,
            prometheus_bft1=metrics_base + 1,
            prometheus_bft2=metrics_base + 3,
            prometheus_bft3=metrics_base + 5,
            prometheus_pool1=metrics_base + 7,
            prometheus_pool2=metrics_base + 9,
        )
        return ports

    def _reconfigure_local(self, indir: Path, destdir: Path, instance_num: int) -> None:
        """Reconfigure scripts and config files located in this repository."""
        for infile in indir.glob("*"):
            fname = infile.name

            with open(infile) as in_fp:
                content = in_fp.read()

            instance_ports = self.get_instance_ports(instance_num)
            new_content = content.replace("/state-cluster", f"/state-cluster{instance_num}")
            # replace node port number strings, omitting the last digit
            new_content = new_content.replace("3000", str(instance_ports.webserver // 10))
            new_content = new_content.replace("9001", str(instance_ports.supervisor))
            new_content = new_content.replace(
                "supervisorctl ", f"supervisorctl -s http://127.0.0.1:{instance_ports.supervisor} "
            )

            if fname.startswith("config-"):
                # reconfigure metrics ports in config-*.json
                new_content = new_content.replace("3030", str(instance_ports.ekg_bft1 // 10))

            dest_file = destdir / fname
            with open(dest_file, "w") as out_fp:
                out_fp.write(new_content)

            # make files without extension executable
            if "." not in fname:
                dest_file.chmod(0o755)

    def prepare_files(
        self,
        destdir: FileType,
        instance_num: int,
        start_script: FileType = "",
        stop_script: FileType = "",
    ) -> InstanceFiles:
        """Prepare files for starting and stoping cluster instance."""
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
            start_script=new_start_script, stop_script=new_stop_script, dir=destdir
        )
