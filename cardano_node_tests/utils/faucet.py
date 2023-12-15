import logging
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)


def fund_from_faucet(
    *dst_addrs: tp.Union[clusterlib.AddressRecord, clusterlib.PoolUser],
    cluster_obj: clusterlib.ClusterLib,
    faucet_data: dict,
    amount: tp.Union[int, tp.List[int]] = 1000_000_000,
    tx_name: tp.Optional[str] = None,
    destination_dir: clusterlib.FileType = ".",
    force: bool = False,
) -> tp.Optional[clusterlib.TxRawOutput]:
    """Send `amount` from faucet addr to all `dst_addrs`."""
    # get payment AddressRecord out of PoolUser
    dst_addr_records: tp.List[clusterlib.AddressRecord] = [
        (r.payment if hasattr(r, "payment") else r) for r in dst_addrs  # type: ignore
    ]
    if isinstance(amount, int):
        amount = [amount] * len(dst_addr_records)

    fund_dst = [
        clusterlib.TxOut(address=d.address, amount=a)
        for d, a in zip(dst_addr_records, amount)
        if force or cluster_obj.g_query.get_address_balance(d.address) < a
    ]
    if not fund_dst:
        return None

    src_address = faucet_data["payment"].address
    with locking.FileLockIfXdist(f"{temptools.get_basetemp()}/{src_address}.lock"):
        tx_name = tx_name or helpers.get_timestamped_rand_str()
        tx_name = f"{tx_name}_funding"
        fund_tx_files = clusterlib.TxFiles(signing_key_files=[faucet_data["payment"].skey_file])

        tx_raw_output = cluster_obj.g_transaction.send_funds(
            src_address=src_address,
            destinations=fund_dst,
            tx_name=tx_name,
            tx_files=fund_tx_files,
            destination_dir=destination_dir,
        )

    return tx_raw_output
