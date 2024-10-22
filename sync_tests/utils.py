import json
import os
import shutil
import platform
import subprocess
import zipfile
import tarfile
from datetime import datetime
from typing import NamedTuple
from typing import List
from typing import Union
from pathlib import Path

import psutil
import time

from colorama import Fore, Style



class CLIOut(NamedTuple):
    stdout: bytes
    stderr: bytes


def run_cardano_cli(cli_args: List[str]) -> CLIOut:
    """Run the `cardano-cli` command.
    Args:
        cli_args: A list of arguments for cardano-cli.
    Returns:
        CLIOut: A tuple containing command stdout and stderr.
    """
    cli_args_strs = [str(arg) for arg in cli_args]
    cmd_str = " ".join(cli_args_strs)
    print(f"Running `{cmd_str}`")

    # re-run the command when running into
    # Network.Socket.connect: <socket: X>: resource exhausted (Resource temporarily unavailable)
    # or
    # MuxError (MuxIOException writev: resource vanished (Broken pipe)) "(sendAll errored)"
    for __ in range(3):
        retcode = None
        with subprocess.Popen(
            cli_args_strs, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ) as p:
            stdout, stderr = p.communicate()
            retcode = p.returncode

        if retcode == 0:
            break

        stderr_dec = stderr.decode()
        err_msg = (
            f"An error occurred running a CLI command `{cmd_str}` on path "
            f"`{Path.cwd()}`: {stderr_dec}"
        )
        if "resource exhausted" in stderr_dec or "resource vanished" in stderr_dec:
            print(err_msg)
            time.sleep(0.4)
            continue
        raise RuntimeError(err_msg)
    else:
        raise RuntimeError(err_msg)

    return CLIOut(stdout or b"", stderr or b"")


def run_command(
    command: Union[str, list],
    ignore_fail: bool = False,
    shell: bool = False,
) -> CLIOut:
    """Run command."""
    cmd: Union[str, list]
    if isinstance(command, str):
        cmd = command if shell else command.split()
    else:
        cmd = command

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=shell)
    stdout, stderr = p.communicate()

    if not ignore_fail and p.returncode != 0:
        err_dec = stderr.decode()
        err_dec = err_dec or stdout.decode()
        raise RuntimeError(f"An error occurred while running `{command}`: {err_dec}")

    return CLIOut(stdout or b"", stderr or b"")


def cli_has(command: str) -> bool:
    """Check if a cardano-cli subcommand or argument is available.
    E.g. `cli_has("query leadership-schedule --next")`
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


def print_ok(message):
    print(Fore.GREEN + f"{message}", Style.RESET_ALL, flush=True)


def print_info(message):
    print(Fore.BLUE + f"{message}", Style.RESET_ALL, flush=True)


def print_warn(message):
    print(Fore.YELLOW + f"{message}", Style.RESET_ALL, flush=True)


def print_info_warn(message):
    print(Fore.LIGHTMAGENTA_EX + f"{message}", Style.RESET_ALL, flush=True)

def print_error(message):
    print(Fore.RED + f"{message}", Style.RESET_ALL, flush=True)


def date_diff_in_seconds(dt2, dt1):
    # dt1 and dt2 should be datetime types
    timedelta = dt2 - dt1
    return int(timedelta.days * 24 * 3600 + timedelta.seconds)


def seconds_to_time(seconds_val):
    mins, secs = divmod(seconds_val, 60)
    hour, mins = divmod(mins, 60)
    return "%d:%02d:%02d" % (hour, mins, secs)


def get_os_type():
    return [platform.system(), platform.release(), platform.version()]


def get_no_of_cpu_cores():
    return os.cpu_count()


def get_total_ram_in_GB():
    return int(psutil.virtual_memory().total / 1000000000)


def show_percentage(part, whole):
    return round(100 * float(part) / float(whole), 2)


def get_current_date_time():
    now = datetime.now()
    return now.strftime("%d/%m/%Y %H:%M:%S")


def get_file_creation_date(path_to_file):
    return time.ctime(os.path.getmtime(path_to_file))


def print_file_content(file_name: str) -> None:
    try:
        with open(file_name, 'r') as file:
            content = file.read()
            print(content)
    except FileNotFoundError:
        print(f"File '{file_name}' not found.")
    except Exception as e:
        print(f"An error occurred while reading the file: {e}")


def is_dir(dir):
    return os.path.isdir(dir)


def list_absolute_file_paths(directory):
    files_paths = []
    for dirpath,_,filenames in os.walk(directory):
        for f in filenames:
            abs_filepath = os.path.abspath(os.path.join(dirpath, f))
            files_paths.append(abs_filepath)
    return files_paths


def get_directory_size(start_path='.'):
    # returns directory size in bytes
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(start_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    return total_size


def zip_file(archive_name, file_name):
    with zipfile.ZipFile(archive_name, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zip:
        zip.write(file_name)


def unzip_file(file_name):
    with zipfile.ZipFile(file_name, 'r') as zip:
        zip.printdir()

        print(f"Extracting all the files from {file_name}...")
        zip.extractall()


def unpack_archive(archive_path):
    """Unpack the archive based on its format."""
    print(f"Attempting to unpack archive: {archive_path}")

    if archive_path.endswith('.zip'):
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            print(f"Contents of zip: {zip_ref.namelist()}")
            zip_ref.extractall('.')
            print(f"Extracted {archive_path} to current directory.")
    elif archive_path.endswith('.tar.gz'):
        with tarfile.open(archive_path, 'r:gz') as tar_ref:
            print(f"Contents of tar.gz: {tar_ref.getnames()}")
            tar_ref.extractall('.')
            print(f"Extracted {archive_path} to current directory.")
    elif archive_path.endswith('.tar'):
        with tarfile.open(archive_path, 'r:') as tar_ref:
            print(f"Contents of tar: {tar_ref.getnames()}")
            tar_ref.extractall('.')
            print(f"Extracted {archive_path} to current directory.")
    else:
        raise ValueError(f"Unsupported archive format: {archive_path}")


def delete_file(file_path):
    # file_path should be a Path (pathlib object)
    try:
        file_path.unlink()
    except OSError as e:
        print_error(f"Error: {file_path} : {e.strerror}")
