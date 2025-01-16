import logging

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def cluster(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Return instance of `clusterlib.ClusterLib`."""
    return cluster_manager.get(use_resources=[cluster_management.Resources.PLUTUS])
