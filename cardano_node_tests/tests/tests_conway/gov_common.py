import logging
import pickle
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import locking

LOGGER = logging.getLogger(__name__)

GOV_DATA_STORE = "governance_data.pickle"

GovClusterT = tp.Tuple[clusterlib.ClusterLib, governance_setup.DefaultGovernance]


def check_drep_delegation(deleg_state: dict, drep_id: str, stake_addr_hash: str) -> None:
    drep_records = deleg_state["dstate"]["unified"]["credentials"]

    stake_addr_key = f"keyHash-{stake_addr_hash}"
    stake_addr_val = drep_records.get(stake_addr_key) or {}

    expected_drep = f"drep-keyHash-{drep_id}"
    if drep_id == "always_abstain":
        expected_drep = "drep-alwaysAbstain"
    elif drep_id == "always_no_confidence":
        expected_drep = "drep-alwaysNoConfidence"

    assert stake_addr_val.get("drep") == expected_drep


def get_default_governance(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
) -> governance_setup.DefaultGovernance:
    with cluster_manager.cache_fixture(key="default_governance") as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        cluster_env = cluster_nodes.get_cluster_env()
        gov_data_dir = cluster_env.state_dir / governance_setup.GOV_DATA_DIR
        gov_data_store = gov_data_dir / GOV_DATA_STORE

        if gov_data_store.exists():
            with open(gov_data_store, "rb") as in_data:
                loaded_gov_data = pickle.load(in_data)
            fixture_cache.value = loaded_gov_data
            return loaded_gov_data  # type: ignore

        with locking.FileLockIfXdist(str(cluster_env.state_dir / f".{GOV_DATA_STORE}.lock")):
            gov_data_dir.mkdir(exist_ok=True, parents=True)

            governance_data = governance_setup.setup(
                cluster_obj=cluster_obj,
                cluster_manager=cluster_manager,
                destination_dir=gov_data_dir,
            )

            # Check delegation to DReps
            deleg_state = clusterlib_utils.get_delegation_state(cluster_obj=cluster_obj)
            drep_id = governance_data.dreps_reg[0].drep_id
            stake_addr_hash = cluster_obj.g_stake_address.get_stake_vkey_hash(
                stake_vkey_file=governance_data.drep_delegators[0].stake.vkey_file
            )
            check_drep_delegation(
                deleg_state=deleg_state, drep_id=drep_id, stake_addr_hash=stake_addr_hash
            )

            fixture_cache.value = governance_data

            with open(gov_data_store, "wb") as out_data:
                pickle.dump(governance_data, out_data)

    return governance_data
