"""Tests for reconnect."""

import logging
import os
import pathlib as pl
import time
import typing as tp

import allure
import pytest
import requests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import http_client
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

TEST_RECONNECT = os.environ.get("TEST_RECONNECT") is not None
TEST_METRICS_RECONNECT = os.environ.get("TEST_METRICS_RECONNECT") is not None


@common.SKIPIF_ON_TESTNET
@pytest.mark.skipif(
    VERSIONS.cluster_era != VERSIONS.transaction_era,
    reason="runs only with same cluster and Tx era",
)
class TestNodeReconnect:
    """Tests for nodes reconnect."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        cluster = cluster_singleton
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    def node_query_utxo(
        self,
        cluster_obj: clusterlib.ClusterLib,
        node: str,
        address: str = "",
        tx_raw_output: clusterlib.TxRawOutput | None = None,
    ) -> list[clusterlib.UTXOData]:
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

    def node_get_tip(
        self,
        cluster_obj: clusterlib.ClusterLib,
        node: str,
    ) -> dict[str, tp.Any]:
        """Query UTxO on given node."""
        orig_socket = os.environ.get("CARDANO_NODE_SOCKET_PATH")
        assert orig_socket
        new_socket = pl.Path(orig_socket).parent / f"{node}.socket"

        try:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = str(new_socket)
            tip = cluster_obj.g_query.get_tip()
            return tip
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
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=1_000_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        try:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = str(new_socket)
            tx_raw_output = cluster_obj.g_transaction.send_tx(
                src_address=src_addr.address,
                tx_name=f"{temp_template}_{int(curr_time)}",
                txouts=txouts,
                tx_files=tx_files,
                verify_tx=False,
            )
            return tx_raw_output
        finally:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = orig_socket

    def get_prometheus_metrics(self, port: int) -> requests.Response:
        response = http_client.get_session().get(f"http://localhost:{port}/metrics", timeout=10)
        assert response, f"Request failed, status code {response.status_code}"
        return response

    def _node_synced(self, cluster_obj: clusterlib.ClusterLib, node: str) -> None:
        sprogress = 0.0
        old_sprogress = 0.0
        for __ in range(5):
            sprogress = float(self.node_get_tip(cluster_obj=cluster_obj, node=node)["syncProgress"])
            if sprogress == 100:
                break
            if sprogress == old_sprogress:
                msg = f"Cannot sync node2, sync progress: {sprogress}%"
                raise AssertionError(msg)
            old_sprogress = sprogress
            time.sleep(2)
        else:
            msg = f"Cannot sync node2 in time, sync progress: {sprogress}%"
            raise AssertionError(msg)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not TEST_RECONNECT, reason="This is not a 'reconnect' testrun")
    def test_reconnect(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test that node reconnects after it was stopped.

        * Stop the node2
        * Submit Tx number 1 on node1
        * Start the stopped node2
        * Submit a Tx number 2 on node2
        * Wait for 2 new blocks
        * Check that node1 knows about Tx number 2, and/or node2 knows about Tx number 1
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)

        node1 = "pool1"
        node2 = "pool2"

        def _assert(tx_outputs: list[clusterlib.TxRawOutput]) -> None:
            tx1_node2 = self.node_query_utxo(
                cluster_obj=cluster, node=node2, tx_raw_output=tx_outputs[-2]
            )
            tx2_node1 = self.node_query_utxo(
                cluster_obj=cluster, node=node1, tx_raw_output=tx_outputs[-1]
            )

            # If node1 knows about Tx number 2, and/or node2 knows about Tx number 1,
            # the connection must have been established.
            assert tx2_node1 or tx1_node2, (
                f"Connection failed?\ntx1_node2: {tx2_node1}\ntx2_node1: {tx2_node1}"
            )

        with cluster_manager.respin_on_failure():
            for restart_no in range(1, 11):
                LOGGER.info(f"Running restart number {restart_no}")

                tx_outputs = []

                # Stop the node2
                cluster_nodes.stop_nodes([node2])

                # Submit a Tx number 1 on the node1
                tx_outputs.append(
                    self.node_submit_tx(
                        cluster_obj=cluster,
                        node=node1,
                        temp_template=f"{temp_template}_{restart_no}_node1",
                        src_addr=payment_addrs[0],
                        dst_addr=payment_addrs[0],
                    )
                )

                # Start the node2
                cluster_nodes.start_nodes([node2])
                time.sleep(5)
                self._node_synced(cluster_obj=cluster, node=node2)

                # Submit a Tx number 2 on the node2
                tx_outputs.append(
                    self.node_submit_tx(
                        cluster_obj=cluster,
                        node=node2,
                        temp_template=f"{temp_template}_{restart_no}_node2",
                        src_addr=payment_addrs[1],
                        dst_addr=payment_addrs[1],
                    )
                )

                for check_no in range(1, 3):
                    cluster.wait_for_new_block(new_blocks=1)
                    try:
                        _assert(tx_outputs=tx_outputs)
                    except AssertionError:
                        if check_no > 1:
                            raise
                        LOGGER.info(f"AssertionError on check {check_no}")

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not TEST_METRICS_RECONNECT, reason="This is not a 'metrics reconnect' testrun"
    )
    @pytest.mark.skipif(configuration.NUM_POOLS != 3, reason="`NUM_POOLS` must be 3")
    @pytest.mark.skipif(configuration.ENABLE_LEGACY, reason="Works only with P2P topology")
    @pytest.mark.skipif(
        "mainnet_fast" not in configuration.SCRIPTS_DIRNAME,
        reason="Cannot run on testnet with short epochs",
    )
    def test_metrics_reconnect(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
    ):
        """Test using metrics that node reconnects after it was restarted."""
        cluster = cluster_singleton
        common.get_test_id(cluster)

        node2 = "pool2"

        prometheus_port = (
            cluster_nodes.get_cluster_type()
            .cluster_scripts.get_instance_ports(cluster_nodes.get_instance_num())
            .prometheus_pool2
        )

        def _assert() -> None:
            response = self.get_prometheus_metrics(prometheus_port)

            metrics_pairs = [m.split() for m in response.text.strip().split("\n")]
            metrics = {m[0]: m[1] for m in metrics_pairs}

            assert int(metrics["cardano_node_metrics_inboundGovernor_hot"]) > 1
            assert int(metrics["cardano_node_metrics_peerSelection_cold"]) == 0

        with cluster_manager.respin_on_failure():
            for restart_no in range(1, 200):
                LOGGER.info(f"Running restart number {restart_no}")

                # Restart node2
                cluster_nodes.restart_nodes([node2], delay=5)
                self._node_synced(cluster_obj=cluster, node=node2)

                for check_no in range(1, 11):
                    try:
                        _assert()
                    except AssertionError:  # noqa: PERF203
                        if check_no == 10:
                            raise
                        LOGGER.info(f"AssertionError on check {check_no}")
                        time.sleep(5)
