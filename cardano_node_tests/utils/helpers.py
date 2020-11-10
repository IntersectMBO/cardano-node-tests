import argparse
import contextlib
import hashlib
import inspect
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Dict
from typing import Generator
from typing import Optional

from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory
from filelock import FileLock

from cardano_node_tests.utils.types import FileType

# suppress messages from filelock
logging.getLogger("filelock").setLevel(logging.WARNING)


LOGGER = logging.getLogger(__name__)

LAUNCH_PATH = Path(os.getcwd())

# Use dummy locking if not executing with multiple workers.
# When running with multiple workers, operations with shared resources (like faucet addresses)
# need to be locked to single worker (otherwise e.g. ballances would not check).
if os.environ.get("PYTEST_XDIST_TESTRUNUID"):
    IS_XDIST = True
    FileLockIfXdist: Any = FileLock
    xdist_sleep = time.sleep
else:
    IS_XDIST = False
    FileLockIfXdist = contextlib.nullcontext

    def xdist_sleep(secs: float) -> None:
        # pylint: disable=all
        pass


@contextlib.contextmanager
def change_cwd(dir_path: FileType) -> Generator[FileType, None, None]:
    """Change and restore CWD - context manager."""
    orig_cwd = os.getcwd()
    os.chdir(dir_path)
    LOGGER.debug(f"Changed CWD to '{dir_path}'.")
    try:
        yield dir_path
    finally:
        os.chdir(orig_cwd)
        LOGGER.debug(f"Restored CWD to '{orig_cwd}'.")


def run_shell_command(command: str, workdir: FileType = "") -> bytes:
    """Run command in shell."""
    cmd = f"bash -c '{command}'"
    cmd = cmd if not workdir else f"cd {workdir}; {cmd}"
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    stdout, stderr = p.communicate()
    if p.returncode != 0:
        raise AssertionError(f"An error occurred while running `{cmd}`: {stderr.decode()}")
    return stdout


def run_command(command: str, workdir: FileType = "") -> bytes:
    """Run command."""
    cmd = command.strip(" ")
    if workdir:
        with change_cwd(workdir):
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    else:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate()
    if p.returncode != 0:
        raise AssertionError(f"An error occurred while running `{cmd}`: {stderr.decode()}")
    return stdout


CURRENT_COMMIT = (
    os.environ.get("GIT_REVISION") or run_shell_command("git rev-parse HEAD").decode().strip()
)
GITHUB_URL = "https://github.com/input-output-hk/cardano-node-tests"
GITHUB_TREE_URL = f"{GITHUB_URL}/tree/{CURRENT_COMMIT}"
GITHUB_BLOB_URL = f"{GITHUB_URL}/blob/{CURRENT_COMMIT}"


def get_basetemp() -> Path:
    """Return base temporary directory for tests artifacts."""
    tempdir = Path(tempfile.gettempdir()) / "cardano-node-tests"
    tempdir.mkdir(mode=0o700, exist_ok=True)
    return tempdir


def get_pytest_globaltemp(tmp_path_factory: TempdirFactory) -> Path:
    """Return global temporary directory for a single pytest run."""
    pytest_tmp_dir = Path(tmp_path_factory.getbasetemp())
    basetemp = pytest_tmp_dir.parent if IS_XDIST else pytest_tmp_dir
    basetemp = basetemp / "tmp"
    basetemp.mkdir(exist_ok=True)
    return basetemp


def get_vcs_link() -> str:
    """Return link to the current+1 line in GitHub."""
    calling_frame = inspect.currentframe().f_back  # type: ignore
    lineno = calling_frame.f_lineno + 1  # type: ignore
    fpath = calling_frame.f_globals["__file__"]  # type: ignore
    fpart = fpath[fpath.find("cardano_node_tests") :]
    url = f"{GITHUB_BLOB_URL}/{fpart}#L{lineno}"
    return url


def get_func_name() -> str:
    """Return calling function name."""
    func_name = inspect.currentframe().f_back.f_code.co_name  # type: ignore
    return func_name


def get_id_for_mktemp(file_path: str) -> str:
    """Return an id for mktemp based on file path."""
    fpart = file_path[file_path.rfind("/") + 1 :].replace(".", "_")
    return fpart


def wait_for(
    func: Callable, delay: int = 5, num_sec: int = 180, message: str = "", silent: bool = False
) -> Any:
    """Wait for success of `func` for `num_sec`."""
    end_time = time.time() + num_sec

    while time.time() < end_time:
        response = func()
        if response:
            return response
        time.sleep(delay)

    if not silent:
        raise AssertionError(f"Failed to {message or 'finish'} in time.")
    return False


def checksum(filename: FileType, blocksize: int = 65536) -> str:
    """Return file checksum."""
    hash = hashlib.blake2b()
    with open(filename, "rb") as f:
        for block in iter(lambda: f.read(blocksize), b""):
            hash.update(block)
    return hash.hexdigest()


def write_json(location: FileType, content: dict) -> FileType:
    """Write dictionary content to JSON file."""
    with open(Path(location).expanduser(), "w") as out_file:
        out_file.write(json.dumps(content, indent=4))
    return location


def get_cardano_version() -> dict:
    """Return version info for cardano-node."""
    out = run_shell_command("cardano-node --version").decode().strip()
    env_info, git_info, *__ = out.splitlines()
    node, platform, ghc, *__ = env_info.split(" - ")
    version = {
        "cardano-node": node.split(" ")[-1],
        "platform": platform,
        "ghc": ghc,
        "git_rev": git_info.split(" ")[-1],
    }
    return version


def decode_bech32(bech32: str) -> str:
    """Convert from bech32 strings."""
    return run_shell_command(f"echo '{bech32}' | bech32").decode().strip()


def check_dir_arg(dir_path: str) -> Optional[Path]:
    """Check that the dir passed as argparse parameter is a valid existing dir."""
    if not dir_path:
        return None
    abs_path = Path(dir_path).expanduser().resolve()
    if not (abs_path.exists() and abs_path.is_dir()):
        raise argparse.ArgumentTypeError(f"check_dir_arg: directory '{dir_path}' doesn't exist")
    return abs_path


def check_file_arg(file_path: str) -> Optional[Path]:
    """Check that the file passed as argparse parameter is a valid existing file."""
    if not file_path:
        return None
    abs_path = Path(file_path).expanduser().resolve()
    if not abs_path.exists() and abs_path.is_file():
        raise argparse.ArgumentTypeError(f"check_file_arg: file '{file_path}' doesn't exist")
    return abs_path


def save_env_for_allure(request: FixtureRequest) -> None:
    """Save environment info in a format for Allure."""
    alluredir = request.config.getoption("--alluredir")

    if not alluredir:
        return

    alluredir = LAUNCH_PATH / alluredir
    metadata: Dict[str, Any] = request.config._metadata  # type: ignore
    with open(alluredir / "environment.properties", "w+") as infile:
        for k, v in metadata.items():
            if isinstance(v, dict):
                continue
            name = k.replace(" ", ".")
            infile.write(f"{name}={v}\n")


def get_cmd_path(cmd: str) -> Path:
    """Return file path of the command."""
    start_script = shutil.which(cmd)
    if not start_script:
        raise AssertionError(f"The `{cmd}` not found on PATH")
    return Path(start_script)


def replace_str_in_file(infile: Path, outfile: Path, orig_str: str, new_str: str) -> None:
    """Replace a string in file with another string."""
    with open(infile) as in_fp:
        content = in_fp.read()

    replaced_content = content.replace(orig_str, new_str)

    with open(outfile, "wt") as out_fp:
        out_fp.write(replaced_content)
