"""Tests for Prometheus and EKG metrics."""
import logging
from pathlib import Path

import allure
import pytest
import requests
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import model_ekg

LOGGER = logging.getLogger(__name__)

if getattr(configuration, "_called_from_test", None):
    pytest.skip("metrics data are not stable yet", allow_module_level=True)


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    p = Path(tmp_path_factory.getbasetemp()).joinpath(helpers.get_id_for_mktemp(__file__)).resolve()
    p.mkdir(exist_ok=True, parents=True)
    return p


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


EXPECTED_METRICS = [
    "cardano_node_metrics_Forge_adopted_int",
    "cardano_node_metrics_Forge_forge_about_to_lead_int",
    "cardano_node_metrics_Forge_forged_int",
    "cardano_node_metrics_Forge_node_is_leader_int",
    "cardano_node_metrics_Forge_node_not_leader_int",
    "cardano_node_metrics_Mem_resident_int",
    "cardano_node_metrics_RTS_gcLiveBytes_int",
    "cardano_node_metrics_RTS_gcMajorNum_int",
    "cardano_node_metrics_RTS_gcMinorNum_int",
    "cardano_node_metrics_RTS_gcticks_int",
    "cardano_node_metrics_RTS_mutticks_int",
    "cardano_node_metrics_Stat_cputicks_int",
    "cardano_node_metrics_Stat_threads_int",
    "cardano_node_metrics_blockNum_int",
    "cardano_node_metrics_blocksForgedNum_int",
    "cardano_node_metrics_currentKESPeriod_int",
    "cardano_node_metrics_delegMapSize_int",
    "cardano_node_metrics_density_real",
    "cardano_node_metrics_epoch_int",
    "cardano_node_metrics_mempoolBytes_int",
    "cardano_node_metrics_myBlocksUncoupled_int",
    "cardano_node_metrics_nodeIsLeaderNum_int",
    "cardano_node_metrics_nodeStartTime_int",
    "cardano_node_metrics_operationalCertificateExpiryKESPeriod_int",
    "cardano_node_metrics_operationalCertificateStartKESPeriod_int",
    "cardano_node_metrics_remainingKESPeriods_int",
    "cardano_node_metrics_served_header_counter_int",
    "cardano_node_metrics_slotInEpoch_int",
    "cardano_node_metrics_slotNum_int",
    "cardano_node_metrics_txsInMempool_int",
    "cardano_node_metrics_txsProcessedNum_int",
    "cardano_node_metrics_utxoSize_int",
    "ekg_server_timestamp_ms",
    "rts_gc_bytes_allocated",
    "rts_gc_bytes_copied",
    "rts_gc_cpu_ms",
    "rts_gc_cumulative_bytes_used",
    "rts_gc_current_bytes_slop",
    "rts_gc_current_bytes_used",
    "rts_gc_gc_cpu_ms",
    "rts_gc_gc_wall_ms",
    "rts_gc_init_cpu_ms",
    "rts_gc_init_wall_ms",
    "rts_gc_max_bytes_slop",
    "rts_gc_max_bytes_used",
    "rts_gc_mutator_cpu_ms",
    "rts_gc_mutator_wall_ms",
    "rts_gc_num_bytes_usage_samples",
    "rts_gc_num_gcs",
    "rts_gc_par_avg_bytes_copied",
    "rts_gc_par_max_bytes_copied",
    "rts_gc_par_tot_bytes_copied",
    "rts_gc_peak_megabytes_allocated",
    "rts_gc_wall_ms",
]


@pytest.fixture
def wait_epochs(cluster: clusterlib.ClusterLib):
    """Make sure we are not checking metrics in epoch < 4."""
    epochs_to_wait = 4 - cluster.get_epoch()
    if epochs_to_wait > 0:
        cluster.wait_for_new_epoch(new_epochs=epochs_to_wait)


def get_prometheus_metrics(port: int) -> requests.Response:
    response = requests.get(f"http://localhost:{port}/metrics")
    assert response, f"Request failed, status code {response.status_code}"
    return response


def get_ekg_metrics(port: int) -> requests.Response:
    response = requests.get(
        f"http://localhost:{port}/",
        headers={"Accept": "application/json"},
    )
    assert response, f"Request failed, status code {response.status_code}"
    return response


@pytest.mark.skipif(
    bool(configuration.TX_ERA),
    reason="different TX eras doesn't affect this test, pointless to run",
)
class TestPrometheus:
    """Prometheus metrics tests."""

    @allure.link(helpers.get_vcs_link())
    def test_available_metrics(
        self,
        wait_epochs,
    ):
        """Test that list of available metrics == list of expected metrics."""
        # pylint: disable=unused-argument
        prometheus_port = (
            cluster_nodes.get_cluster_type()
            .cluster_scripts.get_instance_ports(cluster_nodes.get_cluster_env().instance_num)
            .prometheus_pool1
        )

        response = get_prometheus_metrics(prometheus_port)

        metrics = response.text.strip().split("\n")
        metrics_keys = sorted(m.split(" ")[0] for m in metrics)
        assert metrics_keys == EXPECTED_METRICS, "Metrics differ"


@pytest.mark.skipif(
    bool(configuration.TX_ERA),
    reason="different TX eras doesn't affect this test, pointless to run",
)
class TestEKG:
    """EKG metrics tests."""

    @allure.link(helpers.get_vcs_link())
    def test_available_metrics(
        self,
        wait_epochs,
    ):
        """Test that available EKG metrics matches the expected schema."""
        # pylint: disable=unused-argument
        ekg_port = (
            cluster_nodes.get_cluster_type()
            .cluster_scripts.get_instance_ports(cluster_nodes.get_cluster_env().instance_num)
            .ekg_pool1
        )

        response = get_ekg_metrics(ekg_port)
        model_ekg.Model.validate(response.json())
