"""Custom `ClusterLib` extended with functionality that is useful for testing."""

import logging
import os
import pathlib as pl
import typing as tp

from cardano_clusterlib import clusterlib
from cardano_clusterlib import consts
from cardano_clusterlib import transaction_group

LOGGER = logging.getLogger(__name__)


def record_cli_coverage(cli_args: list[str], coverage_dict: dict) -> None:
    """Record coverage info for CLI commands.

    Args:
        cli_args: A list of command and it's arguments.
        coverage_dict: A dictionary with coverage info.
    """
    parent_dict = coverage_dict
    prev_arg = ""
    for arg in cli_args:
        # If the current argument is a subcommand marker, record it and skip it
        if arg == consts.SUBCOMMAND_MARK:
            prev_arg = arg
            continue

        # If the current argument is a parameter to an option, skip it
        if prev_arg.startswith("--") and not arg.startswith("--"):
            continue

        prev_arg = arg

        cur_dict = parent_dict.get(arg)
        # Initialize record if it doesn't exist yet
        if not cur_dict:
            parent_dict[arg] = {"_count": 0}
            cur_dict = parent_dict[arg]

        # Increment count
        cur_dict["_count"] += 1

        # Set new parent dict
        if not arg.startswith("--"):
            parent_dict = cur_dict


def create_submitted_file(tx_file: clusterlib.FileType) -> None:
    """Create a `.submitted` status file when the Tx was successfully submitted."""
    tx_path = pl.Path(tx_file)
    submitted_symlink = tx_path.with_name(f"{tx_path.name}.submitted")
    try:
        relative_target = os.path.relpath(tx_path, start=submitted_symlink.parent)
        submitted_symlink.symlink_to(relative_target)
    except OSError as exc:
        err = f"Cannot create symlink '{submitted_symlink}' -> '{relative_target}'"
        raise RuntimeError(err) from exc


class ClusterLib(clusterlib.ClusterLib):
    def __init__(
        self,
        state_dir: clusterlib.FileType,
        slots_offset: int | None = None,
        socket_path: clusterlib.FileType = "",
        command_era: str = consts.CommandEras.LATEST,
    ) -> None:
        super().__init__(
            state_dir=state_dir,
            slots_offset=slots_offset,
            socket_path=socket_path,
            command_era=command_era,
        )
        self.cli_coverage: dict[str, tp.Any] = {}

    @property
    def g_transaction(self) -> transaction_group.TransactionGroup:
        """Transaction group."""
        if not self._transaction_group:
            self._transaction_group = TransactionGroup(clusterlib_obj=self)
        return self._transaction_group

    def cli(
        self,
        cli_args: list[str],
        timeout: float | None = None,
        add_default_args: bool = True,
    ) -> clusterlib.CLIOut:
        """Run the `cardano-cli` command.

        Args:
            cli_args: A list of arguments for cardano-cli.
            timeout: A timeout for the command, in seconds (optional).
            add_default_args: Whether to add default arguments to the command (optional).

        Returns:
            clusterlib.CLIOut: A data container containing command stdout and stderr.
        """
        cli_args_strs_all = [str(arg) for arg in cli_args]
        if add_default_args:
            cli_args_strs_all.insert(0, "cardano-cli")
            cli_args_strs_all.insert(1, self.command_era)

        record_cli_coverage(cli_args=cli_args_strs_all, coverage_dict=self.cli_coverage)

        return super().cli(cli_args=cli_args, timeout=timeout, add_default_args=add_default_args)


class TransactionGroup(transaction_group.TransactionGroup):
    def submit_tx(
        self,
        tx_file: clusterlib.FileType,
        txins: list[clusterlib.UTXOData],
        wait_blocks: int | None = None,
    ) -> str:
        """Create a `.submitted` status file when the Tx was successfully submitted."""
        txid = super().submit_tx(tx_file=tx_file, txins=txins, wait_blocks=wait_blocks)
        create_submitted_file(tx_file=tx_file)
        return txid
