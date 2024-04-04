import dataclasses
import logging
import pathlib as pl
import pickle
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking

LOGGER = logging.getLogger(__name__)

GOV_DATA_DIR = "governance_data"
GOV_DATA_STORE = "governance_data.pickle"

DREPS_NUM = 5


def _get_committee_val(data: tp.Dict[str, tp.Any]) -> tp.Dict[str, tp.Any]:
    return data.get("committee") or data.get("commitee") or {}


@dataclasses.dataclass(frozen=True, order=True)
class DefaultGovernance:
    dreps_reg: tp.List[governance_utils.DRepRegistration]
    drep_delegators: tp.List[clusterlib.PoolUser]
    cc_members: tp.List[clusterlib.CCMember]
    pools_cold: tp.List[clusterlib.ColdKeyPair]


GovClusterT = tp.Tuple[clusterlib.ClusterLib, DefaultGovernance]


def _cast_vote(
    cluster_obj: clusterlib.ClusterLib,
    governance_data: DefaultGovernance,
    name_template: str,
    payment_addr: clusterlib.AddressRecord,
    action_txid: str,
    action_ix: int,
) -> None:
    """Approve a governace action.

    This is a simplified version meant just for governance setup.
    Use `tests.conway_common.cast_vote` for tests.
    """
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

    tx_files = clusterlib.TxFiles(
        vote_files=[
            *[r.vote_file for r in votes_drep],
            *[r.vote_file for r in votes_spo],
        ],
        signing_key_files=[
            payment_addr.skey_file,
            *[r.skey_file for r in governance_data.pools_cold],
            *[r.key_pair.skey_file for r in governance_data.dreps_reg],
        ],
    )

    # Make sure we have enough time to submit the votes in one epoch
    clusterlib_utils.wait_for_epoch_interval(cluster_obj=cluster_obj, start=1, stop=-40)

    tx_output = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_vote",
        src_address=payment_addr.address,
        use_build_cmd=True,
        tx_files=tx_files,
    )

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    assert (
        clusterlib.filter_utxos(utxos=out_utxos, address=payment_addr.address)[0].amount
        == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee
    ), f"Incorrect balance for source address `{payment_addr.address}`"

    gov_state = cluster_obj.g_conway_governance.query.gov_state()
    prop_vote = governance_utils.lookup_proposal(
        gov_state=gov_state, action_txid=action_txid, action_ix=action_ix
    )
    assert prop_vote["dRepVotes"], "No DRep votes"
    assert prop_vote["stakePoolVotes"], "No stake pool votes"


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
        msg = "Not enough pool users to create drep registrations"
        raise ValueError(msg)

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

    # Check delegation to DReps
    deleg_state = clusterlib_utils.get_delegation_state(cluster_obj=cluster_obj)
    drep_id = drep_reg_records[0].drep_id
    stake_addr_hash = cluster_obj.g_stake_address.get_stake_vkey_hash(
        stake_vkey_file=drep_users[0].stake.vkey_file
    )
    governance_utils.check_drep_delegation(
        deleg_state=deleg_state, drep_id=drep_id, stake_addr_hash=stake_addr_hash
    )

    gov_data = save_default_governance(
        dreps_reg=drep_reg_records,
        drep_delegators=drep_users,
        cc_members=cc_members,
        pools_cold=node_cold_records,
    )

    return gov_data


def get_default_governance(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
) -> DefaultGovernance:
    cluster_env = cluster_nodes.get_cluster_env()
    gov_data_dir = cluster_env.state_dir / GOV_DATA_DIR
    gov_data_store = gov_data_dir / GOV_DATA_STORE
    governance_data = None

    def _setup_gov() -> tp.Optional[DefaultGovernance]:
        with locking.FileLockIfXdist(str(cluster_env.state_dir / f".{GOV_DATA_STORE}.lock")):
            if gov_data_store.exists():
                return None

            return setup(
                cluster_obj=cluster_obj,
                cluster_manager=cluster_manager,
                destination_dir=gov_data_dir,
            )

    if not gov_data_store.exists():
        governance_data = _setup_gov()

    gov_data_checksum = helpers.checksum(gov_data_store)

    with cluster_manager.cache_fixture(key=gov_data_checksum) as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        if not governance_data and gov_data_store.exists():
            with open(gov_data_store, "rb") as in_data:
                governance_data = pickle.load(in_data)

        fixture_cache.value = governance_data
        return fixture_cache.value  # type: ignore


def save_default_governance(
    dreps_reg: tp.List[governance_utils.DRepRegistration],
    drep_delegators: tp.List[clusterlib.PoolUser],
    cc_members: tp.List[clusterlib.CCMember],
    pools_cold: tp.List[clusterlib.ColdKeyPair],
) -> DefaultGovernance:
    """Save governance data to a pickle, so it can be reused.

    This needs to be called either under a file lock (`FileLockIfXdist`), or in a test
    that locked whole governance for itself.
    """
    cluster_env = cluster_nodes.get_cluster_env()
    gov_data_dir = cluster_env.state_dir / GOV_DATA_DIR
    gov_data_store = gov_data_dir / GOV_DATA_STORE

    gov_data = DefaultGovernance(
        dreps_reg=dreps_reg,
        drep_delegators=drep_delegators,
        cc_members=cc_members,
        pools_cold=pools_cold,
    )

    gov_data_dir.mkdir(exist_ok=True, parents=True)
    with open(gov_data_store, "wb") as out_data:
        pickle.dump(gov_data, out_data)

    return gov_data


def refresh_cc_keys(
    cluster_obj: clusterlib.ClusterLib,
    cc_members: tp.List[clusterlib.CCMember],
    governance_data: DefaultGovernance,
) -> DefaultGovernance:
    """Refresh ho certs for original CC members."""
    gov_data_dir = pl.Path(cc_members[0].hot_vkey_file).parent

    new_cc_members = []
    for c in cc_members:
        key_name = pl.Path(c.hot_vkey_file).stem.replace("_committee_hot", "")
        # Until it is possible to revive resigned CC member, we need to create also
        # new cold keys and thus create a completely new CC member.
        committee_cold_keys = cluster_obj.g_conway_governance.committee.gen_cold_key_pair(
            key_name=key_name,
            destination_dir=gov_data_dir,
        )
        committee_hot_keys = cluster_obj.g_conway_governance.committee.gen_hot_key_pair(
            key_name=key_name,
            destination_dir=gov_data_dir,
        )
        cluster_obj.g_conway_governance.committee.gen_hot_key_auth_cert(
            key_name=key_name,
            cold_vkey_file=c.cold_vkey_file,
            hot_key_file=committee_hot_keys.vkey_file,
            destination_dir=gov_data_dir,
        )
        new_cc_members.append(
            clusterlib.CCMember(
                epoch=c.epoch,
                cold_vkey_file=committee_cold_keys.vkey_file,
                cold_vkey_hash=cluster_obj.g_conway_governance.committee.get_key_hash(
                    vkey_file=committee_cold_keys.vkey_file,
                ),
                cold_skey_file=committee_cold_keys.skey_file,
                hot_vkey_file=committee_hot_keys.vkey_file,
                hot_vkey_hash=cluster_obj.g_conway_governance.committee.get_key_hash(
                    vkey_file=committee_hot_keys.vkey_file
                ),
                hot_skey_file=committee_hot_keys.skey_file,
            )
        )

    unchanged_cc_members = set(governance_data.cc_members).difference(cc_members)

    recreated_gov_data = save_default_governance(
        dreps_reg=governance_data.dreps_reg,
        drep_delegators=governance_data.drep_delegators,
        cc_members=[*new_cc_members, *unchanged_cc_members],
        pools_cold=governance_data.pools_cold,
    )

    return recreated_gov_data


def auth_cc_members(
    cluster_obj: clusterlib.ClusterLib,
    cc_members: tp.List[clusterlib.CCMember],
    name_template: str,
    payment_addr: clusterlib.AddressRecord,
) -> None:
    """Authorize the original CC members."""
    assert cc_members, "No CC members found"
    gov_data_dir = pl.Path(cc_members[0].hot_vkey_file).parent

    hot_auth_certs = []
    for c in cc_members:
        stem = pl.Path(c.hot_vkey_file).stem
        hot_auth_certs.append(gov_data_dir / f"{stem}_auth.cert")

    tx_files = clusterlib.TxFiles(
        certificate_files=hot_auth_certs,
        signing_key_files=[
            payment_addr.skey_file,
            *[r.cold_skey_file for r in cc_members],
        ],
    )

    tx_output = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_cc_auth",
        src_address=payment_addr.address,
        use_build_cmd=True,
        tx_files=tx_files,
    )

    reg_out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    assert (
        clusterlib.filter_utxos(utxos=reg_out_utxos, address=payment_addr.address)[0].amount
        == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee
    ), f"Incorrect balance for source address `{payment_addr.address}`"

    reg_committee_state = cluster_obj.g_conway_governance.query.committee_state()
    member_key = f"keyHash-{cc_members[0].cold_vkey_hash}"
    assert (
        _get_committee_val(data=reg_committee_state)[member_key]["hotCredsAuthStatus"]["tag"]
        == "MemberAuthorized"
    ), "CC Member was not authorized"


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
        quorum=str(cluster_obj.conway_genesis["committee"]["threshold"]),
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
        prop_action["proposalProcedure"]["govAction"]["tag"]
        == governance_utils.ActionTags.UPDATE_COMMITTEE.value
    ), "Incorrect action tag"

    action_ix = prop_action["actionId"]["govActionIx"]

    # Vote & approve the action
    _cast_vote(
        cluster_obj=cluster_obj,
        governance_data=governance_data,
        name_template=name_template,
        payment_addr=pool_user.payment,
        action_txid=action_txid,
        action_ix=action_ix,
    )

    def _check_state(state: dict) -> None:
        cc_member = governance_data.cc_members[0]
        cc_member_val = _get_committee_val(data=state)["members"].get(
            f"keyHash-{cc_member.cold_vkey_hash}"
        )
        assert cc_member_val, "New committee member not found"
        assert cc_member_val == cc_member.epoch

    # Check ratification
    _cur_epoch = cluster_obj.wait_for_new_epoch(padding_seconds=5)
    rat_gov_state = cluster_obj.g_conway_governance.query.gov_state()
    rat_action = governance_utils.lookup_ratified_actions(
        gov_state=rat_gov_state, action_txid=action_txid
    )
    assert rat_action, "Action not found in ratified actions"

    next_rat_state = rat_gov_state["nextRatifyState"]
    _check_state(next_rat_state["nextEnactState"])
    assert next_rat_state["ratificationDelayed"], "Ratification not delayed"

    # Check enactment
    _cur_epoch = cluster_obj.wait_for_new_epoch(padding_seconds=5)
    enact_gov_state = cluster_obj.g_conway_governance.query.gov_state()
    _check_state(enact_gov_state)

    auth_cc_members(
        cluster_obj=cluster_obj,
        cc_members=governance_data.cc_members,
        name_template=name_template,
        payment_addr=pool_user.payment,
    )
