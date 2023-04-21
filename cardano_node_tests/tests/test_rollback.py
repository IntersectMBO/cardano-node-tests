"""Tests for rollbacks.

In rollback tests, we split the cluster into two parts. We achieve this by changing topology
configuration.
"""
import logging
import os
import shutil
import time
from pathlib import Path
from typing import List
from typing import Optional

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

ROLLBACK_PAUSE = os.environ.get("ROLLBACK_PAUSE") is not None
ROLLBACK_NODES_OFFSET = int(os.environ.get("ROLLBACK_NODES_OFFSET") or 1)
LAST_POOL_NAME = f"pool{configuration.NUM_POOLS}"


@pytest.mark.skipif(
    cluster_nodes.get_cluster_type().type != cluster_nodes.ClusterType.LOCAL,
    reason="runs only on local cluster",
)
@pytest.mark.skipif(
    VERSIONS.cluster_era != VERSIONS.transaction_era,
    reason="runs only with same cluster and Tx era",
)
@pytest.mark.skipif(configuration.NUM_POOLS < 4, reason="`NUM_POOLS` must be at least 4")
@pytest.mark.skipif(
    configuration.MIXED_P2P, reason="Works only when all nodes have the same topology type"
)
class TestRollback:
    """Tests for rollbacks."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        cluster = cluster_singleton
        num_addrs = 4 if ROLLBACK_PAUSE else 3

        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[
                    f"addr_rollback_ci{cluster_manager.cluster_instance_num}_{i}"
                    for i in range(num_addrs)
                ],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # Fund source addresses
        clusterlib_utils.fund_from_faucet(
            *addrs,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )
        return addrs

    @pytest.fixture
    def split_topology_dir(self) -> Path:
        """Return path to directory with split topology files."""
        instance_num = cluster_nodes.get_instance_num()

        destdir = Path.cwd() / f"split_topology_ci{instance_num}"
        if destdir.exists():
            return destdir

        destdir.mkdir()

        cluster_nodes.get_cluster_type().cluster_scripts.gen_split_topology_files(
            destdir=destdir,
            instance_num=instance_num,
            offset=ROLLBACK_NODES_OFFSET,
        )

        return destdir

    @pytest.fixture
    def backup_topology(self) -> Path:
        """Backup the original topology files."""
        state_dir = cluster_nodes.get_cluster_env().state_dir
        topology_files = list(state_dir.glob("topology*.json"))

        backup_dir = state_dir / f"backup_topology_{helpers.get_rand_str()}"
        backup_dir.mkdir()

        # Copy topology files to backup dir
        for f in topology_files:
            shutil.copy(f, backup_dir / f.name)

        return backup_dir

    def split_cluster(self, split_topology_dir: Path) -> None:
        """Use the split topology files == split the cluster."""
        state_dir = cluster_nodes.get_cluster_env().state_dir
        topology_files = list(state_dir.glob("topology*.json"))

        prefix = "p2p-split" if configuration.ENABLE_P2P else "split"

        for f in topology_files:
            shutil.copy(split_topology_dir / f"{prefix}-{f.name}", f)

        cluster_nodes.restart_all_nodes()

    def restore_cluster(self, backup_topology: Path) -> None:
        """Restore the original topology files == restore the cluster."""
        state_dir = cluster_nodes.get_cluster_env().state_dir
        topology_files = list(state_dir.glob("topology*.json"))

        for f in topology_files:
            shutil.copy(backup_topology / f.name, f)

        cluster_nodes.restart_all_nodes()

    def node_query_utxo(
        self,
        cluster_obj: clusterlib.ClusterLib,
        node: str,
        address: str = "",
        tx_raw_output: Optional[clusterlib.TxRawOutput] = None,
    ) -> List[clusterlib.UTXOData]:
        """Query UTxO on given node."""
        orig_socket = os.environ.get("CARDANO_NODE_SOCKET_PATH")
        assert orig_socket
        new_socket = Path(orig_socket).parent / f"{node}.socket"

        try:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = str(new_socket)
            utxos = cluster_obj.g_query.get_utxo(address=address, tx_raw_output=tx_raw_output)
            return utxos
        finally:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = orig_socket

    def node_submit_tx(
        self,
        cluster_obj: clusterlib.ClusterLib,
        node: str,
        temp_template: str,
        src_addr: clusterlib.AddressRecord,
        dst_addr: clusterlib.AddressRecord,
    ) -> clusterlib.TxRawOutput:
        """Submit transaction on given node."""
        orig_socket = os.environ.get("CARDANO_NODE_SOCKET_PATH")
        assert orig_socket
        new_socket = Path(orig_socket).parent / f"{node}.socket"

        curr_time = time.time()
        destinations = [clusterlib.TxOut(address=dst_addr.address, amount=1_000_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        try:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = str(new_socket)
            tx_raw_output = cluster_obj.g_transaction.send_tx(
                src_address=src_addr.address,
                tx_name=f"{temp_template}_{int(curr_time)}",
                txouts=destinations,
                tx_files=tx_files,
            )
            return tx_raw_output
        finally:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = orig_socket

    def node_wait_for_block(
        self,
        cluster_obj: clusterlib.ClusterLib,
        node: str,
        block_no: int,
    ) -> int:
        """Wait for block number on given node."""
        orig_socket = os.environ.get("CARDANO_NODE_SOCKET_PATH")
        assert orig_socket
        new_socket = Path(orig_socket).parent / f"{node}.socket"

        try:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = str(new_socket)
            return cluster_obj.wait_for_block(block=block_no)
        finally:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = orig_socket

    @allure.link(helpers.get_vcs_link())
    def test_consensus_reached(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        backup_topology: Path,
        split_topology_dir: Path,
    ):
        """Test that global consensus is reached after rollback.

        The original cluster is split into two clusters, and before `securityParam`
        number of blocks is produced, the original cluster topology gets restored.

        * Submit Tx number 1
        * Split the cluster into two separate clusters
        * Check that the Tx number 1 exists on both clusters
        * Submit a Tx number 2 on the first cluster
        * Check that the Tx number 2 exists only on the first cluster
        * Submit a Tx number 3 on the second cluster
        * Check that the Tx number 3 exists only on the second cluster
        * Restore the cluster topology
        * Check that global consensus was restored
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)

        cluster1_socket = str(
            configuration.STARTUP_CARDANO_NODE_SOCKET_PATH.parent / "pool1.socket"
        )
        cluster2_socket = str(
            configuration.STARTUP_CARDANO_NODE_SOCKET_PATH.parent / f"{LAST_POOL_NAME}.socket"
        )

        tx_outputs = []

        # Submit Tx number 1
        tx_outputs.append(
            self.node_submit_tx(
                cluster_obj=cluster,
                node="pool1",
                temp_template=temp_template,
                src_addr=payment_addrs[0],
                dst_addr=payment_addrs[0],
            )
        )

        if ROLLBACK_PAUSE:
            print(f"PHASE1: single cluster with {configuration.NUM_POOLS} pools")
            print(f"  CARDANO_NODE_SOCKET_PATH: {configuration.STARTUP_CARDANO_NODE_SOCKET_PATH}")
            print(
                f"  Funding address: {payment_addrs[-1].address}, "
                f"skey: {payment_addrs[-1].skey_file.absolute()}, "
                f"vkey: {payment_addrs[-1].vkey_file.absolute()}"
            )
            print(f"  Addresses: {[p.address for p in payment_addrs[:-1]]}")
            input("Press Enter to continue...")

        with cluster_manager.respin_on_failure():
            # Split the cluster into two separate clusters
            self.split_cluster(split_topology_dir=split_topology_dir)

            # Check that the Tx number 1 exists on both clusters
            assert self.node_query_utxo(
                cluster_obj=cluster, node="pool1", tx_raw_output=tx_outputs[-1]
            ), "The Tx number 1 doesn't exist on cluster 1"
            assert self.node_query_utxo(
                cluster_obj=cluster, node=LAST_POOL_NAME, tx_raw_output=tx_outputs[-1]
            ), "The Tx number 1 doesn't exist on cluster 2"

            # Submit a Tx number 2 on the first cluster
            tx_outputs.append(
                self.node_submit_tx(
                    cluster_obj=cluster,
                    node="pool1",
                    temp_template=temp_template,
                    src_addr=payment_addrs[1],
                    dst_addr=payment_addrs[1],
                )
            )

            # Check that the Tx number 2 exists only on the first cluster
            assert self.node_query_utxo(
                cluster_obj=cluster, node="pool1", tx_raw_output=tx_outputs[-1]
            ), "The Tx number 2 doesn't exist on cluster 1"
            assert not self.node_query_utxo(
                cluster_obj=cluster, node=LAST_POOL_NAME, tx_raw_output=tx_outputs[-1]
            ), "The Tx number 2 does exist on cluster 2"

            # Submit a Tx number 3 on the second cluster
            tx_outputs.append(
                self.node_submit_tx(
                    cluster_obj=cluster,
                    node=LAST_POOL_NAME,
                    temp_template=temp_template,
                    src_addr=payment_addrs[2],
                    dst_addr=payment_addrs[2],
                )
            )

            # Check that the Tx number 3 exists only on the second cluster
            assert not self.node_query_utxo(
                cluster_obj=cluster, node="pool1", tx_raw_output=tx_outputs[-1]
            ), "The Tx number 3 does exist on cluster 1"
            assert self.node_query_utxo(
                cluster_obj=cluster, node=LAST_POOL_NAME, tx_raw_output=tx_outputs[-1]
            ), "The Tx number 3 doesn't exist on cluster 2"

            # Wait for new block to let chains progress.
            # We can't wait for too long, because if both clusters has produced more than
            # `securityParam` number of blocks while the topology was fragmented, it would not be
            # possible to bring the the clusters back into global consensus. On local cluster,
            # the value of `securityParam` is 10.
            cluster.wait_for_new_block()

            if ROLLBACK_PAUSE:
                print("PHASE2: cluster with separated into cluster1 and cluster2")
                print(f"  Cluster 1 CARDANO_NODE_SOCKET_PATH: {cluster1_socket}")
                print(f"  Cluster 2 CARDANO_NODE_SOCKET_PATH: {cluster2_socket}")
                input("Press Enter to continue...")

            # Restore the cluster topology
            self.restore_cluster(backup_topology=backup_topology)

            # Wait a bit for rollback to happen
            time.sleep(10)

            if ROLLBACK_PAUSE:
                print("PHASE3: single cluster with restored topology")
                input("Press Enter to continue...")

            # Check that global consensus was restored
            utxo_tx2_cluster1 = self.node_query_utxo(
                cluster_obj=cluster, node="pool1", tx_raw_output=tx_outputs[-2]
            )
            utxo_tx2_cluster2 = self.node_query_utxo(
                cluster_obj=cluster, node=LAST_POOL_NAME, tx_raw_output=tx_outputs[-2]
            )
            utxo_tx3_cluster1 = self.node_query_utxo(
                cluster_obj=cluster, node="pool1", tx_raw_output=tx_outputs[-1]
            )
            utxo_tx3_cluster2 = self.node_query_utxo(
                cluster_obj=cluster, node=LAST_POOL_NAME, tx_raw_output=tx_outputs[-1]
            )

            assert (
                utxo_tx2_cluster1 == utxo_tx2_cluster2
            ), "UTxOs are not identical, consensus was not restored?"
            assert (
                utxo_tx3_cluster1 == utxo_tx3_cluster2
            ), "UTxOs are not identical, consensus was not restored?"

            assert (
                utxo_tx2_cluster1 or utxo_tx3_cluster1
            ), "Neither Tx number 2 nor Tx number 3 exists on chain"

            # At this point we know that the cluster is not split, so we don't need to respin
            # the cluster if the test fails.

        assert not (
            utxo_tx2_cluster1 and utxo_tx3_cluster1
        ), "Neither Tx number 2 nor Tx number 3 was rolled back"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_permanent_fork(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        backup_topology: Path,
        split_topology_dir: Path,
    ):
        """Test that global consensus is NOT reached and the result is permanent fork.

        The original cluster is split into two clusters, and after `securityParam`
        number of blocks is produced, the original cluster topology gets restored.

        * Submit Tx number 1
        * Split the cluster into two separate clusters
        * Submit a Tx number 2 on the first cluster
        * Submit a Tx number 3 on the second cluster
        * Wait until `securityParam` number of blocks is produced on both clusters
        * Restore the cluster topology
        * Check that global consensus was NOT restored
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)

        tx_outputs = []

        # Submit Tx number 1
        tx_outputs.append(
            self.node_submit_tx(
                cluster_obj=cluster,
                node="pool1",
                temp_template=temp_template,
                src_addr=payment_addrs[0],
                dst_addr=payment_addrs[0],
            )
        )

        # The `securityParam` specifies after how many blocks is the blockchain considered to be
        # final, and thus can no longer be rolled back (i.e. what is the maximum allowable length
        # of any chain fork).
        split_block = cluster.g_query.get_block_no()
        final_block = split_block + cluster.genesis["securityParam"] + 1

        # Split the cluster into two separate clusters
        self.split_cluster(split_topology_dir=split_topology_dir)

        # The cluster needs respin after this point
        cluster_manager.set_needs_respin()

        # Submit a Tx number 2 on the first cluster
        tx_outputs.append(
            self.node_submit_tx(
                cluster_obj=cluster,
                node="pool1",
                temp_template=temp_template,
                src_addr=payment_addrs[1],
                dst_addr=payment_addrs[1],
            )
        )

        # Submit a Tx number 3 on the second cluster
        tx_outputs.append(
            self.node_submit_tx(
                cluster_obj=cluster,
                node=LAST_POOL_NAME,
                temp_template=temp_template,
                src_addr=payment_addrs[2],
                dst_addr=payment_addrs[2],
            )
        )

        # After both clusters has produced more than `securityParam` number of blocks while the
        # topology was fragmented, it is not be possible to bring the the clusters back
        # into global consensus.
        self.node_wait_for_block(cluster_obj=cluster, node="pool1", block_no=final_block)
        self.node_wait_for_block(cluster_obj=cluster, node=LAST_POOL_NAME, block_no=final_block)

        # Restore the cluster topology
        self.restore_cluster(backup_topology=backup_topology)
        time.sleep(10)

        # Wait for new blocks to let chains progress
        cluster.wait_for_new_block(new_blocks=2)

        # Check that global consensus was NOT restored
        utxo_tx1_cluster1 = self.node_query_utxo(
            cluster_obj=cluster, node="pool1", tx_raw_output=tx_outputs[0]
        )
        utxo_tx1_cluster2 = self.node_query_utxo(
            cluster_obj=cluster, node=LAST_POOL_NAME, tx_raw_output=tx_outputs[0]
        )
        utxo_tx2_cluster1 = self.node_query_utxo(
            cluster_obj=cluster, node="pool1", tx_raw_output=tx_outputs[1]
        )
        utxo_tx2_cluster2 = self.node_query_utxo(
            cluster_obj=cluster, node=LAST_POOL_NAME, tx_raw_output=tx_outputs[1]
        )
        utxo_tx3_cluster1 = self.node_query_utxo(
            cluster_obj=cluster, node="pool1", tx_raw_output=tx_outputs[2]
        )
        utxo_tx3_cluster2 = self.node_query_utxo(
            cluster_obj=cluster, node=LAST_POOL_NAME, tx_raw_output=tx_outputs[2]
        )

        assert utxo_tx1_cluster1 == utxo_tx1_cluster2, "UTxOs from Tx 1 are not identical"

        assert (
            utxo_tx2_cluster1 != utxo_tx2_cluster2
        ), "UTxOs are identical, consensus was restored?"

        assert (
            utxo_tx3_cluster1 != utxo_tx3_cluster2
        ), "UTxOs are identical, consensus was restored?"

        assert (
            utxo_tx2_cluster1 and not utxo_tx2_cluster2
        ), "Tx number 2 is supposed to exist only on the first cluster"

        assert (
            not utxo_tx3_cluster1 and utxo_tx3_cluster2
        ), "Tx number 3 is supposed to exist only on the second cluster"
