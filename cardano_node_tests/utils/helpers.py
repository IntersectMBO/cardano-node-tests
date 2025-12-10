import argparse
import contextlib
import datetime
import functools
import hashlib
import inspect
import io
import itertools
import json
import logging
import os
import pathlib as pl
import random
import signal
import string
import subprocess
import types as tt
import typing as tp
from collections import abc

import cardano_node_tests.utils.types as ttypes

LOGGER = logging.getLogger(__name__)

GITHUB_URL = "https://github.com/IntersectMBO/cardano-node-tests"

TCallable = tp.TypeVar("TCallable", bound=tp.Callable)


@contextlib.contextmanager
def change_cwd(dir_path: ttypes.FileType) -> tp.Iterator[ttypes.FileType]:
    """Change and restore CWD - context manager."""
    orig_cwd = pl.Path.cwd()
    os.chdir(dir_path)
    LOGGER.debug(f"Changed CWD to '{dir_path}'.")
    try:
        yield dir_path
    finally:
        os.chdir(orig_cwd)
        LOGGER.debug(f"Restored CWD to '{orig_cwd}'.")


@contextlib.contextmanager
def ignore_interrupt() -> tp.Iterator[None]:
    """Ignore the KeyboardInterrupt signal."""
    orig_handler = None
    try:
        orig_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError as exc:
        if "signal only works in main thread" not in str(exc):
            raise

    if orig_handler is None:
        yield
        return

    try:
        yield
    finally:
        signal.signal(signal.SIGINT, orig_handler)


@contextlib.contextmanager
def environ(env: dict) -> tp.Iterator[None]:
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


def run_command(
    command: str | list,
    *,
    workdir: ttypes.FileType = "",
    ignore_fail: bool = False,
    shell: bool = False,
) -> bytes:
    """Run command."""
    cmd: str | list
    if isinstance(command, str):
        cmd = command if shell else command.split()
        cmd_str = command
    else:
        cmd = command
        cmd_str = " ".join(command)

    LOGGER.debug("Running `%s`", cmd_str)

    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=shell, cwd=workdir or None
    ) as p:
        stdout, stderr = p.communicate()
        retcode = p.returncode

    if not ignore_fail and retcode != 0:
        err_dec = stderr.decode()
        err_dec = err_dec or stdout.decode()
        msg = f"An error occurred while running `{cmd_str}`: {err_dec}"
        raise RuntimeError(msg)

    return stdout


def run_in_bash(command: str, *, workdir: ttypes.FileType = "") -> bytes:
    """Run command(s) in bash."""
    cmd = ["bash", "-o", "pipefail", "-c", f"{command}"]
    return run_command(cmd, workdir=workdir)


@functools.cache
def get_current_commit() -> str:
    # TODO: make sure we are in correct repo
    return os.environ.get("GIT_REVISION") or run_command("git rev-parse HEAD").decode().strip()


# TODO: unify with the implementation in clusterlib
def get_rand_str(length: int = 8) -> str:
    """Return random string."""
    if length < 1:
        return ""
    return "".join(random.choice(string.ascii_lowercase) for i in range(length))


# TODO: unify with the implementation in clusterlib
def prepend_flag(flag: str, contents: tp.Iterable) -> list[str]:
    """Prepend flag to every item of the sequence.

    Args:
        flag: A flag to prepend to every item of the `contents`.
        contents: A list (iterable) of content to be prepended.

    Returns:
        list[str]: A list of flag followed by content, see below.

    >>> prepend_flag("--foo", [1, 2, 3])
    ['--foo', '1', '--foo', '2', '--foo', '3']
    """
    return list(itertools.chain.from_iterable([flag, str(x)] for x in contents))


def get_timestamped_rand_str(rand_str_length: int = 4) -> str:
    """Return random string prefixed with timestamp.

    >>> len(get_timestamped_rand_str()) == len("200801_002401314_cinf")
    True
    """
    timestamp = datetime.datetime.now(tz=datetime.UTC).strftime("%y%m%d_%H%M%S%f")[:-3]
    rand_str_component = get_rand_str(length=rand_str_length)
    rand_str_component = rand_str_component and f"_{rand_str_component}"
    return f"{timestamp}{rand_str_component}"


def get_line_str_from_frame(frame: tt.FrameType) -> str:
    lineno = frame.f_lineno
    fpath = frame.f_globals["__file__"]
    line_str = f"{fpath}#L{lineno}"
    return line_str


def get_current_line_str() -> str:
    """Get `filename#lineno` of current line.

    NOTE: this will not work correctly if called from context manager.
    """
    calling_frame = None
    with contextlib.suppress(AttributeError):
        calling_frame = inspect.currentframe().f_back  # type: ignore

    if not calling_frame:
        msg = "Couldn't get the calling frame."
        raise ValueError(msg)

    return get_line_str_from_frame(frame=calling_frame)


def get_vcs_link() -> str:
    """Return link to the current line in GitHub."""
    calling_frame = None
    with contextlib.suppress(AttributeError):
        calling_frame = inspect.currentframe().f_back  # type: ignore

    if not calling_frame:
        msg = "Couldn't get the calling frame."
        raise ValueError(msg)

    line_str = get_line_str_from_frame(frame=calling_frame)
    loc_part = line_str[line_str.find("cardano_node_tests") :]
    url = f"{GITHUB_URL}/blob/{get_current_commit()}/{loc_part}"
    return url


def checksum(filename: ttypes.FileType, *, blocksize: int = 65536) -> str:
    """Return file checksum."""
    hash_o = hashlib.blake2b()
    with open(filename, "rb") as f:
        for block in iter(lambda: f.read(blocksize), b""):
            hash_o.update(block)
    return hash_o.hexdigest()


def write_json(*, out_file: ttypes.FileType, content: dict) -> ttypes.FileType:
    """Write dictionary content to JSON file."""
    with open(pl.Path(out_file).expanduser(), "w", encoding="utf-8") as out_fp:
        out_fp.write(json.dumps(content, indent=4))
    return out_file


def decode_bech32(bech32: str) -> str:
    """Convert from bech32 string."""
    return run_command(f"echo '{bech32}' | bech32", shell=True).decode().strip()


def encode_bech32(*, prefix: str, data: str) -> str:
    """Convert to bech32 string."""
    return run_command(f"echo '{data}' | bech32 {prefix}", shell=True).decode().strip()


def check_dir_arg(dir_path: str) -> pl.Path | None:
    """Check that the dir passed as argparse parameter is a valid existing dir."""
    if not dir_path:
        return None
    abs_path = pl.Path(dir_path).expanduser().resolve()
    if not (abs_path.exists() and abs_path.is_dir()):
        msg = f"check_dir_arg: directory '{dir_path}' doesn't exist"
        raise argparse.ArgumentTypeError(msg)
    return abs_path


def check_dir_arg_keep(dir_path: str) -> pl.Path | None:
    """Check that the dir passed as argparse parameter is a valid existing dir.

    Keep the original path instead resolving it to absolute.
    """
    if not dir_path:
        return None
    orig_path = pl.Path(dir_path)
    abs_path = orig_path.expanduser().resolve()
    if not (abs_path.exists() and abs_path.is_dir()):
        msg = f"check_dir_arg: directory '{dir_path}' doesn't exist"
        raise argparse.ArgumentTypeError(msg)
    if dir_path.startswith("~"):
        orig_path.expanduser()
    return orig_path


def check_file_arg(file_path: str) -> pl.Path | None:
    """Check that the file passed as argparse parameter is a valid existing file."""
    if not file_path:
        return None
    abs_path = pl.Path(file_path).expanduser().resolve()
    if not (abs_path.exists() and abs_path.is_file()):
        msg = f"check_file_arg: file '{file_path}' doesn't exist"
        raise argparse.ArgumentTypeError(msg)
    return abs_path


def get_eof_offset(infile: pl.Path) -> int:
    """Return position of the current end of the file."""
    with open(infile, "rb") as in_fp:
        in_fp.seek(0, io.SEEK_END)
        last_line_pos = in_fp.tell()
    return last_line_pos


def is_in_interval(num1: float, num2: float, *, frac: float = 0.1) -> bool:
    """Check that the num1 is in the interval defined by num2 and its fraction."""
    num2_frac = num2 * frac
    _min = num2 - num2_frac
    _max = num2 + num2_frac
    return _min <= num1 <= _max


@functools.lru_cache(maxsize=100)
def tool_has(command: str) -> bool:
    """Check if a tool has a subcommand or argument available.

    E.g. `tool_has_arg("create-script-context --plutus-v1")`
    """
    err_str = ""
    try:
        run_command(command)
    except RuntimeError as err:
        err_str = str(err)
    else:
        return True

    cmd_err = err_str.split(":", maxsplit=1)[1].strip()
    return not cmd_err.startswith("Invalid")


def flatten(iterable: tp.Iterable, *, ltypes: type[tp.Iterable] | None = None) -> tp.Generator:
    """Flatten an irregular (arbitrarily nested) iterable of iterables."""
    ltypes_p = ltypes if ltypes is not None else abc.Iterable
    remainder = iter(iterable)
    while True:
        try:
            first = next(remainder)
        except StopIteration:
            break
        if isinstance(first, ltypes_p) and not isinstance(first, str | bytes):
            remainder = itertools.chain(first, remainder)
        else:
            yield first


def validate_dict_values(
    dict1: dict[str, tp.Any], dict2: dict[str, tp.Any], *, keys: tp.Iterable[str]
) -> list[str]:
    """Compare values for specified keys between two dictionaries and return discrepancies.

    Args:
        dict1: First dictionary to compare. This represents the expected data.
        dict2: Second dictionary to compare. This represents the actual data.
        keys: List of keys to compare between the two dictionaries.

    Returns:
        A list of discrepancies, with each discrepancy describing a mismatch
        between the values in dict1 and dict2 for the specified keys.
    """
    errors = []

    for key in keys:
        expected_value = dict1.get(key)
        actual_value = dict2.get(key)

        if expected_value != actual_value:
            msg = f"Discrepancy in '{key}': {actual_value}. Expected: {expected_value}"
            errors.append(msg)

    return errors


def get_pool_param(key: str, *, pool_params: dict) -> tp.Any:
    """Get pool parameter value from pool params dict."""
    # Keys are prefixed with "sps" in cardano-node 10.6.0+
    # E.g. "owner" -> "spsOwner"
    if key.startswith("sps"):
        sps_key = key
        old_key = f"{key[3].lower()}{key[4:]}"
    else:
        sps_key = f"sps{key.capitalize()}"
        old_key = key

    val = pool_params.get(sps_key)
    if val is None:
        # Try to use old key name
        val = pool_params.get(old_key)

    return val
