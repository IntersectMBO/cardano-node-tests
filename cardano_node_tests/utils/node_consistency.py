"""Checks of blockchain consistency across the cluster nodes."""

import logging
import time

from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils

LOGGER = logging.getLogger(__name__)


def _get_nodes_missing_utxos(
    cluster_obj: clusterlib.ClusterLib,
    utxos: list[clusterlib.UTXOData],
) -> set[str]:
    """Return set of nodes that don't have the given UTxOs."""
    missing_nodes: set[str] = set()

    known_nodes = cluster_nodes.get_cluster_type().NODES
    # Skip the check if there is only one node
    if len(known_nodes) <= 1:
        return missing_nodes

    instance_num = cluster_nodes.get_instance_num()

    # Check if all nodes know about the UTxO
    try:
        for node in known_nodes:
            # Set 'CARDANO_NODE_SOCKET_PATH' to point to socket of the selected node
            cluster_nodes.set_cluster_env(
                instance_num=instance_num, socket_file_name=f"{node}.socket"
            )

            if not cluster_obj.g_query.get_utxo(utxo=utxos):
                missing_nodes.add(node)
    finally:
        # Restore 'CARDANO_NODE_SOCKET_PATH' to original value
        cluster_nodes.set_cluster_env(instance_num=instance_num)

    return missing_nodes


def check_missing_utxos(
    cluster_obj: clusterlib.ClusterLib,
    utxos: list[clusterlib.UTXOData],
) -> None:
    """Fail if any node is missing the given UTxOs."""
    missing_nodes = _get_nodes_missing_utxos(cluster_obj=cluster_obj, utxos=utxos)

    if missing_nodes:
        msg = f"Following nodes are missing the given UTxOs: {sorted(missing_nodes)}"
        raise AssertionError(msg)


def _detect_fork(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
) -> tuple[set[str], set[str]]:
    """Detect if one or more nodes have forked blockchain or is out of sync."""
    forked_nodes: set[str] = set()
    unsynced_nodes: set[str] = set()

    known_nodes = cluster_nodes.get_cluster_type().NODES
    if len(known_nodes) <= 1:
        LOGGER.warning("WARNING: Not enough nodes available to detect forks, skipping the check.")
        return forked_nodes, unsynced_nodes

    instance_num = cluster_nodes.get_instance_num()

    # Create a UTxO
    payment_rec = cluster_obj.g_address.gen_payment_addr_and_keys(
        name=f"{temp_template}_fork",
    )
    tx_raw_output = clusterlib_utils.fund_from_faucet(
        payment_rec,
        cluster_obj=cluster_obj,
        all_faucets=cluster_manager.cache.addrs_data,
        amount=2_000_000,
        tx_name=f"{temp_template}_fork",
    )
    assert tx_raw_output
    utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_raw_output)

    # Check if all nodes know about the UTxO
    try:
        for node in known_nodes:
            # Set 'CARDANO_NODE_SOCKET_PATH' to point to socket of the selected node
            cluster_nodes.set_cluster_env(
                instance_num=instance_num, socket_file_name=f"{node}.socket"
            )

            for __ in range(5):
                if float(cluster_obj.g_query.get_tip()["syncProgress"]) == 100:
                    break
                time.sleep(1)
            else:
                unsynced_nodes.add(node)
                continue

            if not cluster_obj.g_query.get_utxo(utxo=utxos):
                forked_nodes.add(node)
    finally:
        # Restore 'CARDANO_NODE_SOCKET_PATH' to original value
        cluster_nodes.set_cluster_env(instance_num=instance_num)

    # Forked nodes are the ones that differ from the majority of nodes
    if forked_nodes and len(forked_nodes) > (len(known_nodes) // 2):
        forked_nodes = set(known_nodes - forked_nodes)

    return forked_nodes, unsynced_nodes


def fail_on_fork(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
) -> None:
    """Fail if one or more nodes have forked blockchain or is out of sync."""
    forked_nodes, unsynced_nodes = _detect_fork(
        cluster_manager=cluster_manager, cluster_obj=cluster_obj, temp_template=temp_template
    )

    err_msg = []

    if forked_nodes:
        err_msg.append(f"Following nodes appear to have forked blockchain: {sorted(forked_nodes)}")
    if unsynced_nodes:
        err_msg.append(f"Following nodes appear to be out of sync: {sorted(unsynced_nodes)}")

    if err_msg:
        # The local cluster needs to be respun before it is usable again
        cluster_manager.set_needs_respin()
        raise AssertionError("\n".join(err_msg))
