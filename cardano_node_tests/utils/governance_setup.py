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


def _get_committee_val(data: dict[str, tp.Any]) -> dict[str, tp.Any]:
    return dict(data.get("committee") or data.get("commitee") or {})


def _cast_vote(
    cluster_obj: clusterlib.ClusterLib,
    governance_data: governance_utils.GovernanceRecords,
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
        cluster_obj.g_governance.vote.create_drep(
            vote_name=f"{name_template}_drep{i}",
            action_txid=action_txid,
            action_ix=action_ix,
            vote=clusterlib.Votes.YES,
            drep_vkey_file=d.key_pair.vkey_file,
        )
        for i, d in enumerate(governance_data.dreps_reg, start=1)
    ]
    votes_spo = [
        cluster_obj.g_governance.vote.create_spo(
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
    expected_balance = clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee
    filtered_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=payment_addr.address)
    if not filtered_utxos:
        err = f"No UTxOs found for address `{payment_addr.address}`"
        raise RuntimeError(err)
    actual_balance = filtered_utxos[0].amount
    if actual_balance != expected_balance:
        err = f"Incorrect balance for source address `{payment_addr.address}`"
        raise RuntimeError(err)

    gov_state = cluster_obj.g_query.get_gov_state()
    prop_vote = governance_utils.lookup_proposal(
        gov_state=gov_state, action_txid=action_txid, action_ix=action_ix
    )

    if not prop_vote["dRepVotes"]:
        err = "No DRep votes."
        raise RuntimeError(err)

    if not prop_vote["stakePoolVotes"]:
        err = "No stake pool votes."
        raise RuntimeError(err)


def create_vote_stake(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    no_of_addr: int,
    destination_dir: clusterlib.FileType = ".",
) -> list[clusterlib.PoolUser]:
    pool_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster_obj,
        name_template=name_template,
        no_of_addr=no_of_addr,
        destination_dir=destination_dir,
    )

    # Fund the payment address with some ADA
    clusterlib_utils.fund_from_faucet(
        *[u.payment for u in pool_users],
        cluster_obj=cluster_obj,
        faucet_data=cluster_manager.cache.addrs_data["faucet"],
        amount=500_000_000_000,
        destination_dir=destination_dir,
    )

    return pool_users


def load_committee(cluster_obj: clusterlib.ClusterLib) -> list[governance_utils.CCKeyMember]:
    genesis_cc_members = cluster_obj.conway_genesis.get("committee", {}).get("members") or {}
    if not genesis_cc_members:
        return []

    data_dir = cluster_obj.state_dir / GOV_DATA_DIR

    cc_members = []
    for vkey_file in sorted(data_dir.glob("cc_member*_committee_cold.vkey")):
        fpath = vkey_file.parent
        fbase = vkey_file.name.replace("cold.vkey", "")
        hot_vkey_file = fpath / f"{fbase}hot.vkey"
        cold_vkey_hash = cluster_obj.g_governance.committee.get_key_hash(vkey_file=vkey_file)
        genesis_epoch = genesis_cc_members[f"keyHash-{cold_vkey_hash}"]
        cc_members.append(
            governance_utils.CCKeyMember(
                cc_member=clusterlib.CCMember(
                    epoch=genesis_epoch,
                    cold_vkey_file=vkey_file,
                    cold_vkey_hash=cold_vkey_hash,
                    cold_skey_file=fpath / f"{fbase}cold.skey",
                ),
                hot_keys=governance_utils.CCHotKeys(
                    hot_vkey_file=hot_vkey_file,
                    hot_vkey_hash=cluster_obj.g_governance.committee.get_key_hash(
                        vkey_file=hot_vkey_file
                    ),
                    hot_skey_file=fpath / f"{fbase}hot.skey",
                ),
            )
        )

    return cc_members


def load_dreps(cluster_obj: clusterlib.ClusterLib) -> list[governance_utils.DRepRegistration]:
    """Load DReps from the state directory."""
    data_dir = cluster_obj.state_dir / GOV_DATA_DIR
    deposit_amt = cluster_obj.g_query.get_drep_deposit()

    dreps = []
    for vkey_file in sorted(data_dir.glob("default_drep_*_drep.vkey")):
        skey_file = vkey_file.with_suffix(".skey")
        fpath = vkey_file.parent
        reg_cert = fpath / vkey_file.name.replace(".vkey", "_reg.cert")
        drep_id = cluster_obj.g_governance.drep.get_id(
            drep_vkey_file=vkey_file,
            out_format="hex",
        )
        dreps.append(
            governance_utils.DRepRegistration(
                registration_cert=reg_cert,
                key_pair=clusterlib.KeyPair(vkey_file=vkey_file, skey_file=skey_file),
                drep_id=drep_id,
                deposit=deposit_amt,
            )
        )

    return dreps


def load_drep_users(cluster_obj: clusterlib.ClusterLib) -> list[clusterlib.PoolUser]:
    """Load DReps users from the state directory."""
    data_dir = cluster_obj.state_dir / GOV_DATA_DIR

    users = []
    for stake_vkey_file in sorted(data_dir.glob("vote_stake_addr*_stake.vkey")):
        fpath = stake_vkey_file.parent

        stake_skey_file = stake_vkey_file.with_suffix(".skey")
        stake_address = clusterlib.read_address_from_file(stake_vkey_file.with_suffix(".addr"))

        payment_vkey_file = fpath / stake_vkey_file.name.replace("_stake.vkey", ".vkey")
        payment_skey_file = payment_vkey_file.with_suffix(".skey")
        payment_address = clusterlib.read_address_from_file(payment_vkey_file.with_suffix(".addr"))

        users.append(
            clusterlib.PoolUser(
                payment=clusterlib.AddressRecord(
                    address=payment_address,
                    vkey_file=payment_vkey_file,
                    skey_file=payment_skey_file,
                ),
                stake=clusterlib.AddressRecord(
                    address=stake_address, vkey_file=stake_vkey_file, skey_file=stake_skey_file
                ),
            )
        )

    return users


def setup(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
) -> governance_utils.GovernanceRecords:
    cc_members = load_committee(cluster_obj=cluster_obj)
    drep_reg_records = load_dreps(cluster_obj=cluster_obj)
    drep_users = load_drep_users(cluster_obj=cluster_obj)
    node_cold_records = [
        cluster_manager.cache.addrs_data[pn]["cold_key_pair"]
        for pn in cluster_management.Resources.ALL_POOLS
    ]

    gov_data = save_default_governance(
        dreps_reg=drep_reg_records,
        drep_delegators=drep_users,
        cc_members=cc_members,
        pools_cold=node_cold_records,
    )

    # When using "fast" cluster, we need to wait for at least epoch 1 for DReps
    # to be usable. DReps don't vote in PV9.
    if (
        drep_reg_records
        and cluster_obj.g_query.get_protocol_params()["protocolVersion"]["major"] >= 10
    ):
        cluster_obj.wait_for_epoch(epoch_no=1, padding_seconds=5)

        drep1_rec = cluster_obj.g_query.get_drep_stake_distribution(
            drep_vkey_file=drep_reg_records[0].key_pair.vkey_file
        )
        if not drep1_rec:
            msg = "DRep stake distribution not found."
            raise RuntimeError(msg)

    return gov_data


def get_default_governance(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
) -> governance_utils.GovernanceRecords:
    """Get default governance data for CC members, DReps and SPOs."""
    if cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.TESTNET:
        err = "Default governance is not available on testnets"
        raise ValueError(err)

    cluster_env = cluster_nodes.get_cluster_env()
    gov_data_dir = cluster_env.state_dir / GOV_DATA_DIR
    gov_data_store = gov_data_dir / GOV_DATA_STORE
    governance_data: governance_utils.GovernanceRecords | None = None

    def _setup_gov() -> governance_utils.GovernanceRecords | None:
        if gov_data_store.exists():
            return None

        gov_records = setup(
            cluster_obj=cluster_obj,
            cluster_manager=cluster_manager,
        )

        if not gov_data_store.exists():
            msg = f"File `{gov_data_store}` not found"
            raise FileNotFoundError(msg)

        return gov_records

    with locking.FileLockIfXdist(str(cluster_env.state_dir / f".{GOV_DATA_STORE}.lock")):
        governance_data = _setup_gov()

    gov_data_checksum = helpers.checksum(gov_data_store)

    fixture_cache: cluster_management.FixtureCache[governance_utils.GovernanceRecords | None]
    with cluster_manager.cache_fixture(key=gov_data_checksum) as fixture_cache:
        if fixture_cache.value is not None:
            return fixture_cache.value

        if governance_data is None:
            with open(gov_data_store, "rb") as in_data:
                governance_data = pickle.load(in_data)

        fixture_cache.value = governance_data
        return fixture_cache.value


def save_default_governance(
    dreps_reg: list[governance_utils.DRepRegistration],
    drep_delegators: list[clusterlib.PoolUser],
    cc_members: list[governance_utils.CCKeyMember],
    pools_cold: list[clusterlib.ColdKeyPair],
) -> governance_utils.GovernanceRecords:
    """Save governance data to a pickle, so it can be reused.

    This needs to be called either under a file lock (`FileLockIfXdist`), or in a test
    that locked whole governance for itself.
    """
    cluster_env = cluster_nodes.get_cluster_env()
    gov_data_dir = cluster_env.state_dir / GOV_DATA_DIR
    gov_data_store = gov_data_dir / GOV_DATA_STORE

    gov_data = governance_utils.GovernanceRecords(
        dreps_reg=dreps_reg,
        drep_delegators=drep_delegators,
        cc_key_members=cc_members,
        pools_cold=pools_cold,
    )

    gov_data_dir.mkdir(exist_ok=True, parents=True)
    with open(gov_data_store, "wb") as out_data:
        pickle.dump(gov_data, out_data)

    return gov_data


def refresh_cc_keys(
    cluster_obj: clusterlib.ClusterLib,
    cc_members: list[governance_utils.CCKeyMember],
    governance_data: governance_utils.GovernanceRecords,
) -> governance_utils.GovernanceRecords:
    """Refresh hot certs for original CC members."""
    gov_data_dir = pl.Path(cc_members[0].hot_keys.hot_vkey_file).parent

    new_cc_members = []
    for c in cc_members:
        key_name = pl.Path(c.hot_keys.hot_vkey_file).stem.replace("_committee_hot", "")
        # Until it is possible to revive resigned CC member, we need to create also
        # new cold keys and thus create a completely new CC member.
        committee_cold_keys = cluster_obj.g_governance.committee.gen_cold_key_pair(
            key_name=key_name,
            destination_dir=gov_data_dir,
        )
        committee_hot_keys = cluster_obj.g_governance.committee.gen_hot_key_pair(
            key_name=key_name,
            destination_dir=gov_data_dir,
        )
        cluster_obj.g_governance.committee.gen_hot_key_auth_cert(
            key_name=key_name,
            cold_vkey_file=c.cc_member.cold_vkey_file,
            hot_key_file=committee_hot_keys.vkey_file,
            destination_dir=gov_data_dir,
        )
        new_cc_members.append(
            governance_utils.CCKeyMember(
                cc_member=clusterlib.CCMember(
                    epoch=c.cc_member.epoch,
                    cold_vkey_file=committee_cold_keys.vkey_file,
                    cold_vkey_hash=cluster_obj.g_governance.committee.get_key_hash(
                        vkey_file=committee_cold_keys.vkey_file,
                    ),
                    cold_skey_file=committee_cold_keys.skey_file,
                ),
                hot_keys=governance_utils.CCHotKeys(
                    hot_vkey_file=committee_hot_keys.vkey_file,
                    hot_vkey_hash=cluster_obj.g_governance.committee.get_key_hash(
                        vkey_file=committee_hot_keys.vkey_file
                    ),
                    hot_skey_file=committee_hot_keys.skey_file,
                ),
            )
        )

    unchanged_cc_members = set(governance_data.cc_key_members).difference(cc_members)

    recreated_gov_data = save_default_governance(
        dreps_reg=governance_data.dreps_reg,
        drep_delegators=governance_data.drep_delegators,
        cc_members=[*new_cc_members, *unchanged_cc_members],
        pools_cold=governance_data.pools_cold,
    )

    return recreated_gov_data


def auth_cc_members(
    cluster_obj: clusterlib.ClusterLib,
    cc_members: list[governance_utils.CCKeyMember],
    name_template: str,
    payment_addr: clusterlib.AddressRecord,
) -> None:
    """Authorize the original CC members."""
    if not cc_members:
        msg = "No CC members found."
        raise ValueError(msg)

    gov_data_dir = pl.Path(cc_members[0].hot_keys.hot_vkey_file).parent

    hot_auth_certs = []
    for c in cc_members:
        stem = pl.Path(c.hot_keys.hot_vkey_file).stem
        hot_auth_certs.append(gov_data_dir / f"{stem}_auth.cert")

    tx_files = clusterlib.TxFiles(
        certificate_files=hot_auth_certs,
        signing_key_files=[
            payment_addr.skey_file,
            *[r.cc_member.cold_skey_file for r in cc_members],
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
    filtered_utxos = clusterlib.filter_utxos(utxos=reg_out_utxos, address=payment_addr.address)
    if not filtered_utxos:
        msg = f"No UTxOs found for address `{payment_addr.address}`."
        raise RuntimeError(msg)
    if (
        filtered_utxos[0].amount
        != clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee
    ):
        msg = f"Incorrect balance for source address `{payment_addr.address}`."
        raise RuntimeError(msg)

    cluster_obj.wait_for_new_block(new_blocks=2)
    reg_committee_state = cluster_obj.g_query.get_committee_state()
    member_key = f"keyHash-{cc_members[0].cc_member.cold_vkey_hash}"
    if (
        _get_committee_val(data=reg_committee_state)[member_key]["hotCredsAuthStatus"]["tag"]
        != "MemberAuthorized"
    ):
        msg = "CC Member was not authorized."
        raise RuntimeError(msg)


def reinstate_committee(  # noqa: C901
    cluster_obj: clusterlib.ClusterLib,
    governance_data: governance_utils.GovernanceRecords,
    name_template: str,
    pool_user: clusterlib.PoolUser,
) -> None:
    """Reinstate the original CC members."""
    # Create an "update committee" action

    deposit_amt = cluster_obj.g_query.get_gov_action_deposit()
    anchor_data = governance_utils.get_default_anchor_data()
    prev_action_rec = governance_utils.get_prev_action(
        action_type=governance_utils.PrevGovActionIds.COMMITTEE,
        gov_state=cluster_obj.g_query.get_gov_state(),
    )

    update_action = cluster_obj.g_governance.action.update_committee(
        action_name=name_template,
        deposit_amt=deposit_amt,
        anchor_url=anchor_data.url,
        anchor_data_hash=anchor_data.hash,
        threshold=str(cluster_obj.conway_genesis["committee"]["threshold"]),
        add_cc_members=[r.cc_member for r in governance_data.cc_key_members],
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
    init_epoch = cluster_obj.g_query.get_epoch()

    tx_output_action = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_action",
        src_address=pool_user.payment.address,
        use_build_cmd=True,
        tx_files=tx_files_action,
    )

    out_utxos_action = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output_action)
    filtered_utxos = clusterlib.filter_utxos(
        utxos=out_utxos_action, address=pool_user.payment.address
    )
    if not filtered_utxos:
        msg = f"No UTxOs found for address `{pool_user.payment.address}`."
        raise RuntimeError(msg)
    if (
        filtered_utxos[0].amount
        != clusterlib.calculate_utxos_balance(tx_output_action.txins)
        - tx_output_action.fee
        - deposit_amt
    ):
        msg = f"Incorrect balance for source address `{pool_user.payment.address}`."
        raise RuntimeError(msg)

    action_txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
    action_gov_state = cluster_obj.g_query.get_gov_state()
    prop_action = governance_utils.lookup_proposal(
        gov_state=action_gov_state, action_txid=action_txid
    )
    if not prop_action:
        msg = "Update committee action not found."
        raise RuntimeError(msg)

    if (
        prop_action["proposalProcedure"]["govAction"]["tag"]
        != governance_utils.ActionTags.UPDATE_COMMITTEE.value
    ):
        msg = "Incorrect action tag."
        raise RuntimeError(msg)

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

    if cluster_obj.g_query.get_epoch() != init_epoch:
        msg = "Epoch changed."
        raise RuntimeError(msg)

    def _check_state(state: dict) -> None:
        cc_key_member = governance_data.cc_key_members[0]
        cc_member_val = _get_committee_val(data=state)["members"].get(
            f"keyHash-{cc_key_member.cc_member.cold_vkey_hash}"
        )
        if not cc_member_val:
            msg = "New committee member not found."
            raise RuntimeError(msg)
        if cc_member_val != cc_key_member.cc_member.epoch:
            msg = "Epoch of the new committee member does not match."
            raise RuntimeError(msg)

    # Check ratification
    cluster_obj.wait_for_epoch(epoch_no=init_epoch + 1, padding_seconds=5)
    rat_gov_state = cluster_obj.g_query.get_gov_state()
    rat_action = governance_utils.lookup_ratified_actions(
        gov_state=rat_gov_state, action_txid=action_txid
    )
    if not rat_action:
        msg = "Action not found in ratified actions."
        raise RuntimeError(msg)

    next_rat_state = rat_gov_state["nextRatifyState"]
    _check_state(next_rat_state["nextEnactState"])
    if not next_rat_state["ratificationDelayed"]:
        msg = "Ratification not delayed."
        raise RuntimeError(msg)

    # Check enactment
    cluster_obj.wait_for_epoch(epoch_no=init_epoch + 2, padding_seconds=5)
    enact_gov_state = cluster_obj.g_query.get_gov_state()
    _check_state(enact_gov_state)

    auth_cc_members(
        cluster_obj=cluster_obj,
        cc_members=governance_data.cc_key_members,
        name_template=name_template,
        payment_addr=pool_user.payment,
    )
