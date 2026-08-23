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

TEST_RECONNECT = helpers.is_truthy_env_var("TEST_RECONNECT")
TEST_METRICS_RECONNECT = helpers.is_truthy_env_var("TEST_METRICS_RECONNECT")

# Number of node restarts performed by the `test_reconnect` test.
RESTARTS_NUM = 10
# Amount of Lovelace in each of the UTxOs that are pre-created for the `test_reconnect` test.
# One UTxO on each payment address is spent in each restart iteration.
UTXO_AMOUNT = 5_000_000
# Amount of Lovelace on top of the pre-created UTxOs, to cover the fee of the splitting Tx.
FEE_SLACK = 10_000_000

# Prometheus metric names, listed from the most recent to the oldest. Newer node versions
# append the `_int` suffix to integer metrics and capitalize the `peerSelection` counters,
# older node versions use the plain lowercase names.
INBOUND_GOVERNOR_HOT_METRICS: tp.Final[tuple[str, ...]] = (
    "cardano_node_metrics_inboundGovernor_hot_int",
    "cardano_node_metrics_inboundGovernor_hot",
)
PEER_SELECTION_COLD_METRICS: tp.Final[tuple[str, ...]] = (
    "cardano_node_metrics_peerSelection_Cold_int",
    "cardano_node_metrics_peerSelection_cold",
)


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
            min_amount=RESTARTS_NUM * UTXO_AMOUNT + FEE_SLACK,
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @pytest.fixture
    def payment_utxos(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ) -> list[list[clusterlib.UTXOData]]:
        """Split funds on each payment address into one UTxO per restart iteration.

        Each restart iteration spends its own UTxO, so a transaction that is still waiting in
        a mempool cannot block the transaction that is submitted in the next iteration.
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)
        utxos: list[list[clusterlib.UTXOData]] = []

        for idx, addr in enumerate(payment_addrs, start=1):
            txouts = [
                clusterlib.TxOut(address=addr.address, amount=UTXO_AMOUNT)
                for __ in range(RESTARTS_NUM)
            ]
            tx_output = cluster.g_transaction.send_tx(
                src_address=addr.address,
                tx_name=f"{temp_template}_split_addr{idx}",
                txouts=txouts,
                tx_files=clusterlib.TxFiles(signing_key_files=[addr.skey_file]),
                # Create a separate UTxO for each txout instead of joining them into one
                join_txouts=False,
            )
            # Any of the outputs of the splitting Tx can be used - all of them belong to
            # `addr` and hold at least `UTXO_AMOUNT`, the change txout included. There's no
            # need to tell the change txout apart from the requested txouts.
            out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
            addr_utxos = out_utxos[:RESTARTS_NUM]
            assert len(addr_utxos) == RESTARTS_NUM and all(
                u.amount >= UTXO_AMOUNT for u in addr_utxos
            ), f"Unexpected UTxOs on '{addr.address}': {addr_utxos}"
            utxos.append(addr_utxos)

        return utxos

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
        finally:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = orig_socket
        return utxos

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
        finally:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = orig_socket
        return tip

    def node_submit_tx(
        self,
        cluster_obj: clusterlib.ClusterLib,
        node: str,
        temp_template: str,
        src_addr: clusterlib.AddressRecord,
        dst_addr: clusterlib.AddressRecord,
        txin: clusterlib.UTXOData,
    ) -> clusterlib.TxRawOutput:
        """Submit transaction on given node.

        The `txin` is specified explicitly, so the transaction doesn't depend on the UTxO state
        as seen by the node. Automatic input selection would keep selecting an input that is
        already spent by a previous transaction that is still waiting in a mempool.
        """
        orig_socket = os.environ.get("CARDANO_NODE_SOCKET_PATH")
        assert orig_socket
        new_socket = pl.Path(orig_socket).parent / f"{node}.socket"

        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=1_000_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        try:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = str(new_socket)
            tx_raw_output = cluster_obj.g_transaction.send_tx(
                src_address=src_addr.address,
                tx_name=temp_template,
                txins=[txin],
                txouts=txouts,
                tx_files=tx_files,
                verify_tx=False,
            )
        finally:
            os.environ["CARDANO_NODE_SOCKET_PATH"] = orig_socket
        return tx_raw_output

    def get_prometheus_metrics(self, port: int) -> requests.Response:
        response = http_client.get_session().get(f"http://localhost:{port}/metrics", timeout=10)
        assert response, f"Request failed, status code {response.status_code}"
        return response

    @staticmethod
    def parse_prometheus_metrics(response_text: str) -> dict[str, float]:
        """Parse a Prometheus response into a metric name to value mapping.

        A metric line is `name[{labels}] value [timestamp]`. Comment lines are skipped,
        labels are stripped from the metric names and the values of all the series of a
        labelled metric are summed, so that the counts the tests are interested in are
        totals and not an arbitrary series.

        Args:
            response_text: Body of the response from the Prometheus endpoint.

        Returns:
            dict: Metric names and their values.
        """
        metrics: dict[str, float] = {}

        for metric_line in response_text.splitlines():
            line = metric_line.strip()
            if not line or line.startswith("#"):
                continue

            # Strip the labels, they can contain whitespace
            name, brace, remainder = line.partition("{")
            if brace:
                remainder = remainder.partition("}")[2]
            else:
                name, __, remainder = line.partition(" ")

            # Ignore the optional timestamp that can follow the value
            value_str = remainder.split()
            if not value_str:
                continue
            try:
                value = float(value_str[0])
            except ValueError:
                continue

            name = name.strip()
            metrics[name] = metrics.get(name, 0.0) + value

        return metrics

    @staticmethod
    def get_metric_value(metrics: dict[str, float], names: tuple[str, ...]) -> int:
        """Return value of the first metric that is present under one of its known names.

        Metric names differ between node versions, so several aliases of the same metric
        are checked.

        Args:
            metrics: Metrics parsed from the Prometheus endpoint.
            names: Known names of the metric, from the most recent to the oldest.

        Returns:
            int: Value of the metric.
        """
        for name in names:
            value = metrics.get(name)
            if value is not None:
                return int(value)

        msg = f"None of the metrics {names} was found"
        raise AssertionError(msg)

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
        payment_utxos: list[list[clusterlib.UTXOData]],
    ):
        """Test that node reconnects after it was stopped.

        * Split funds into one UTxO per iteration
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
        # There's one list of UTxOs for each of the two payment addresses
        utxos1, utxos2 = payment_utxos

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
                f"Connection failed?\ntx1_node2: {tx1_node2}\ntx2_node1: {tx2_node1}"
            )

        with cluster_manager.respin_on_failure():
            for restart_no, (txin1, txin2) in enumerate(zip(utxos1, utxos2, strict=True), start=1):
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
                        txin=txin1,
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
                        txin=txin2,
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
    @pytest.mark.skipif(
        "mainnet_fast" not in configuration.TESTNET_VARIANT,
        reason="Cannot run on testnet with short epochs",
    )
    def test_metrics_reconnect(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
    ):
        """Test using metrics that node reconnects after it was restarted.

        Test node reconnection after restart by validating Prometheus metrics indicating
        successful peer connections. Performs 200 restart iterations to ensure reliability.

        * Get Prometheus port for pool2 from cluster configuration
        * For each iteration (up to 200 restarts):

          - Restart pool2 node with 5 second delay
          - Wait for node to sync with chain
          - Fetch Prometheus metrics from node HTTP endpoint
          - Parse metrics response into key-value pairs
          - Check inboundGovernor hot metric > 1 (active inbound connections)
          - Check peerSelection cold metric == 0 (no cold peers)
          - Retry up to 10 times with 5 second delays if assertions fail
          - Fail test if metrics don't match after 10 attempts
        """
        cluster = cluster_singleton
        common.get_test_id(cluster)

        node2 = "pool2"

        prometheus_port = (
            cluster_nodes.get_cluster_type()
            .cluster_scripts.get_instance_ports(instance_num=cluster_nodes.get_instance_num())
            .prometheus_pool2
        )

        def _assert() -> None:
            response = self.get_prometheus_metrics(prometheus_port)

            metrics = self.parse_prometheus_metrics(response.text)

            assert self.get_metric_value(metrics, INBOUND_GOVERNOR_HOT_METRICS) > 1
            assert self.get_metric_value(metrics, PEER_SELECTION_COLD_METRICS) == 0

        with cluster_manager.respin_on_failure():
            for restart_no in range(1, 200):
                LOGGER.info(f"Running restart number {restart_no}")

                # Restart node2
                cluster_nodes.restart_nodes([node2], delay=5)
                self._node_synced(cluster_obj=cluster, node=node2)

                for check_no in range(1, 11):
                    try:
                        _assert()
                    except AssertionError:
                        if check_no == 10:
                            raise
                        LOGGER.info(f"AssertionError on check {check_no}")
                        time.sleep(5)
