"""Defragment address UTxOs."""

import logging
import pathlib as pl

from cardano_clusterlib import clusterlib

LOGGER = logging.getLogger(__name__)


def defragment(cluster_obj: clusterlib.ClusterLib, address: str, skey_file: pl.Path) -> None:
    """Defragment address UTxOs."""
    new_blocks = 3

    loop = 1
    utxos_len = -1
    while True:
        # Select UTxOs that are not locked and that contain only Lovelace
        utxos_all = cluster_obj.g_query.get_utxo(address=address)
        utxos_ids_excluded = {
            f"{u.utxo_hash}#{u.utxo_ix}"
            for u in utxos_all
            if u.coin != clusterlib.DEFAULT_COIN or u.datum_hash
        }
        utxos = [u for u in utxos_all if f"{u.utxo_hash}#{u.utxo_ix}" not in utxos_ids_excluded]

        prev_utxos_len, utxos_len = utxos_len, len(utxos)
        if prev_utxos_len <= utxos_len and loop >= 2:
            LOGGER.info("No more UTxOs to defragment.")
            break
        if utxos_len <= 10:
            break

        batch_size = min(100, utxos_len)
        batch_num = 1
        for b in range(0, utxos_len, batch_size):
            LOGGER.info(f"Defragmenting UTxOs: Running loop {loop}, batch {batch_num}")
            batch = utxos[b : b + batch_size]
            tx_name = f"defrag_loop{loop}_batch{batch_num}"

            tx_output = cluster_obj.g_transaction.build_tx(
                src_address=address,
                tx_name=tx_name,
                txins=batch,
                change_address=address,
            )
            tx_signed_file = cluster_obj.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                tx_name=tx_name,
                signing_key_files=[skey_file],
            )
            cluster_obj.g_transaction.submit_tx_bare(tx_file=tx_signed_file)
            batch_num += 1

        loop += 1

        LOGGER.info(
            f"Defragmenting UTxOs: Waiting for {new_blocks} new blocks before starting loop {loop}"
        )
        cluster_obj.wait_for_new_block(new_blocks=new_blocks)

    # Wait for the last defragmentation to be included in the chain
    cluster_obj.wait_for_new_block(new_blocks=new_blocks)
