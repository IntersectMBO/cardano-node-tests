"""Wrapper for marlowe-cli for working with cardano cluster."""
import base64
import datetime
import functools
import itertools
import json
import logging
import os
import random
import string
import subprocess
import time
import warnings
from pathlib import Path
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union

from cardano_clusterlib import clusterlib

LOGGER = logging.getLogger(__name__)


class CLIError(Exception):
    pass


def record_cli_coverage(cli_args: List[str], coverage_dict: dict) -> None:
    """Record coverage info for CLI commands.

    Args:
        cli_args: A list of command and it's arguments.
        coverage_dict: A dictionary with coverage info.
    """
    parent_dict = coverage_dict
    prev_arg = ""
    for arg in cli_args:
        # if the current argument is a parameter to an option, skip it
        if prev_arg.startswith("--") and not arg.startswith("--"):
            continue
        prev_arg = arg

        cur_dict = parent_dict.get(arg)
        # initialize record if it doesn't exist yet
        if not cur_dict:
            parent_dict[arg] = {"_count": 0}
            cur_dict = parent_dict[arg]

        # increment count
        cur_dict["_count"] += 1

        # set new parent dict
        if not arg.startswith("--"):
            parent_dict = cur_dict


class CLIOut(NamedTuple):
    stdout: bytes
    stderr: bytes


class MarloweCliWrapper:
    """Methods for working with cardano cluster using `marlowe-cli`..

    Attributes:
        cluster_obj: Cluster Object configured with `cardano-cli` wrapper
        state_dir: A directory with cluster state files (keys, config files, logs, ...).
        protocol: A cluster protocol - full cardano mode by default.
        tx_era: An era used for transactions, by default same as network Era.
        slots_offset: Difference in slots between cluster's start era and current era
            (e.g. Byron->Mary)
    """

    def _write_cli_log(self, command: str) -> None:
        if not self._cli_log:
            return

        with open(self._cli_log, "a", encoding="utf-8") as logfile:
            logfile.write(f"{datetime.datetime.now()}: {command}\n")


    def cli_base(self, cli_args: List[str]) -> CLIOut:
        """Run a command.

        Args:
            cli_args: A list consisting of command and it's arguments.

        Returns:
            CLIOut: A tuple containing command stdout and stderr.
        """
        cmd_str = " ".join(cli_args)
        LOGGER.debug("Running `%s`", cmd_str)
        self._write_cli_log(cmd_str)

        # re-run the command when running into
        # Network.Socket.connect: <socket: X>: resource exhausted (Resource temporarily unavailable)
        # or
        # MuxError (MuxIOException writev: resource vanished (Broken pipe)) "(sendAll errored)"
        for __ in range(3):
            with subprocess.Popen(cli_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
                stdout, stderr = p.communicate()

                if p.returncode == 0:
                    break

            stderr_dec = stderr.decode()
            err_msg = (
                f"An error occurred running a CLI command `{cmd_str}` on path "
                f"`{os.getcwd()}`: {stderr_dec}"
            )
            if "resource exhausted" in stderr_dec or "resource vanished" in stderr_dec:
                LOGGER.error(err_msg)
                time.sleep(0.4)
                continue
            raise CLIError(err_msg)
        else:
            raise CLIError(err_msg)

        return CLIOut(stdout or b"", stderr or b"")

    def cli(self, cli_args: List[str]) -> CLIOut:
        """Run the `cardano-cli` command.

        Args:
            cli_args: A list of arguments for cardano-cli.

        Returns:
            CLIOut: A tuple containing command stdout and stderr.
        """
        cmd = ["cardano-cli", *cli_args]
        record_cli_coverage(cli_args=cmd, coverage_dict=self.cli_coverage)
        return self.cli_base(cmd)

