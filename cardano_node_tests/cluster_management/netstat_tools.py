"""Functions based on `netstat`."""

import logging
import os
import re
import time
import typing as tp

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


def get_netstat_out() -> str:
    """Get output of the `netstat` command."""
    try:
        return helpers.run_command(
            "netstat -pant | grep -E 'LISTEN|TIME_WAIT'", ignore_fail=True, shell=True
        ).decode()
    except Exception as excp:
        LOGGER.error(f"Failed to fetch netstat output: {excp}")  # noqa: TRY400
        return ""


def kill_old_cluster(instance_num: int, log_func: tp.Callable[[str], None]) -> None:  # noqa: C901
    """Attempt to kill all processes left over from a previous cluster instance."""

    def _get_netstat_split() -> list[str]:
        return get_netstat_out().replace("\t", "    ").splitlines()

    def _get_pid(line: str) -> int | None:
        try:
            pid_str = line.strip().split()[-1].split("/")[0]
            return int(pid_str)
        except (IndexError, ValueError):
            return None

    def _try_kill(pid: int) -> None:
        try:
            os.kill(pid, 15)
        except Exception as excp:
            log_func(f"Failed to kill leftover process PID {pid}: {excp}")
            return

    def _get_proc_cmdline(pid: int) -> str:
        try:
            with open(f"/proc/{pid}/cmdline") as f:
                cmdline = f.read().replace("\0", " ").strip()
        except Exception:
            cmdline = ""

        return cmdline

    port_nums = cluster_nodes.get_cluster_type().cluster_scripts.get_instance_ports(instance_num)
    port_strs = [
        # Add whitestpace to the end of each port number to avoid matching a port number that is a
        # prefix of another port number.
        f":{p} "
        for p in (
            port_nums.supervisor,
            port_nums.webserver,
            port_nums.submit_api,
            *port_nums.node_ports,
        )
    ]

    # Attempt to kill the `supervisord` process first. If successful, this will also kill all the
    # processes started by supervisor.
    port_supervisor_str = port_strs[0]
    for line in _get_netstat_split():
        if port_supervisor_str not in line:
            continue
        pid = _get_pid(line)
        if pid:
            log_func(f"Killing supervisor process: PID {pid}")
            _try_kill(pid)
        time.sleep(5)
        break

    # Kill all the leftover processes, if possible, and wait for them to finish
    ports_re = re.compile(r"|".join(re.escape(p) for p in port_strs))
    for _ in range(5):
        found = False
        for line in _get_netstat_split():
            if not ports_re.search(line):
                continue
            found = True
            pid = _get_pid(line)
            if pid:
                cmdline = _get_proc_cmdline(pid)
                log_func(f"Killing leftover process: PID {pid}; cmdline: {cmdline}")
                _try_kill(pid)
            time.sleep(5)
            break
        if not found:
            break
