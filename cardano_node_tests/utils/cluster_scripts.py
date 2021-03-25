"""Functionality for cluster scripts (starting and stopping clusters).

* copying scripts and their configuration, so it can be atered by tests
* setup of scripts and their configuration for starting of multiple cluster instances
"""
import itertools
import re
import shutil
from pathlib import Path
from typing import List
from typing import NamedTuple

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType


class InstanceFiles(NamedTuple):
    start_script: Path
    stop_script: Path
    start_script_args: List[str]
    dir: Path


class StartupFiles(NamedTuple):
    start_script: Path
    genesis_spec: Path
    config_glob: str


class InstancePorts(NamedTuple):
    base: int
    webserver: int
    bft1: int
    relay1: int
    pool1: int
    pool2: int
    pool3: int
    supervisor: int
    ekg_bft1: int
    ekg_relay1: int
    ekg_pool1: int
    ekg_pool2: int
    ekg_pool3: int
    prometheus_bft1: int
    prometheus_relay1: int
    prometheus_pool1: int
    prometheus_pool2: int
    prometheus_pool3: int


class ScriptsTypes:
    """Generic cluster scripts."""

    DEVOPS = "devops"
    LOCAL = "local"
    TESTNET = "testnet"
    TESTNET_NOPOOLS = "testnet_nopools"

    def __init__(self) -> None:
        self.type = "unknown"

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        raise NotImplementedError(f"Not implemented for cluster instance type '{self.type}'.")

    def copy_scripts_files(self, destdir: Path) -> StartupFiles:
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


class DevopsScripts(ScriptsTypes):
    """DevOps cluster scripts (shelley-only mode)."""

    def __init__(self) -> None:
        super().__init__()
        self.type = ScriptsTypes.DEVOPS

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        offset = (50 + instance_num) * 10
        base = 30000 + offset

        ekg = 12588 + instance_num
        prometheus = 12898 + instance_num

        ports = InstancePorts(
            base=base,
            webserver=base,
            bft1=base + 1,
            relay1=0,
            pool1=base + 2,
            pool2=base + 3,
            pool3=0,
            supervisor=12001 + instance_num,
            ekg_bft1=ekg,
            ekg_relay1=0,
            ekg_pool1=ekg,
            ekg_pool2=ekg,
            ekg_pool3=0,
            prometheus_bft1=prometheus,
            prometheus_relay1=0,
            prometheus_pool1=prometheus,
            prometheus_pool2=prometheus,
            prometheus_pool3=0,
        )
        return ports

    def _get_node_config_paths(self, start_script: Path) -> List[Path]:
        """Return path of node config files in nix."""
        with open(start_script) as infile:
            content = infile.read()

        node_config = re.findall(r"cp /nix/store/(.+\.json) ", content)
        nix_store = Path("/nix/store")
        node_config_paths = [nix_store / c for c in node_config]

        return node_config_paths

    def copy_scripts_files(self, destdir: Path) -> StartupFiles:
        """Make copy of cluster scripts files located on nix."""
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
        new_content = new_content.replace("3000", str(instance_ports.base // 10))
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

    def prepare_scripts_files(
        self,
        destdir: FileType,
        instance_num: int,
        start_script: FileType = "",
        stop_script: FileType = "",
    ) -> InstanceFiles:
        """Prepare scripts files for starting and stopping cluster instance."""
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
            start_script=new_start_script,
            stop_script=new_stop_script,
            start_script_args=[],
            dir=destdir,
        )


class LocalScripts(ScriptsTypes):
    """Local cluster scripts (full cardano mode)."""

    def __init__(self) -> None:
        super().__init__()
        self.type = ScriptsTypes.LOCAL

    def get_instance_ports(self, instance_num: int) -> InstancePorts:
        """Return ports mapping for given cluster instance."""
        offset = (50 + instance_num) * 10
        base = 30000 + offset
        metrics_base = 30300 + offset

        ports = InstancePorts(
            base=base,
            webserver=base,
            bft1=base + 1,
            relay1=0,
            pool1=base + 2,
            pool2=base + 3,
            pool3=base + 4,
            supervisor=12001 + instance_num,
            ekg_bft1=metrics_base,
            ekg_relay1=0,
            ekg_pool1=metrics_base + 2,
            ekg_pool2=metrics_base + 4,
            ekg_pool3=metrics_base + 6,
            prometheus_bft1=metrics_base + 1,
            prometheus_relay1=0,
            prometheus_pool1=metrics_base + 3,
            prometheus_pool2=metrics_base + 5,
            prometheus_pool3=metrics_base + 7,
        )
        return ports

    def copy_scripts_files(self, destdir: Path) -> StartupFiles:
        """Make copy of cluster scripts files located in this repository."""
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

    def _reconfigure_local(self, indir: Path, destdir: Path, instance_num: int) -> None:
        """Reconfigure scripts and config files located in this repository."""
        instance_ports = self.get_instance_ports(instance_num)
        for infile in indir.glob("*"):
            fname = infile.name
            dest_file = destdir / fname

            if "genesis" in fname:
                shutil.copy(infile, dest_file)
                continue

            with open(infile) as in_fp:
                content = in_fp.read()

            new_content = content.replace("/state-cluster", f"/state-cluster{instance_num}")
            # replace node port number strings, omitting the last digit
            new_content = new_content.replace("3000", str(instance_ports.base // 10))
            new_content = new_content.replace("9001", str(instance_ports.supervisor))
            new_content = new_content.replace(
                "supervisorctl ", f"supervisorctl -s http://127.0.0.1:{instance_ports.supervisor} "
            )

            if fname.startswith("config-"):
                # reconfigure metrics ports in config-*.json
                new_content = new_content.replace("3030", str(instance_ports.ekg_bft1 // 10))

            with open(dest_file, "w") as out_fp:
                out_fp.write(new_content)

            # make files without extension executable
            if "." not in fname:
                dest_file.chmod(0o755)

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


class TestnetScripts(ScriptsTypes):
    """Testnet cluster scripts (full cardano mode)."""

    TESTNET_GLOBS = ("config*.json", "genesis-*.json", "topology-*.json")
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
            bft1=0,
            relay1=base + 1,
            pool1=base + 4,
            pool2=base + 5,
            pool3=0,
            supervisor=12001 + instance_num,
            ekg_bft1=0,
            ekg_relay1=metrics_base,
            ekg_pool1=metrics_base + 6,
            ekg_pool2=metrics_base + 8,
            ekg_pool3=0,
            prometheus_bft1=0,
            prometheus_relay1=metrics_base + 1,
            prometheus_pool1=metrics_base + 7,
            prometheus_pool2=metrics_base + 9,
            prometheus_pool3=0,
        )
        return ports

    def copy_scripts_files(self, destdir: Path) -> StartupFiles:
        """Make copy of cluster scripts files located in this repository."""
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
        """Reconfigure scripts and config files located in this repository."""
        instance_ports = self.get_instance_ports(instance_num)
        _infiles = [list(indir.glob(g)) for g in globs]
        infiles = list(itertools.chain.from_iterable(_infiles))
        for infile in infiles:
            fname = infile.name
            dest_file = destdir / fname

            if "genesis" in fname:
                shutil.copy(infile, dest_file)
                continue

            with open(infile) as in_fp:
                content = in_fp.read()

            new_content = content.replace("/state-cluster", f"/state-cluster{instance_num}")
            # replace node port number strings, omitting the last digit
            new_content = new_content.replace("3000", str(instance_ports.base // 10))
            # reconfigure metrics ports
            new_content = new_content.replace("3030", str(instance_ports.ekg_relay1 // 10))
            new_content = new_content.replace("9001", str(instance_ports.supervisor))
            new_content = new_content.replace(
                "supervisorctl ", f"supervisorctl -s http://127.0.0.1:{instance_ports.supervisor} "
            )

            with open(dest_file, "w") as out_fp:
                out_fp.write(new_content)

            # make files without extension executable
            if "." not in fname:
                dest_file.chmod(0o755)

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

        self._reconfigure_testnet(
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
            bft1=0,
            relay1=base + 1,
            pool1=0,
            pool2=0,
            pool3=0,
            supervisor=12001 + instance_num,
            ekg_bft1=0,
            ekg_relay1=metrics_base,
            ekg_pool1=0,
            ekg_pool2=0,
            ekg_pool3=0,
            prometheus_bft1=0,
            prometheus_relay1=metrics_base + 1,
            prometheus_pool1=0,
            prometheus_pool2=0,
            prometheus_pool3=0,
        )
        return ports
