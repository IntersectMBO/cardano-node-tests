"""Checks of blockchain consistency across the cluster nodes."""

import dataclasses
import logging
import time
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils

LOGGER = logging.getLogger(__name__)

# Number of blocks to wait for the tx after all nodes reached the same chain tip
_CONVERGED_WAIT_BLOCKS = 3


@dataclasses.dataclass(frozen=True)
class _NodeTxState:
    """State of a node relevant for checking presence of a transaction."""

    tip_hash: str | None
    block: int | None
    has_tx: bool


def _get_nodes_tx_state(
    cluster_obj: clusterlib.ClusterLib,
    txin: str,
    nodes: tp.Collection[str],
) -> dict[str, _NodeTxState]:
    """Return the tip and presence of the given transaction output for each of the given nodes.

    The tip is queried before and after the UTxO query. When the tip changes in the meantime,
    the tip is recorded as `None`, as it is not clear which ledger state the UTxO query ran
    against.
    """
    instance_num = cluster_nodes.get_instance_num()
    node_states: dict[str, _NodeTxState] = {}

    try:
        for node in nodes:
            # Set 'CARDANO_NODE_SOCKET_PATH' to point to socket of the selected node
            cluster_nodes.set_cluster_env(
                instance_num=instance_num, socket_file_name=f"{node}.socket"
            )

            tip_before = cluster_obj.g_query.get_tip()
            has_tx = bool(cluster_obj.g_query.get_utxo(txin=txin))
            tip_after = cluster_obj.g_query.get_tip()

            tip_hash = tip_after.get("hash")
            if tip_hash != tip_before.get("hash"):
                tip_hash = None

            node_states[node] = _NodeTxState(
                tip_hash=tip_hash, block=tip_after.get("block"), has_tx=has_tx
            )
    finally:
        # Restore 'CARDANO_NODE_SOCKET_PATH' to original value
        cluster_nodes.set_cluster_env(instance_num=instance_num)

    return node_states


def _format_node_states(node_states: dict[str, _NodeTxState]) -> str:
    """Return human readable representation of the nodes state."""
    return "\n".join(
        f"  {node}: block {s.block}, tip {s.tip_hash}, has tx: {s.has_tx}"
        for node, s in sorted(node_states.items())
    )


def _is_forked(node_states: dict[str, _NodeTxState]) -> bool:
    """Check if some nodes have different blocks at the same block height.

    Different tips at different heights can be just a delay in block propagation, so these are
    not considered a fork.
    """
    tips_per_block: dict[int | None, set[str]] = {}
    for s in node_states.values():
        if s.tip_hash is not None:
            tips_per_block.setdefault(s.block, set()).add(s.tip_hash)
    return any(len(tips) > 1 for tips in tips_per_block.values())


def _wait_for_tx(
    cluster_obj: clusterlib.ClusterLib,
    txid: str,
    nodes: tp.Collection[str],
    timeout: float,
) -> None:
    """Wait for the tx to be on all nodes, fail if it doesn't happen after the fork is resolved."""
    txin = f"{txid}#0"
    converged_block: int | None = None
    end_time = time.monotonic() + timeout

    while True:
        time.sleep(2)
        node_states = _get_nodes_tx_state(cluster_obj=cluster_obj, txin=txin, nodes=nodes)
        if all(s.has_tx for s in node_states.values()):
            LOGGER.info("The tx `%s` is present on all nodes.", txid)
            return

        tip_hashes = {s.tip_hash for s in node_states.values()}
        if len(tip_hashes) == 1 and None not in tip_hashes:
            # All nodes agree on the same chain tip, so the fork is resolved (if there was any).
            # The tx can still get into a block from a mempool of a node on the winning chain,
            # so wait for a couple more blocks before failing.
            block = next(iter(node_states.values())).block or 0
            if converged_block is None:
                converged_block = block
            elif block >= converged_block + _CONVERGED_WAIT_BLOCKS:
                break
        elif _is_forked(node_states=node_states):
            # The nodes forked again, start counting the blocks from the next convergence
            converged_block = None

        if time.monotonic() >= end_time:
            break

    if converged_block is not None:
        msg = (
            f"The tx `{txid}` is missing even after all nodes reached the same chain tip:\n"
            f"{_format_node_states(node_states)}"
        )
    else:
        msg = (
            f"The tx `{txid}` is missing on some nodes and the nodes didn't reach the same "
            f"chain tip in {timeout:.0f} seconds:\n{_format_node_states(node_states)}"
        )
    raise AssertionError(msg)


def check_tx_on_all_nodes(
    cluster_obj: clusterlib.ClusterLib,
    tx_raw_output: clusterlib.TxRawOutput,
    timeout: float | None = None,
) -> None:
    """Fail if the given transaction is not on the chain of all nodes.

    Meant to be called right after the transaction was submitted, before its outputs are spent.
    The presence of the transaction is checked using its first output. Outputs of a transaction
    are either all on the chain or none, so checking a single output is enough.

    The transaction can be missing on some of the nodes because of a temporary fork. When it is
    missing, wait for the nodes to agree on the same chain tip (i.e. for the fork to be resolved)
    and for a couple more blocks, as the tx can still get into a block from a mempool.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        tx_raw_output: A data used when building the transaction (`clusterlib.TxRawOutput`).
        timeout: A time (in seconds) to wait for the tx to be on all nodes (optional), including
            the time needed for the fork to be resolved and the extra blocks after that.
            By default the time needed for producing 20 blocks on average, but at least
            60 seconds.

    Raises:
        AssertionError: If the transaction is still missing after the fork was resolved, or if
            the fork was not resolved in the given time.
    """
    known_nodes = cluster_nodes.get_cluster_type().NODES
    # Skip the check if there is only one node
    if len(known_nodes) <= 1:
        return

    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
    txin = f"{txid}#0"

    node_states = _get_nodes_tx_state(cluster_obj=cluster_obj, txin=txin, nodes=known_nodes)
    if all(s.has_tx for s in node_states.values()):
        return

    # Some node can be just a bit behind with block propagation, so retry quietly first
    time.sleep(max(1.0, cluster_obj.slot_length))
    node_states = _get_nodes_tx_state(cluster_obj=cluster_obj, txin=txin, nodes=known_nodes)
    if all(s.has_tx for s in node_states.values()):
        return

    if timeout is None:
        block_time = cluster_obj.slot_length / float(cluster_obj.genesis["activeSlotsCoeff"])
        timeout = max(60.0, 20 * block_time)

    LOGGER.warning(
        "Some nodes are missing the tx `%s`, possible fork, waiting up to %.0f seconds "
        "for the fork to be resolved:\n%s",
        txid,
        timeout,
        _format_node_states(node_states),
    )
    _wait_for_tx(cluster_obj=cluster_obj, txid=txid, nodes=known_nodes, timeout=timeout)


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
