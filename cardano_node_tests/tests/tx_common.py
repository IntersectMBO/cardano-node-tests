import logging
from pathlib import Path
from typing import List
from typing import Tuple

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

MIN_UTXO_VALUE = 857_690


def get_raw_tx_values(
    cluster_obj: clusterlib.ClusterLib,
    tx_name: str,
    src_record: clusterlib.AddressRecord,
    dst_record: clusterlib.AddressRecord,
    for_build_command: bool = False,
) -> clusterlib.TxRawOutput:
    """Get values for building raw TX using `clusterlib.build_raw_tx_bare`."""
    src_address = src_record.address
    dst_address = dst_record.address

    tx_files = clusterlib.TxFiles(signing_key_files=[src_record.skey_file])
    ttl = cluster_obj.g_transaction.calculate_tx_ttl()

    if for_build_command:
        fee = 0
        min_change = 1_500_000
    else:
        fee = cluster_obj.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=tx_name,
            dst_addresses=[dst_address],
            tx_files=tx_files,
            ttl=ttl,
        )
        min_change = 0

    src_addr_highest_utxo = cluster_obj.g_query.get_utxo_with_highest_amount(src_address)

    # use only the UTxO with the highest amount
    txins = [src_addr_highest_utxo]
    txouts = [
        clusterlib.TxOut(
            address=dst_address, amount=src_addr_highest_utxo.amount - fee - min_change
        ),
    ]
    out_file = Path(f"{helpers.get_timestamped_rand_str()}_tx.body")

    return clusterlib.TxRawOutput(
        txins=txins,
        txouts=txouts,
        txouts_count=1,
        tx_files=tx_files,
        out_file=out_file,
        fee=fee,
        build_args=[],
        invalid_hereafter=ttl,
    )


def get_txins_txouts(
    txins: List[clusterlib.UTXOData], txouts: List[clusterlib.TxOut]
) -> Tuple[List[str], List[str]]:
    txins_combined = [f"{x.utxo_hash}#{x.utxo_ix}" for x in txins]
    txouts_combined = [f"{x.address}+{x.amount}" for x in txouts]
    return txins_combined, txouts_combined
