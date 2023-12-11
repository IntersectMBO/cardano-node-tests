import logging
import pickle
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import locking

LOGGER = logging.getLogger(__name__)

GOV_DATA_DIR = "governance_data"
GOV_DATA_STORE = "governance_data.pickle"

DREPS_NUM = 4
CC_SIZE = 3


class DefaultGovernance(tp.NamedTuple):
    dreps_reg: tp.List[governance_utils.DRepRegistration]
    drep_delegators: tp.List[clusterlib.PoolUser]
    cc_members: tp.List[clusterlib.CCMember]
    pools_cold: tp.List[clusterlib.ColdKeyPair]


GovClusterT = tp.Tuple[clusterlib.ClusterLib, DefaultGovernance]


def create_vote_stake(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    destination_dir: clusterlib.FileType = ".",
) -> tp.List[clusterlib.PoolUser]:
    name_template = "vote_stake"

    pool_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster_obj,
        name_template=name_template,
        no_of_addr=DREPS_NUM,
        destination_dir=destination_dir,
    )

    # Fund the payment address with some ADA
    clusterlib_utils.fund_from_faucet(
        *pool_users,
        cluster_obj=cluster_obj,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=500_000_000_000,
        destination_dir=destination_dir,
    )

    return pool_users


def create_dreps(
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    pool_users: tp.List[clusterlib.PoolUser],
    destination_dir: clusterlib.FileType = ".",
) -> tp.Tuple[tp.List[governance_utils.DRepRegistration], tp.List[clusterlib.PoolUser]]:
    no_of_addrs = len(pool_users)

    if no_of_addrs < DREPS_NUM:
        raise ValueError("Not enough pool users to create drep registrations")

    name_template = "default_drep"
    stake_deposit = cluster_obj.g_query.get_address_deposit()
    drep_users = pool_users[:DREPS_NUM]

    # Create DRep registration certs
    drep_reg_records = [
        governance_utils.get_drep_reg_record(
            cluster_obj=cluster_obj,
            name_template=f"{name_template}_{i}",
            destination_dir=destination_dir,
        )
        for i in range(1, DREPS_NUM + 1)
    ]

    # Create stake address registration certs
    stake_reg_certs = [
        cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{name_template}_addr{i}",
            deposit_amt=stake_deposit,
            stake_vkey_file=du.stake.vkey_file,
            destination_dir=destination_dir,
        )
        for i, du in enumerate(drep_users, start=1)
    ]

    # Create vote delegation cert
    stake_deleg_certs = [
        cluster_obj.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{name_template}_addr{i + 1}",
            stake_vkey_file=du.stake.vkey_file,
            drep_key_hash=drep_reg_records[i].drep_id,
            destination_dir=destination_dir,
        )
        for i, du in enumerate(drep_users)
    ]

    # Make sure we have enough time to finish the registration/delegation in one epoch
    clusterlib_utils.wait_for_epoch_interval(cluster_obj=cluster_obj, start=1, stop=-15)

    tx_files = clusterlib.TxFiles(
        certificate_files=[
            *[r.registration_cert for r in drep_reg_records],
            *stake_reg_certs,
            *stake_deleg_certs,
        ],
        signing_key_files=[
            payment_addr.skey_file,
            *[r.stake.skey_file for r in drep_users],
            *[r.key_pair.skey_file for r in drep_reg_records],
        ],
    )

    clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_reg",
        src_address=payment_addr.address,
        use_build_cmd=True,
        tx_files=tx_files,
        deposit=(drep_reg_records[0].deposit + stake_deposit) * len(drep_reg_records),
        destination_dir=destination_dir,
    )

    return drep_reg_records, drep_users


def load_committee(cluster_obj: clusterlib.ClusterLib) -> tp.List[clusterlib.CCMember]:
    genesis_cc_members = cluster_obj.conway_genesis.get("committee", {}).get("members") or {}
    if not genesis_cc_members:
        return []

    data_dir = cluster_obj.state_dir / GOV_DATA_DIR

    cc_members = []
    for vkey_file in sorted(data_dir.glob("cc_member*_committee_cold.vkey")):
        fpath = vkey_file.parent
        fbase = vkey_file.name.replace("cold.vkey", "")
        hot_vkey_file = fpath / f"{fbase}hot.vkey"
        cold_vkey_hash = cluster_obj.g_conway_governance.committee.get_key_hash(vkey_file=vkey_file)
        genesis_epoch = genesis_cc_members[f"keyHash-{cold_vkey_hash}"]
        cc_members.append(
            clusterlib.CCMember(
                epoch=genesis_epoch,
                cold_vkey_file=vkey_file,
                cold_vkey_hash=cold_vkey_hash,
                cold_skey_file=fpath / f"{fbase}cold.skey",
                hot_vkey_file=hot_vkey_file,
                hot_vkey_hash=cluster_obj.g_conway_governance.committee.get_key_hash(
                    vkey_file=hot_vkey_file
                ),
                hot_skey_file=fpath / f"{fbase}hot.skey",
            )
        )

    return cc_members


def setup(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    destination_dir: clusterlib.FileType = ".",
) -> DefaultGovernance:
    cc_members = load_committee(cluster_obj=cluster_obj)
    vote_stake = create_vote_stake(
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        destination_dir=destination_dir,
    )
    drep_reg_records, drep_users = create_dreps(
        cluster_obj=cluster_obj,
        payment_addr=vote_stake[0].payment,
        pool_users=vote_stake,
        destination_dir=destination_dir,
    )
    node_cold_records = [
        cluster_manager.cache.addrs_data[pn]["cold_key_pair"]
        for pn in cluster_management.Resources.ALL_POOLS
    ]

    cluster_obj.wait_for_new_epoch(padding_seconds=5)

    return DefaultGovernance(
        dreps_reg=drep_reg_records,
        drep_delegators=drep_users,
        cc_members=cc_members,
        pools_cold=node_cold_records,
    )


def get_default_governance(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
) -> DefaultGovernance:
    with cluster_manager.cache_fixture(key="default_governance") as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        cluster_env = cluster_nodes.get_cluster_env()
        gov_data_dir = cluster_env.state_dir / GOV_DATA_DIR
        gov_data_store = gov_data_dir / GOV_DATA_STORE

        if gov_data_store.exists():
            with open(gov_data_store, "rb") as in_data:
                loaded_gov_data = pickle.load(in_data)
            fixture_cache.value = loaded_gov_data
            return loaded_gov_data  # type: ignore

        with locking.FileLockIfXdist(str(cluster_env.state_dir / f".{GOV_DATA_STORE}.lock")):
            gov_data_dir.mkdir(exist_ok=True, parents=True)

            governance_data = setup(
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
            governance_utils.check_drep_delegation(
                deleg_state=deleg_state, drep_id=drep_id, stake_addr_hash=stake_addr_hash
            )

            fixture_cache.value = governance_data

            with open(gov_data_store, "wb") as out_data:
                pickle.dump(governance_data, out_data)

    return governance_data
