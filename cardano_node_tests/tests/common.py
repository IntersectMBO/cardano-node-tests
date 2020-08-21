from typing import List

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers


def create_pool_users(
    cluster_obj: clusterlib.ClusterLib, temp_template: str, no_of_addr: int = 1,
) -> List[clusterlib.PoolUser]:
    """Create PoolOwners.

    Common functionality for tests.
    """
    pool_users = []
    payment_addrs = []
    for i in range(no_of_addr):
        # create key pairs and addresses
        stake_addr_rec = helpers.create_stake_addr_records(
            f"addr{i}_{temp_template}", cluster_obj=cluster_obj
        )[0]
        payment_addr_rec = helpers.create_payment_addr_records(
            f"addr{i}_{temp_template}",
            cluster_obj=cluster_obj,
            stake_vkey_file=stake_addr_rec.vkey_file,
        )[0]
        # create pool owner struct
        pool_user = clusterlib.PoolUser(payment=payment_addr_rec, stake=stake_addr_rec)
        payment_addrs.append(payment_addr_rec)
        pool_users.append(pool_user)

    return pool_users
