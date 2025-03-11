from cardano_node_tests.utils import temptools

CLUSTER_LOCK = ".cluster.lock"
LOG_LOCK = ".manager_log.lock"
START_CLUSTER_LOG = "start-cluster.log"
ADDRS_DATA_DIRNAME = "addrs_data"


def get_cluster_lock_file() -> str:
    pytest_tmp_dir = temptools.get_pytest_root_tmp()
    cluster_lock = f"{pytest_tmp_dir}/{CLUSTER_LOCK}"
    return cluster_lock
