"""Tests for reconnect."""
import logging
import os
import pathlib as pl
import time
import typing as tp

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

TEST_RECONNECT = os.environ.get("TEST_RECONNECT") is not None


@pytest.mark.skipif(not TEST_RECONNECT, reason="This is not a 'reconnect' testrun")
@pytest.mark.skipif(
    cluster_nodes.get_cluster_type().type != cluster_nodes.ClusterType.LOCAL,
    reason="Runs only on local cluster",
)
@pytest.mark.skipif(
    VERSIONS.cluster_era != VERSIONS.transaction_era,
    reason="runs only with same cluster and Tx era",
)
@pytest.mark.skipif(not configuration.ENABLE_P2P, reason="Works only with P2P topology")
class TestNodeReconnect:
    """Tests for nodes reconnect."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
    ) -> tp.List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        cluster = cluster_singleton
        num_addrs = 3

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

    def node_query_utxo(
        self,
        cluster_obj: clusterlib.ClusterLib,
        node: str,
        address: str = "",
        tx_raw_output: tp.Optional[clusterlib.TxRawOutput] = None,
    ) -> tp.List[clusterlib.UTXOData]:
        """Query UTxO on given node."""
        orig_socket = os.environ.get("CARDANO_NODE_SOCKET_PATH")
        assert orig_socket
        new_socket = pl.Path(orig_socket).parent / f"{node}.socket"

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
        new_socket = pl.Path(orig_socket).parent / f"{node}.socket"

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

    @allure.link(helpers.get_vcs_link())
    def test_reconnect(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
        payment_addrs: tp.List[clusterlib.AddressRecord],
    ):
        """Test that node reconnects after it was stopped.

        * Submit Tx number 1
        * Check that the Tx number 1 exists on both node1 and node2
        * Stop the node2
        * Submit a Tx number 2
        * Check that the Tx number 2 exists on the node1
        * Start the stopped node2
        * Submit a Tx number 3 on the node2
        * Check that the Tx number 3 exists on both node1 and node2
        * Check that the Tx number 2 exists on the node2
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)

        node1 = "pool1"
        node2 = "pool2"

        tx_outputs = []

        # Submit Tx number 1
        tx_outputs.append(
            self.node_submit_tx(
                cluster_obj=cluster,
                node=node1,
                temp_template=temp_template,
                src_addr=payment_addrs[0],
                dst_addr=payment_addrs[0],
            )
        )

        cluster.wait_for_new_block(new_blocks=3)

        # Check that the Tx number 1 exists on both node1 and node2
        assert self.node_query_utxo(
            cluster_obj=cluster, node=node1, tx_raw_output=tx_outputs[-1]
        ), "The Tx number 1 doesn't exist on node1"
        assert self.node_query_utxo(
            cluster_obj=cluster, node=node2, tx_raw_output=tx_outputs[-1]
        ), "The Tx number 1 doesn't exist on node2"

        with cluster_manager.respin_on_failure():
            # Stop the node2
            cluster_nodes.stop_nodes([node2])
            time.sleep(10)

            # Submit a Tx number 2 on the node1
            tx_outputs.append(
                self.node_submit_tx(
                    cluster_obj=cluster,
                    node=node1,
                    temp_template=temp_template,
                    src_addr=payment_addrs[1],
                    dst_addr=payment_addrs[1],
                )
            )

            # Check that the Tx number 2 exists on the node1
            assert self.node_query_utxo(
                cluster_obj=cluster, node=node1, tx_raw_output=tx_outputs[-1]
            ), "The Tx number 2 doesn't exist on node 1"

            # Start the stopped node
            cluster_nodes.start_nodes([node2])
            time.sleep(10)

            # Submit a Tx number 3 on the node2
            tx_outputs.append(
                self.node_submit_tx(
                    cluster_obj=cluster,
                    node=node2,
                    temp_template=temp_template,
                    src_addr=payment_addrs[2],
                    dst_addr=payment_addrs[2],
                )
            )

            cluster.wait_for_new_block(new_blocks=3)

            # Check that the Tx number 3 exists on both node1 and node2
            assert self.node_query_utxo(
                cluster_obj=cluster, node=node1, tx_raw_output=tx_outputs[-1]
            ), "The Tx number 3 doesn't exist on node1"
            assert self.node_query_utxo(
                cluster_obj=cluster, node=node2, tx_raw_output=tx_outputs[-1]
            ), "The Tx number 3 doesn't exist on node2"

            # Check that the Tx number 2 exists on the node2
            assert self.node_query_utxo(
                cluster_obj=cluster, node=node2, tx_raw_output=tx_outputs[-2]
            ), "The Tx number 2 doesn't exist on node2"
