import contextlib
import logging
import random

import cardano_clusterlib.types as cl_types
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)


def fund_from_faucet(
    *dst_addrs: clusterlib.AddressRecord | clusterlib.PoolUser,
    cluster_obj: clusterlib.ClusterLib,
    faucet_data: dict | None = None,
    all_faucets: dict[str, dict] | None = None,
    amount: None | int | list[int] = None,
    tx_name: str | None = None,
    destination_dir: clusterlib.FileType = ".",
    force: bool = False,
) -> clusterlib.TxRawOutput | None:
    """Send `amount` from faucet addr to all `dst_addrs`."""
    if amount is None:
        amount = 1000_000_000

    if not (faucet_data or all_faucets):
        msg = "Either `faucet_data` or `all_faucets` must be provided."
        raise AssertionError(msg)

    # Get payment AddressRecord out of PoolUser
    dst_addr_records: list[clusterlib.AddressRecord] = [
        (r.payment if hasattr(r, "payment") else r)
        for r in dst_addrs  # type: ignore
    ]
    if isinstance(amount, int):
        amount = [amount] * len(dst_addr_records)

    fund_txouts = [
        clusterlib.TxOut(address=d.address, amount=a)
        for d, a in zip(dst_addr_records, amount)
        if force or cluster_obj.g_query.get_address_balance(d.address) < a
    ]
    if not fund_txouts:
        return None

    if not faucet_data and all_faucets:
        # Randomly select one of the "user" faucets
        all_user_keys = [k for k in all_faucets if k.startswith("user")]
        selected_user_key = random.choice(all_user_keys)
        faucet_data = all_faucets[selected_user_key]

    assert faucet_data
    src_address = faucet_data["payment"].address
    with locking.FileLockIfXdist(f"{temptools.get_basetemp()}/{src_address}.lock"):
        tx_name = tx_name or helpers.get_timestamped_rand_str()
        tx_name = f"{tx_name}_funding"
        fund_tx_files = clusterlib.TxFiles(signing_key_files=[faucet_data["payment"].skey_file])

        tx_raw_output = cluster_obj.g_transaction.send_tx(
            src_address=src_address,
            tx_name=tx_name,
            txouts=fund_txouts,
            tx_files=fund_tx_files,
            destination_dir=destination_dir,
        )

    return tx_raw_output


def return_funds_to_faucet(
    *src_addrs: clusterlib.AddressRecord,
    cluster_obj: clusterlib.ClusterLib,
    faucet_addr: str,
    amount: int | list[int] = -1,
    tx_name: str | None = None,
    destination_dir: cl_types.FileType = ".",
) -> None:
    """Send `amount` from all `src_addrs` to `faucet_addr`.

    The amount of "-1" means all available funds.
    """
    tx_name = tx_name or helpers.get_timestamped_rand_str()
    tx_name = f"{tx_name}_return_funds"
    if isinstance(amount, int):
        amount = [amount] * len(src_addrs)

    with locking.FileLockIfXdist(f"{temptools.get_basetemp()}/{faucet_addr}.lock"):
        try:
            logging.disable(logging.ERROR)
            for addr, amount_rec in zip(src_addrs, amount):
                fund_txouts = [clusterlib.TxOut(address=faucet_addr, amount=amount_rec)]
                fund_tx_files = clusterlib.TxFiles(signing_key_files=[addr.skey_file])
                # Try to return funds; don't mind if there's not enough funds for fees etc.
                with contextlib.suppress(Exception):
                    cluster_obj.g_transaction.send_tx(
                        src_address=addr.address,
                        tx_name=tx_name,
                        txouts=fund_txouts,
                        tx_files=fund_tx_files,
                        destination_dir=destination_dir,
                    )
        finally:
            logging.disable(logging.NOTSET)
