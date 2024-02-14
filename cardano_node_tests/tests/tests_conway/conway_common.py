"""Common functionality for Conway governance tests."""
import dataclasses
import json
import logging
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils

LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, order=True)
class VotedVotes:
    cc: tp.List[clusterlib.VoteCC]  # pylint: disable=invalid-name
    drep: tp.List[clusterlib.VoteDrep]
    spo: tp.List[clusterlib.VoteSPO]


def possible_rem_issue(gov_state: tp.Dict[str, tp.Any], epoch: int) -> bool:
    """Check if the unexpected removed action situation can be result of known ledger issue.

    When the issue manifests, only single expired action gets removed and all other expired or
    ratified actions are ignored int the given epoch.

    See https://github.com/IntersectMBO/cardano-ledger/issues/3979
    """
    removed_actions: tp.List[tp.Dict[str, tp.Any]] = gov_state["nextRatifyState"][
        "expiredGovActions"
    ]
    proposals: tp.List[tp.Dict[str, tp.Any]] = gov_state["proposals"]

    if len(removed_actions) != 1 or len(proposals) == 1:
        return False

    action_txid = removed_actions[0]["txId"]
    action_ix = removed_actions[0]["govActionIx"]

    for _p in proposals:
        _p_action_id = _p["actionId"]
        if (
            _p["expiresAfter"] < epoch
            and _p_action_id["txId"] == action_txid
            and _p_action_id["govActionIx"] == action_ix
        ):
            return True

    return False


def get_yes_abstain_vote(idx: int) -> clusterlib.Votes:
    """Check that votes of DReps who abstained are not considered as "No" votes."""
    if idx % 2 == 0:
        return clusterlib.Votes.YES
    return clusterlib.Votes.ABSTAIN


def save_gov_state(gov_state: tp.Dict[str, tp.Any], name_template: str) -> None:
    """Save governance state to a file."""
    with open(f"{name_template}_gov_state.json", "w", encoding="utf-8") as out_fp:
        json.dump(gov_state, out_fp, indent=2)


def save_committee_state(committee_state: tp.Dict[str, tp.Any], name_template: str) -> None:
    """Save CC state to a file."""
    with open(f"{name_template}_committee_state.json", "w", encoding="utf-8") as out_fp:
        json.dump(committee_state, out_fp, indent=2)


def get_pool_user(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    caching_key: str,
) -> clusterlib.PoolUser:
    """Create a pool user."""
    with cluster_manager.cache_fixture(key=caching_key) as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        test_id = common.get_test_id(cluster_obj)
        pool_user = clusterlib_utils.create_pool_users(
            cluster_obj=cluster_obj,
            name_template=f"{test_id}_pool_user",
            no_of_addr=1,
        )[0]
        fixture_cache.value = pool_user

    # Fund the payment address with some ADA
    clusterlib_utils.fund_from_faucet(
        pool_user.payment,
        cluster_obj=cluster_obj,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )

    # Register the stake address
    stake_deposit_amt = cluster_obj.g_query.get_address_deposit()
    stake_addr_reg_cert = cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
        addr_name=f"{test_id}_pool_user",
        deposit_amt=stake_deposit_amt,
        stake_vkey_file=pool_user.stake.vkey_file,
    )
    tx_files_action = clusterlib.TxFiles(
        certificate_files=[stake_addr_reg_cert],
        signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
    )

    clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{test_id}_pool_user",
        src_address=pool_user.payment.address,
        use_build_cmd=True,
        tx_files=tx_files_action,
    )

    assert cluster_obj.g_query.get_stake_addr_info(
        pool_user.stake.address
    ).address, f"Stake address is not registered: {pool_user.stake.address}"

    return pool_user


def cast_vote(
    cluster_obj: clusterlib.ClusterLib,
    governance_data: governance_setup.DefaultGovernance,
    name_template: str,
    payment_addr: clusterlib.AddressRecord,
    action_txid: str,
    action_ix: int,
    approve_cc: tp.Optional[bool] = None,
    approve_drep: tp.Optional[bool] = None,
    approve_spo: tp.Optional[bool] = None,
    cc_skip_votes: bool = False,
    drep_skip_votes: bool = False,
    spo_skip_votes: bool = False,
) -> VotedVotes:
    """Cast a vote."""
    # pylint: disable=too-many-arguments
    votes_cc = []
    votes_drep = []
    votes_spo = []

    if approve_cc is not None:
        _votes_cc = [
            None  # This CC member doesn't vote, his votes count as "No"
            if cc_skip_votes and i % 3 == 0
            else cluster_obj.g_conway_governance.vote.create_committee(
                vote_name=f"{name_template}_cc{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=get_yes_abstain_vote(i) if approve_cc else clusterlib.Votes.NO,
                cc_hot_vkey_file=m.hot_vkey_file,
                anchor_url=f"http://www.cc-vote{i}.com",
                anchor_data_hash="5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d",
            )
            for i, m in enumerate(governance_data.cc_members, start=1)
        ]
        votes_cc = [v for v in _votes_cc if v]
    if approve_drep is not None:
        _votes_drep = [
            None  # This DRep doesn't vote, his votes count as "No"
            if drep_skip_votes and i % 3 == 0
            else cluster_obj.g_conway_governance.vote.create_drep(
                vote_name=f"{name_template}_drep{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=get_yes_abstain_vote(i) if approve_drep else clusterlib.Votes.NO,
                drep_vkey_file=d.key_pair.vkey_file,
                anchor_url=f"http://www.drep-vote{i}.com",
                anchor_data_hash="5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d",
            )
            for i, d in enumerate(governance_data.dreps_reg, start=1)
        ]
        votes_drep = [v for v in _votes_drep if v]
    if approve_spo is not None:
        _votes_spo = [
            None  # This SPO doesn't vote, his votes count as "No"
            if spo_skip_votes and i % 3 == 0
            else cluster_obj.g_conway_governance.vote.create_spo(
                vote_name=f"{name_template}_pool{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=get_yes_abstain_vote(i) if approve_spo else clusterlib.Votes.NO,
                cold_vkey_file=p.vkey_file,
                anchor_url=f"http://www.spo-vote{i}.com",
                anchor_data_hash="5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d",
            )
            for i, p in enumerate(governance_data.pools_cold, start=1)
        ]
        votes_spo = [v for v in _votes_spo if v]

    cc_keys = [r.hot_skey_file for r in governance_data.cc_members] if votes_cc else []
    drep_keys = [r.key_pair.skey_file for r in governance_data.dreps_reg] if votes_drep else []
    spo_keys = [r.skey_file for r in governance_data.pools_cold] if votes_spo else []

    tx_files = clusterlib.TxFiles(
        vote_files=[
            *[r.vote_file for r in votes_cc],
            *[r.vote_file for r in votes_drep],
            *[r.vote_file for r in votes_spo],
        ],
        signing_key_files=[
            payment_addr.skey_file,
            *cc_keys,
            *drep_keys,
            *spo_keys,
        ],
    )

    # Make sure we have enough time to submit the votes in one epoch
    clusterlib_utils.wait_for_epoch_interval(
        cluster_obj=cluster_obj, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
    )

    tx_output = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_vote_",
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
    _cur_epoch = cluster_obj.g_query.get_epoch()
    save_gov_state(
        gov_state=gov_state,
        name_template=f"{name_template}_vote_{_cur_epoch}",
    )
    prop_vote = governance_utils.lookup_proposal(gov_state=gov_state, action_txid=action_txid)
    assert not votes_cc or prop_vote["committeeVotes"], "No committee votes"
    assert not votes_drep or prop_vote["dRepVotes"], "No DRep votes"
    assert not votes_spo or prop_vote["stakePoolVotes"], "No stake pool votes"

    return VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo)


def resign_ccs(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    ccs_to_resign: tp.List[clusterlib.CCMember],
    payment_addr: clusterlib.AddressRecord,
) -> clusterlib.TxRawOutput:
    """Resign multiple CC Members."""
    res_certs = [
        cluster_obj.g_conway_governance.committee.gen_cold_key_resignation_cert(
            key_name=f"{name_template}_{i}",
            cold_vkey_file=r.cold_vkey_file,
            resignation_metadata_url=f"http://www.cc-resign{i}.com",
            resignation_metadata_hash="5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d",
        )
        for i, r in enumerate(ccs_to_resign, start=1)
    ]

    cc_cold_skeys = [r.cold_skey_file for r in ccs_to_resign]
    tx_files = clusterlib.TxFiles(
        certificate_files=res_certs,
        signing_key_files=[payment_addr.skey_file, *cc_cold_skeys],
    )

    tx_output = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_res",
        src_address=payment_addr.address,
        use_build_cmd=True,
        tx_files=tx_files,
    )

    cluster_obj.wait_for_new_block(new_blocks=2)
    res_committee_state = cluster_obj.g_conway_governance.query.committee_state()
    for cc_member in ccs_to_resign:
        member_key = f"keyHash-{cc_member.cold_vkey_hash}"
        assert (
            res_committee_state["committee"][member_key]["hotCredsAuthStatus"]["tag"]
            == "MemberResigned"
        ), "CC Member not resigned"

    return tx_output


def propose_change_constitution(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    pool_user: clusterlib.PoolUser,
) -> tp.Tuple[clusterlib.ActionConstitution, str, int]:
    """Propose a constitution change."""
    deposit_amt = cluster_obj.conway_genesis["govActionDeposit"]

    anchor_url = "http://www.const-action.com"
    anchor_data_hash = cluster_obj.g_conway_governance.get_anchor_data_hash(text=anchor_url)

    constitution_url = "http://www.const-new.com"
    constitution_hash = cluster_obj.g_conway_governance.get_anchor_data_hash(text=constitution_url)

    prev_action_rec = governance_utils.get_prev_action(
        action_type=governance_utils.PrevGovActionIds.CONSTITUTION,
        gov_state=cluster_obj.g_conway_governance.query.gov_state(),
    )

    constitution_action = cluster_obj.g_conway_governance.action.create_constitution(
        action_name=f"{name_template}_constitution",
        deposit_amt=deposit_amt,
        anchor_url=anchor_url,
        anchor_data_hash=anchor_data_hash,
        constitution_url=constitution_url,
        constitution_hash=constitution_hash,
        prev_action_txid=prev_action_rec.txid,
        prev_action_ix=prev_action_rec.ix,
        deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
    )

    tx_files = clusterlib.TxFiles(
        proposal_files=[constitution_action.action_file],
        signing_key_files=[pool_user.payment.skey_file],
    )

    # Make sure we have enough time to submit the proposal in one epoch
    clusterlib_utils.wait_for_epoch_interval(
        cluster_obj=cluster_obj, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
    )

    tx_output = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_constitution_action",
        src_address=pool_user.payment.address,
        use_build_cmd=True,
        tx_files=tx_files,
    )

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    assert (
        clusterlib.filter_utxos(utxos=out_utxos, address=pool_user.payment.address)[0].amount
        == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
    ), f"Incorrect balance for source address `{pool_user.payment.address}`"

    action_txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_output.out_file)
    action_gov_state = cluster_obj.g_conway_governance.query.gov_state()
    _cur_epoch = cluster_obj.g_query.get_epoch()
    save_gov_state(
        gov_state=action_gov_state,
        name_template=f"{name_template}_constitution_action_{_cur_epoch}",
    )
    prop_action = governance_utils.lookup_proposal(
        gov_state=action_gov_state, action_txid=action_txid
    )
    assert prop_action, "Create constitution action not found"
    assert (
        prop_action["action"]["tag"] == governance_utils.ActionTags.NEW_CONSTITUTION.value
    ), "Incorrect action tag"

    action_ix = prop_action["actionId"]["govActionIx"]

    return constitution_action, action_txid, action_ix
