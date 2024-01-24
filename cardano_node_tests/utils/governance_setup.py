import dataclasses
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

DREPS_NUM = 5


@dataclasses.dataclass(frozen=True, order=True)
class DefaultGovernance:
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


def reinstate_committee(
    cluster_obj: clusterlib.ClusterLib,
    governance_data: DefaultGovernance,
    name_template: str,
    pool_user: clusterlib.PoolUser,
) -> None:
    """Reinstate the original CC members."""
    # pylint: disable=too-many-statements
    # Create an "update committee" action

    deposit_amt = cluster_obj.conway_genesis["govActionDeposit"]
    anchor_url = "http://www.cc-reinstate.com"
    anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"
    prev_action_rec = governance_utils.get_prev_action(
        action_type=governance_utils.PrevGovActionIds.COMMITTEE,
        gov_state=cluster_obj.g_conway_governance.query.gov_state(),
    )

    update_action = cluster_obj.g_conway_governance.action.update_committee(
        action_name=name_template,
        deposit_amt=deposit_amt,
        anchor_url=anchor_url,
        anchor_data_hash=anchor_data_hash,
        quorum=str(cluster_obj.conway_genesis["committee"]["quorum"]),
        add_cc_members=governance_data.cc_members,
        prev_action_txid=prev_action_rec.txid,
        prev_action_ix=prev_action_rec.ix,
        deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
    )

    tx_files_action = clusterlib.TxFiles(
        proposal_files=[update_action.action_file],
        signing_key_files=[pool_user.payment.skey_file],
    )

    # Make sure we have enough time to submit the proposal in one epoch
    clusterlib_utils.wait_for_epoch_interval(cluster_obj=cluster_obj, start=1, stop=-40)

    tx_output_action = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_action",
        src_address=pool_user.payment.address,
        use_build_cmd=True,
        tx_files=tx_files_action,
    )

    out_utxos_action = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output_action)
    assert (
        clusterlib.filter_utxos(utxos=out_utxos_action, address=pool_user.payment.address)[0].amount
        == clusterlib.calculate_utxos_balance(tx_output_action.txins)
        - tx_output_action.fee
        - deposit_amt
    ), f"Incorrect balance for source address `{pool_user.payment.address}`"

    action_txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
    action_gov_state = cluster_obj.g_conway_governance.query.gov_state()
    _cur_epoch = cluster_obj.g_query.get_epoch()
    prop_action = governance_utils.lookup_proposal(
        gov_state=action_gov_state, action_txid=action_txid
    )
    assert prop_action, "Update committee action not found"
    assert (
        prop_action["action"]["tag"] == governance_utils.ActionTags.UPDATE_COMMITTEE.value
    ), "Incorrect action tag"

    action_ix = prop_action["actionId"]["govActionIx"]

    def _cast_vote() -> None:
        votes_drep = [
            cluster_obj.g_conway_governance.vote.create_drep(
                vote_name=f"{name_template}_drep{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=clusterlib.Votes.YES,
                drep_vkey_file=d.key_pair.vkey_file,
            )
            for i, d in enumerate(governance_data.dreps_reg, start=1)
        ]
        votes_spo = [
            cluster_obj.g_conway_governance.vote.create_spo(
                vote_name=f"{name_template}_pool{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=clusterlib.Votes.YES,
                cold_vkey_file=p.vkey_file,
            )
            for i, p in enumerate(governance_data.pools_cold, start=1)
        ]

        tx_files_vote = clusterlib.TxFiles(
            vote_files=[
                *[r.vote_file for r in votes_drep],
                *[r.vote_file for r in votes_spo],
            ],
            signing_key_files=[
                pool_user.payment.skey_file,
                *[r.skey_file for r in governance_data.pools_cold],
                *[r.key_pair.skey_file for r in governance_data.dreps_reg],
            ],
        )

        # Make sure we have enough time to submit the votes in one epoch
        clusterlib_utils.wait_for_epoch_interval(cluster_obj=cluster_obj, start=1, stop=-40)

        tx_output_vote = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster_obj,
            name_template=f"{name_template}_vote",
            src_address=pool_user.payment.address,
            use_build_cmd=True,
            tx_files=tx_files_vote,
        )

        out_utxos_vote = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output_vote)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos_vote, address=pool_user.payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output_vote.txins) - tx_output_vote.fee
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        vote_gov_state = cluster_obj.g_conway_governance.query.gov_state()
        _cur_epoch = cluster_obj.g_query.get_epoch()
        prop_vote = governance_utils.lookup_proposal(
            gov_state=vote_gov_state, action_txid=action_txid
        )
        assert not prop_vote["committeeVotes"], "Unexpected committee votes"
        assert prop_vote["dRepVotes"], "No DRep votes"
        assert prop_vote["stakePoolVotes"], "No stake pool votes"

    # Vote & approve the action
    _cast_vote()

    def _check_state(state: dict) -> None:
        cc_member = governance_data.cc_members[0]
        cc_member_val = state["committee"]["members"].get(f"keyHash-{cc_member.cold_vkey_hash}")
        assert cc_member_val, "New committee member not found"
        assert cc_member_val == cc_member.epoch

    # Check ratification
    _cur_epoch = cluster_obj.wait_for_new_epoch(padding_seconds=5)
    rat_gov_state = cluster_obj.g_conway_governance.query.gov_state()
    rem_action = governance_utils.lookup_removed_actions(
        gov_state=rat_gov_state, action_txid=action_txid
    )
    assert rem_action, "Action not found in removed actions"

    next_rat_state = rat_gov_state["nextRatifyState"]
    _check_state(next_rat_state["nextEnactState"])
    assert next_rat_state["ratificationDelayed"], "Ratification not delayed"

    # Check enactment
    _cur_epoch = cluster_obj.wait_for_new_epoch(padding_seconds=5)
    enact_gov_state = cluster_obj.g_conway_governance.query.gov_state()
    _check_state(enact_gov_state["enactState"])

    # Authorize CC members

    gov_data_dir = cluster_nodes.get_cluster_env().state_dir / GOV_DATA_DIR
    hot_auth_certs = list(gov_data_dir.glob("cc_member*_committee_hot_auth.cert"))
    assert hot_auth_certs, "No certs found"

    tx_files_auth = clusterlib.TxFiles(
        certificate_files=hot_auth_certs,
        signing_key_files=[
            pool_user.payment.skey_file,
            *[r.cold_skey_file for r in governance_data.cc_members],
        ],
    )

    tx_output_auth = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_cc_auth",
        src_address=pool_user.payment.address,
        use_build_cmd=True,
        tx_files=tx_files_auth,
    )

    reg_out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output_auth)
    assert (
        clusterlib.filter_utxos(utxos=reg_out_utxos, address=pool_user.payment.address)[0].amount
        == clusterlib.calculate_utxos_balance(tx_output_auth.txins) - tx_output_auth.fee
    ), f"Incorrect balance for source address `{pool_user.payment.address}`"

    reg_committee_state = cluster_obj.g_conway_governance.query.committee_state()
    member_key = f"keyHash-{governance_data.cc_members[0].cold_vkey_hash}"
    assert (
        reg_committee_state["committee"][member_key]["hotCredsAuthStatus"]["tag"]
        == "MemberAuthorized"
    ), "CC Member was not authorized"
