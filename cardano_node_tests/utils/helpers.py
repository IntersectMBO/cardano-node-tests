import argparse
import contextlib
import datetime
import functools
import hashlib
import inspect
import itertools
import json
import logging
import os
import pathlib as pl
import random
import shlex
import signal
import string
import subprocess
import types as tt
import typing as tp
from collections import abc

import cardano_node_tests.utils.types as ttypes

LOGGER = logging.getLogger(__name__)

GITHUB_URL = "https://github.com/IntersectMBO/cardano-node-tests"


class CommandExecError(RuntimeError):
    """Raised when a command cannot be executed at all (e.g. missing executable or workdir)."""


@contextlib.contextmanager
def change_cwd(dir_path: ttypes.FileType) -> tp.Generator[ttypes.FileType]:
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
def ignore_interrupt() -> tp.Generator[None]:
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
def environ(env: dict[str, str]) -> tp.Generator[None]:
    """Temporarily set environment variables and restore previous environment afterwards."""
    original_env = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def is_truthy_env_var(var: str) -> bool:
    """Check if an environment variable is set to a truthy value."""
    return (os.environ.get(var) or "").lower() in ("1", "true", "yes", "y", "on", "enabled")


def get_env_int(var: str, default: int) -> int:
    """Read an integer environment variable.

    Returns ``default`` when the variable is unset or empty. Raises a
    ``ValueError`` naming the offending variable when the value is malformed.
    """
    raw = os.environ.get(var)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as err:
        msg = f"Invalid integer value for env var '{var}': {raw!r}"
        raise ValueError(msg) from err


def get_env_path(var: str) -> pl.Path | None:
    """Read a path environment variable.

    Returns the resolved ``pathlib.Path`` (with ``~`` expanded) when set,
    otherwise ``None``.
    """
    raw = os.environ.get(var)
    if not raw:
        return None
    return pl.Path(raw).expanduser().resolve()


def run_command(
    command: str | list[tp.Any],
    *,
    workdir: ttypes.FileType = "",
    ignore_fail: bool = False,
    shell: bool = False,
    merge_stderr: bool = False,
    stdin_data: bytes | None = None,
) -> bytes:
    """Run command.

    Args:
        command: A command to run - either a string (tokenized with shlex unless `shell`
            is used) or a list of arguments (items are converted to str).
        workdir: A working directory for the command.
        ignore_fail: Don't raise an exception when the command fails.
        shell: Run the command in a shell.
        merge_stderr: Redirect stderr to stdout.
        stdin_data: Data to pass to stdin of the command.

    Returns:
        bytes: Content of stdout (with stderr merged in when `merge_stderr` is used).

    Raises:
        RuntimeError: When the command fails (unless `ignore_fail` is used).
        CommandExecError: When the command cannot be executed at all
            (e.g. missing or non-executable file). Subclass of RuntimeError.
    """
    cmd: str | list[str]
    if isinstance(command, str):
        cmd = command if shell else shlex.split(command)
        cmd_str = command
    else:
        cmd = [str(c) for c in command]
        cmd_str = " ".join(cmd)

    LOGGER.debug("Running `%s`", cmd_str)

    try:
        with subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin_data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            shell=shell,
            cwd=workdir or None,
        ) as p:
            stdout, stderr = p.communicate(input=stdin_data)
            retcode = p.returncode
    except OSError as err:
        msg = f"Failed to execute `{cmd_str}`: {err}"
        raise CommandExecError(msg) from err

    if retcode != 0:
        if not ignore_fail:
            err_dec = (stderr or stdout).decode()
            msg = f"An error occurred while running `{cmd_str}`: {err_dec}"
            raise RuntimeError(msg)
        LOGGER.debug("Ignoring failure of `%s`, retcode `%s`.", cmd_str, retcode)

    return stdout


def run_in_bash(command: str, *, workdir: ttypes.FileType = "") -> bytes:
    """Run command(s) in bash."""
    cmd = ["bash", "-o", "pipefail", "-c", command]
    return run_command(cmd, workdir=workdir)


@functools.cache
def get_current_commit() -> str:
    """Return the current git commit hash.

    Uses the `GIT_REVISION` env variable when set to a non-empty value, otherwise asks git.
    Git is run in the repo containing this module, independent of CWD. The result is
    cached for the lifetime of the process.
    """
    return (
        os.environ.get("GIT_REVISION")
        or run_command("git rev-parse HEAD", workdir=pl.Path(__file__).parent).decode().strip()
    )


# TODO: unify with the implementation in clusterlib
def get_rand_str(length: int = 8) -> str:
    """Return random string."""
    if length < 1:
        return ""
    return "".join(random.choices(string.ascii_lowercase, k=length))


# TODO: unify with the implementation in clusterlib
def prepend_flag(flag: str, contents: tp.Iterable[tp.Any]) -> list[str]:
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
    """Return `filename#L<lineno>` string for the given frame."""
    lineno = frame.f_lineno
    fpath = frame.f_globals["__file__"]
    line_str = f"{fpath}#L{lineno}"
    return line_str


def _get_calling_frame() -> tt.FrameType:
    """Return the frame of the caller of the function that invoked this helper.

    Raises:
        ValueError: When the calling frame is not available.
    """
    frame = inspect.currentframe()
    calling_frame = frame.f_back.f_back if frame and frame.f_back else None

    if calling_frame is None:
        msg = "Couldn't get the calling frame."
        raise ValueError(msg)

    return calling_frame


def get_current_line_str() -> str:
    """Get `filename#L<lineno>` of current line.

    NOTE: Reports the location of the immediate caller. Calling through any wrapper
    (decorator, context manager, `functools.partial`) reports the wrapper's location instead.
    """
    return get_line_str_from_frame(frame=_get_calling_frame())


def get_vcs_link() -> str:
    """Return link to the current line in GitHub."""
    line_str = get_line_str_from_frame(frame=_get_calling_frame())
    repo_idx = line_str.find("cardano_node_tests")
    if repo_idx == -1:
        msg = f"Couldn't find the repo location in '{line_str}'."
        raise ValueError(msg)
    url = f"{GITHUB_URL}/blob/{get_current_commit()}/{line_str[repo_idx:]}"
    return url


def checksum(filename: ttypes.FileType) -> str:
    """Return file checksum."""
    with open(filename, "rb") as f:
        return hashlib.file_digest(f, hashlib.blake2b).hexdigest()


def write_json(*, out_file: ttypes.FileType, content: dict) -> ttypes.FileType:
    """Write dictionary content to JSON file."""
    with open(pl.Path(out_file).expanduser(), "w", encoding="utf-8") as out_fp:
        json.dump(content, out_fp, indent=4)
    return out_file


def decode_bech32(bech32: str) -> str:
    """Convert from bech32 string."""
    return run_command(["bech32"], stdin_data=f"{bech32}\n".encode()).decode().strip()


def encode_bech32(*, prefix: str, data: str) -> str:
    """Convert to bech32 string."""
    return run_command(["bech32", prefix], stdin_data=f"{data}\n".encode()).decode().strip()


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

    Keep the original path (with `~` expanded) instead of resolving it to absolute.
    """
    if not dir_path:
        return None
    orig_path = pl.Path(dir_path).expanduser()
    abs_path = orig_path.resolve()
    if not (abs_path.exists() and abs_path.is_dir()):
        msg = f"check_dir_arg_keep: directory '{dir_path}' doesn't exist"
        raise argparse.ArgumentTypeError(msg)
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


def is_in_interval(num1: float, num2: float, *, frac: float = 0.1) -> bool:
    """Check that the num1 is in the interval defined by num2 and its fraction."""
    num2_frac = abs(num2 * frac)
    _min = num2 - num2_frac
    _max = num2 + num2_frac
    return _min <= num1 <= _max


@functools.lru_cache(maxsize=100)
def tool_has(command: str) -> bool:
    """Check if a tool has a subcommand or argument available.

    E.g. `tool_has("cardano-cli legacy governance")`

    Raises:
        CommandExecError: When the tool cannot be executed at all, as its
            availability cannot be determined in that case.
    """
    try:
        run_command(command)
    except CommandExecError:
        raise
    except RuntimeError as err:
        err_str = str(err)
    else:
        return True

    # Strip the "An error occurred while running `...`:" prefix added by `run_command`.
    # Split on the exact "`: " delimiter so a colon inside the command string cannot
    # select the wrong segment. Fall back to the whole message when the prefix is missing.
    cmd_err = (err_str.partition("`: ")[2] or err_str).strip()
    return not cmd_err.startswith("Invalid")


def flatten(
    iterable: tp.Iterable[tp.Any], *, ltypes: type[tp.Iterable[tp.Any]] | None = None
) -> tp.Generator[tp.Any]:
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
    # E.g. "rewardAccount" -> "spsRewardAccount"
    if key.startswith("sps"):
        sps_key = key
        old_key = f"{key[3:4].lower()}{key[4:]}"
    else:
        sps_key = f"sps{key[:1].upper()}{key[1:]}"
        old_key = key

    val = pool_params.get(sps_key)
    if val is None:
        # Try to use old key name
        val = pool_params.get(old_key)

    return val


def check_cardano_node_socket_path() -> None:
    """Check that `CARDANO_NODE_SOCKET_PATH` value is valid for use by testing framework."""
    socket_env = os.environ.get("CARDANO_NODE_SOCKET_PATH")
    if not socket_env:
        msg = "The `CARDANO_NODE_SOCKET_PATH` env variable is not set."
        raise ValueError(msg)

    socket_path = pl.Path(socket_env).expanduser().resolve()
    parts = socket_path.parts
    if (
        len(parts) < 2
        or not parts[-2].startswith("state-cluster")
        or parts[-1]
        not in (
            "bft1.socket",
            "relay1.socket",
        )
    ):
        msg = "The `CARDANO_NODE_SOCKET_PATH` value is not valid for use by testing framework."
        raise ValueError(msg)
