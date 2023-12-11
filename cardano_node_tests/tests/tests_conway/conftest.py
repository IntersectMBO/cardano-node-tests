import logging

import pytest

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.utils import governance_setup

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def cluster_use_governance(
    cluster_manager: cluster_management.ClusterManager,
) -> governance_setup.GovClusterT:
    """Mark governance as "in use" and return instance of `clusterlib.ClusterLib`."""
    cluster_obj = cluster_manager.get(
        use_resources=[
            cluster_management.Resources.COMMITTEE,
            cluster_management.Resources.DREPS,
            *cluster_management.Resources.ALL_POOLS,
        ]
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster_obj
    )
    return cluster_obj, governance_data


@pytest.fixture
def cluster_lock_governance(
    cluster_manager: cluster_management.ClusterManager,
) -> governance_setup.GovClusterT:
    """Mark governance as "locked" and return instance of `clusterlib.ClusterLib`."""
    cluster_obj = cluster_manager.get(
        use_resources=cluster_management.Resources.ALL_POOLS,
        lock_resources=[cluster_management.Resources.COMMITTEE, cluster_management.Resources.DREPS],
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster_obj
    )
    return cluster_obj, governance_data
