import argparse
import contextlib
import datetime
import functools
import hashlib
import inspect
import io
import json
import logging
import os
import random
import shutil
import signal
import string
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Iterator
from typing import Optional
from typing import Union

from filelock import FileLock

from cardano_node_tests.utils.types import FileType

# suppress messages from filelock
logging.getLogger("filelock").setLevel(logging.WARNING)


LOGGER = logging.getLogger(__name__)

GITHUB_URL = "https://github.com/input-output-hk/cardano-node-tests"


# Use dummy locking if not executing with multiple workers.
# When running with multiple workers, operations with shared resources (like faucet addresses)
# need to be locked to single worker (otherwise e.g. balances would not check).
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
def change_cwd(dir_path: FileType) -> Iterator[FileType]:
    """Change and restore CWD - context manager."""
    orig_cwd = os.getcwd()
    os.chdir(dir_path)
    LOGGER.debug(f"Changed CWD to '{dir_path}'.")
    try:
        yield dir_path
    finally:
        os.chdir(orig_cwd)
        LOGGER.debug(f"Restored CWD to '{orig_cwd}'.")


@contextlib.contextmanager
def ignore_interrupt() -> Iterator[None]:
    """Ignore the KeyboardInterrupt signal."""
    orig_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, orig_handler)


@contextlib.contextmanager
def environ(env: dict) -> Iterator[None]:
    """Temporarily set environment variables and restore previous environment afterwards."""
    original_env = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for key, value in original_env.items():
            if value is None:
                del os.environ[key]
            else:
                os.environ[key] = value


def run_command(command: Union[str, list], workdir: FileType = "", shell: bool = False) -> bytes:
    """Run command."""
    cmd: Union[str, list]
    if isinstance(command, str):
        cmd = command if shell else command.split(" ")
    else:
        cmd = command

    # pylint: disable=consider-using-with
    if workdir:
        with change_cwd(workdir):
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=shell)
    else:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=shell)
    stdout, stderr = p.communicate()

    if p.returncode != 0:
        err_dec = stderr.decode()
        err_dec = err_dec or stdout.decode()
        raise AssertionError(f"An error occurred while running `{command}`: {err_dec}")

    return stdout


def run_in_bash(command: str, workdir: FileType = "") -> bytes:
    """Run command(s) in bash."""
    cmd = ["bash", "-o", "pipefail", "-c", f"{command}"]
    return run_command(cmd, workdir=workdir)


@functools.lru_cache
def get_current_commit() -> str:
    # TODO: make sure we are in correct repo
    return os.environ.get("GIT_REVISION") or run_command("git rev-parse HEAD").decode().strip()


@functools.lru_cache
def get_basetemp() -> Path:
    """Return base temporary directory for tests artifacts."""
    tempdir = Path(tempfile.gettempdir()) / "cardano-node-tests"
    tempdir.mkdir(mode=0o700, exist_ok=True)
    return tempdir


# TODO: unify with the implementation in clusterlib
def get_rand_str(length: int = 8) -> str:
    """Return random string."""
    if length < 1:
        return ""
    return "".join(random.choice(string.ascii_lowercase) for i in range(length))


def get_timestamped_rand_str(rand_str_length: int = 4) -> str:
    """Return random string prefixed with timestamp.

    >>> len(get_timestamped_rand_str()) == len("200801_002401314_cinf")
    True
    """
    timestamp = datetime.datetime.now().strftime("%y%m%d_%H%M%S%f")[:-3]
    rand_str_component = get_rand_str(rand_str_length)
    rand_str_component = rand_str_component and f"_{rand_str_component}"
    return f"{timestamp}{rand_str_component}"


def get_vcs_link() -> str:
    """Return link to the current line in GitHub."""
    calling_frame = inspect.currentframe().f_back  # type: ignore
    lineno = calling_frame.f_lineno  # type: ignore
    fpath = calling_frame.f_globals["__file__"]  # type: ignore
    fpart = fpath[fpath.find("cardano_node_tests") :]
    url = f"{GITHUB_URL}/blob/{get_current_commit()}/{fpart}#L{lineno}"
    return url


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
    hash_o = hashlib.blake2b()
    with open(filename, "rb") as f:
        for block in iter(lambda: f.read(blocksize), b""):
            hash_o.update(block)
    return hash_o.hexdigest()


def write_json(location: FileType, content: dict) -> FileType:
    """Write dictionary content to JSON file."""
    with open(Path(location).expanduser(), "w", encoding="utf-8") as out_file:
        out_file.write(json.dumps(content, indent=4))
    return location


def decode_bech32(bech32: str) -> str:
    """Convert from bech32 string."""
    return run_command(f"echo '{bech32}' | bech32", shell=True).decode().strip()


def encode_bech32(prefix: str, data: str) -> str:
    """Convert to bech32 string."""
    return run_command(f"echo '{data}' | bech32 {prefix}", shell=True).decode().strip()


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
    if not (abs_path.exists() and abs_path.is_file()):
        raise argparse.ArgumentTypeError(f"check_file_arg: file '{file_path}' doesn't exist")
    return abs_path


def get_cmd_path(cmd: str) -> Path:
    """Return file path of the command."""
    start_script = shutil.which(cmd)
    if not start_script:
        raise AssertionError(f"The `{cmd}` not found on PATH")
    return Path(start_script)


def replace_str_in_file(infile: Path, outfile: Path, orig_str: str, new_str: str) -> None:
    """Replace a string in file with another string."""
    with open(infile, encoding="utf-8") as in_fp:
        content = in_fp.read()

    replaced_content = content.replace(orig_str, new_str)

    with open(outfile, "w", encoding="utf-8") as out_fp:
        out_fp.write(replaced_content)


def get_eof_offset(infile: Path) -> int:
    """Return position of the current end of the file."""
    with open(infile, "rb") as in_fp:
        in_fp.seek(0, io.SEEK_END)
        last_line_pos = in_fp.tell()
    return last_line_pos


def is_in_interval(num1: float, num2: float, frac: float = 0.1) -> bool:
    """Check that the num1 is in the interval defined by num2 and its fraction."""
    num2_frac = num2 * frac
    _min = num2 - num2_frac
    _max = num2 + num2_frac
    return _min <= num1 <= _max
