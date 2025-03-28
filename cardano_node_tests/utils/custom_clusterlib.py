"""Custom `ClusterLib` extended with functionality that is useful for testing."""

import logging
import os
import pathlib as pl

from cardano_clusterlib import clusterlib
from cardano_clusterlib import transaction_group

LOGGER = logging.getLogger(__name__)


def create_submitted_file(tx_file: clusterlib.FileType) -> None:
    """Create a `.submitted` status file when the Tx was successfully submitted."""
    tx_path = pl.Path(tx_file)
    submitted_symlink = tx_path.with_name(f"{tx_path.name}.submitted")
    try:
        relative_target = os.path.relpath(tx_path, start=submitted_symlink.parent)
        submitted_symlink.symlink_to(relative_target)
    except OSError as exc:
        LOGGER.warning(f"Cannot create symlink '{submitted_symlink}' -> '{relative_target}': {exc}")


class ClusterLib(clusterlib.ClusterLib):
    @property
    def g_transaction(self) -> transaction_group.TransactionGroup:
        """Transaction group."""
        if not self._transaction_group:
            self._transaction_group = TransactionGroup(clusterlib_obj=self)
        return self._transaction_group


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
