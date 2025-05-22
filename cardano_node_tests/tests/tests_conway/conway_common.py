"""Common functionality for Conway governance tests."""

import dataclasses
import itertools
import json
import logging
import pathlib as pl
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import web

LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class PParamPropRec:
    proposals: list[clusterlib_utils.UpdateProposal]
    action_txid: str
    action_ix: int
    proposal_names: set[str]
    future_pparams: dict[str, tp.Any]


def is_in_bootstrap(
    cluster_obj: clusterlib.ClusterLib,
) -> bool:
    """Check if the cluster is in bootstrap period."""
    pv = cluster_obj.g_query.get_protocol_params()["protocolVersion"]["major"]
    return bool(pv == 9)


def get_committee_val(data: dict[str, tp.Any]) -> dict[str, tp.Any]:
    """Get the committee value from the data.

    The key can be either correctly "committee", or with typo "commitee".
    TODO: Remove this function when the typo is fixed in the ledger.
    """
    return dict(data.get("committee") or data.get("commitee") or {})


def possible_rem_issue(gov_state: dict[str, tp.Any], epoch: int) -> bool:
    """Check if the unexpected removed action situation can be result of known ledger issue.

    When the issue manifests, only single expired action gets removed and all other expired or
    ratified actions are ignored int the given epoch.

    See https://github.com/IntersectMBO/cardano-ledger/issues/3979
    """
    removed_actions: list[dict[str, tp.Any]] = gov_state["nextRatifyState"]["expiredGovActions"]
    proposals: list[dict[str, tp.Any]] = gov_state["proposals"]

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
    if idx == 1 or idx % 2 == 0:
        return clusterlib.Votes.YES
    if idx % 3 == 0:
        return clusterlib.Votes.NO
    return clusterlib.Votes.ABSTAIN


def get_no_abstain_vote(idx: int) -> clusterlib.Votes:
    """Check that votes of DReps who abstained are not considered as "No" votes."""
    if idx == 1 or idx % 2 == 0:
        return clusterlib.Votes.NO
    if idx % 3 == 0:
        return clusterlib.Votes.YES
    return clusterlib.Votes.ABSTAIN


def save_gov_state(gov_state: dict[str, tp.Any], name_template: str) -> None:
    """Save governance state to a file."""
    with open(f"{name_template}_gov_state.json", "w", encoding="utf-8") as out_fp:
        json.dump(gov_state, out_fp, indent=2)


def save_committee_state(committee_state: dict[str, tp.Any], name_template: str) -> None:
    """Save CC state to a file."""
    with open(f"{name_template}_committee_state.json", "w", encoding="utf-8") as out_fp:
        json.dump(committee_state, out_fp, indent=2)


def save_drep_state(drep_state: governance_utils.DRepStateT, name_template: str) -> None:
    """Save DRep state to a file."""
    with open(f"{name_template}_drep_state.json", "w", encoding="utf-8") as out_fp:
        json.dump(drep_state, out_fp, indent=2)


def submit_vote(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    payment_addr: clusterlib.AddressRecord,
    votes: tp.Sequence[governance_utils.VotesAllT],
    keys: list[clusterlib.FileType],
    script_votes: clusterlib.OptionalScriptVotes = (),
    submit_method: str = "",
    use_build_cmd: bool = True,
    witness_count_add: int = 0,
) -> clusterlib.TxRawOutput:
    """Submit a Tx with votes."""
    tx_files = clusterlib.TxFiles(
        vote_files=[r.vote_file for r in votes],
        signing_key_files=[
            payment_addr.skey_file,
            *keys,
        ],
    )

    tx_output = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_vote",
        src_address=payment_addr.address,
        submit_method=submit_method,
        use_build_cmd=use_build_cmd,
        tx_files=tx_files,
        script_votes=script_votes,
        witness_count_add=witness_count_add,
    )

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    assert (
        clusterlib.filter_utxos(utxos=out_utxos, address=payment_addr.address)[0].amount
        == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee
    ), f"Incorrect balance for source address `{payment_addr.address}`"

    return tx_output


def cast_vote(
    cluster_obj: clusterlib.ClusterLib,
    governance_data: governance_utils.GovernanceRecords,
    name_template: str,
    payment_addr: clusterlib.AddressRecord,
    action_txid: str,
    action_ix: int,
    approve_cc: bool | None = None,
    approve_drep: bool | None = None,
    approve_spo: bool | None = None,
    cc_skip_votes: bool = False,
    drep_skip_votes: bool = False,
    spo_skip_votes: bool = False,
    use_build_cmd: bool = True,
    witness_count_add: int = 0,
) -> governance_utils.VotedVotes:
    """Cast a vote."""
    votes_cc = []
    votes_drep = []  # All DRep votes
    votes_drep_keys = []  # DRep votes with key
    votes_drep_scripts = []  # DRep votes with script
    votes_spo = []
    anchor_data = governance_utils.get_default_anchor_data()

    if approve_cc is not None:
        _votes_cc = [
            None  # This CC member doesn't vote, his votes count as "No"
            if cc_skip_votes and i % 3 == 0
            else cluster_obj.g_governance.vote.create_committee(
                vote_name=f"{name_template}_cc{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=get_yes_abstain_vote(i) if approve_cc else get_no_abstain_vote(i),
                cc_hot_vkey_file=m.hot_keys.hot_vkey_file,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
            )
            for i, m in enumerate(governance_data.cc_key_members, start=1)
        ]
        votes_cc = [v for v in _votes_cc if v]

    if approve_drep is not None:
        _votes_drep_keys = [
            None  # This DRep doesn't vote, his votes count as "No"
            if drep_skip_votes and i % 3 == 0
            else cluster_obj.g_governance.vote.create_drep(
                vote_name=f"{name_template}_drep{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=get_yes_abstain_vote(i) if approve_drep else get_no_abstain_vote(i),
                drep_vkey_file=d.key_pair.vkey_file,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
            )
            for i, d in enumerate(governance_data.dreps_reg, start=1)
        ]
        votes_drep_scripts = [
            None  # This DRep doesn't vote, his votes count as "No"
            if drep_skip_votes and i % 3 == 0
            else cluster_obj.g_governance.vote.create_drep(
                vote_name=f"{name_template}_sdrep{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=get_yes_abstain_vote(i) if approve_drep else get_no_abstain_vote(i),
                drep_script_hash=d.script_hash,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
            )
            for i, d in enumerate(governance_data.drep_scripts_reg, start=1)
        ]
        votes_drep_keys = [v for v in _votes_drep_keys if v]
        _votes_drep_scripts = [v for v in votes_drep_scripts if v]
        votes_drep = [*votes_drep_keys, *_votes_drep_scripts]

    if approve_spo is not None:
        _votes_spo = [
            None  # This SPO doesn't vote, his votes count as "No"
            if spo_skip_votes and i % 3 == 0
            else cluster_obj.g_governance.vote.create_spo(
                vote_name=f"{name_template}_pool{i}",
                action_txid=action_txid,
                action_ix=action_ix,
                vote=get_yes_abstain_vote(i) if approve_spo else get_no_abstain_vote(i),
                cold_vkey_file=p.vkey_file,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
            )
            for i, p in enumerate(governance_data.pools_cold, start=1)
        ]
        votes_spo = [v for v in _votes_spo if v]

    cc_keys = [r.hot_keys.hot_skey_file for r in governance_data.cc_key_members] if votes_cc else []
    drep_keys = [r.key_pair.skey_file for r in governance_data.dreps_reg] if votes_drep_keys else []
    drep_script_key_pairs = itertools.chain.from_iterable(
        [r.key_pairs for r in governance_data.drep_scripts_reg] if votes_drep_scripts else []
    )
    drep_script_witnesses = [r.skey_file for r in drep_script_key_pairs]
    spo_keys = [r.skey_file for r in governance_data.pools_cold] if votes_spo else []

    votes_simple: list[governance_utils.VotesAllT] = [*votes_cc, *votes_drep_keys, *votes_spo]
    keys_all = [*cc_keys, *drep_keys, *drep_script_witnesses, *spo_keys]

    script_votes: list[clusterlib.ScriptVote] = []

    if votes_drep_scripts:
        drep_script_reg_certs = [r.registration_cert for r in governance_data.drep_scripts_reg]
        script_votes = [
            clusterlib.ScriptVote(
                vote_file=v.vote_file,
                script_file=r.script_file,
                collaterals=r.collaterals,
                execution_units=r.execution_units,
                redeemer_file=r.redeemer_file,
                redeemer_cbor_file=r.redeemer_cbor_file,
                redeemer_value=r.redeemer_value,
            )
            for r, v in zip(drep_script_reg_certs, votes_drep_scripts)
            if v
        ]

    # Make sure we have enough time to submit the votes in one epoch
    clusterlib_utils.wait_for_epoch_interval(
        cluster_obj=cluster_obj, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
    )

    submit_vote(
        cluster_obj=cluster_obj,
        name_template=name_template,
        payment_addr=payment_addr,
        votes=votes_simple,
        keys=keys_all,
        script_votes=script_votes,
        use_build_cmd=use_build_cmd,
        witness_count_add=witness_count_add,
    )

    # Make sure the vote is included in the ledger
    gov_state = cluster_obj.g_governance.query.gov_state()
    vote_epoch = cluster_obj.g_query.get_epoch()
    save_gov_state(
        gov_state=gov_state,
        name_template=f"{name_template}_vote_{vote_epoch}",
    )
    prop_vote = governance_utils.lookup_proposal(gov_state=gov_state, action_txid=action_txid)
    assert not votes_cc or prop_vote["committeeVotes"], "No committee votes"
    assert not votes_drep or prop_vote["dRepVotes"], "No DRep votes"
    assert not votes_spo or prop_vote["stakePoolVotes"], "No stake pool votes"

    return governance_utils.VotedVotes(cc=votes_cc, drep=votes_drep, spo=votes_spo)


def resign_ccs(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    ccs_to_resign: list[clusterlib.CCMember],
    payment_addr: clusterlib.AddressRecord,
) -> clusterlib.TxRawOutput:
    """Resign multiple CC Members."""
    res_metadata_file = pl.Path(f"{name_template}_res_metadata.json")
    res_metadata_content = {"name": "Resigned CC member"}
    helpers.write_json(out_file=res_metadata_file, content=res_metadata_content)
    res_metadata_hash = cluster_obj.g_governance.get_anchor_data_hash(file_text=res_metadata_file)
    res_metadata_url = web.publish(file_path=res_metadata_file)
    res_certs = [
        cluster_obj.g_governance.committee.gen_cold_key_resignation_cert(
            key_name=f"{name_template}_{i}",
            cold_vkey_file=r.cold_vkey_file,
            resignation_metadata_url=res_metadata_url,
            resignation_metadata_hash=res_metadata_hash,
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
    res_committee_state = cluster_obj.g_governance.query.committee_state()
    save_committee_state(committee_state=res_committee_state, name_template=f"{name_template}_res")
    for cc_member in ccs_to_resign:
        member_key = f"keyHash-{cc_member.cold_vkey_hash}"
        member_rec = res_committee_state["committee"].get(member_key)
        assert not member_rec or member_rec["hotCredsAuthStatus"]["tag"] == "MemberResigned", (
            "CC Member not resigned"
        )

    return tx_output


def propose_change_constitution(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    anchor_url: str,
    anchor_data_hash: str,
    constitution_url: str,
    constitution_hash: str,
    pool_user: clusterlib.PoolUser,
    constitution_script_hash: str = "",
) -> tuple[clusterlib.ActionConstitution, str, int]:
    """Propose a constitution change."""
    deposit_amt = cluster_obj.g_query.get_gov_action_deposit()

    prev_action_rec = governance_utils.get_prev_action(
        action_type=governance_utils.PrevGovActionIds.CONSTITUTION,
        gov_state=cluster_obj.g_governance.query.gov_state(),
    )

    constitution_action = cluster_obj.g_governance.action.create_constitution(
        action_name=name_template,
        deposit_amt=deposit_amt,
        anchor_url=anchor_url,
        anchor_data_hash=anchor_data_hash,
        constitution_url=constitution_url,
        constitution_hash=constitution_hash,
        constitution_script_hash=constitution_script_hash,
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
        fee_buffer=2_000_000,
    )

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    assert (
        clusterlib.filter_utxos(utxos=out_utxos, address=pool_user.payment.address)[0].amount
        == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
    ), f"Incorrect balance for source address `{pool_user.payment.address}`"

    action_txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_output.out_file)
    action_gov_state = cluster_obj.g_governance.query.gov_state()
    action_epoch = cluster_obj.g_query.get_epoch()
    save_gov_state(
        gov_state=action_gov_state,
        name_template=f"{name_template}_constitution_action_{action_epoch}",
    )
    prop_action = governance_utils.lookup_proposal(
        gov_state=action_gov_state, action_txid=action_txid
    )
    assert prop_action, "Create constitution action not found"
    assert (
        prop_action["proposalProcedure"]["govAction"]["tag"]
        == governance_utils.ActionTags.NEW_CONSTITUTION.value
    ), "Incorrect action tag"

    action_ix = prop_action["actionId"]["govActionIx"]

    return constitution_action, action_txid, action_ix


def propose_pparams_update(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    anchor_url: str,
    anchor_data_hash: str,
    pool_user: clusterlib.PoolUser,
    proposals: list[clusterlib_utils.UpdateProposal],
    prev_action_rec: governance_utils.PrevActionRec | None = None,
) -> PParamPropRec:
    """Propose a pparams update."""
    deposit_amt = cluster_obj.g_query.get_gov_action_deposit()

    prev_action_rec = prev_action_rec or governance_utils.get_prev_action(
        action_type=governance_utils.PrevGovActionIds.PPARAM_UPDATE,
        gov_state=cluster_obj.g_governance.query.gov_state(),
    )

    update_args = clusterlib_utils.get_pparams_update_args(update_proposals=proposals)
    pparams_action = cluster_obj.g_governance.action.create_pparams_update(
        action_name=name_template,
        deposit_amt=deposit_amt,
        anchor_url=anchor_url,
        anchor_data_hash=anchor_data_hash,
        cli_args=update_args,
        prev_action_txid=prev_action_rec.txid,
        prev_action_ix=prev_action_rec.ix,
        deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
    )

    tx_files_action = clusterlib.TxFiles(
        proposal_files=[pparams_action.action_file],
        signing_key_files=[pool_user.payment.skey_file],
    )

    # Make sure we have enough time to submit the proposal in one epoch
    clusterlib_utils.wait_for_epoch_interval(
        cluster_obj=cluster_obj, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
    )

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
    action_gov_state = cluster_obj.g_governance.query.gov_state()
    action_epoch = cluster_obj.g_query.get_epoch()
    save_gov_state(
        gov_state=action_gov_state, name_template=f"{name_template}_action_{action_epoch}"
    )
    prop_action = governance_utils.lookup_proposal(
        gov_state=action_gov_state, action_txid=action_txid
    )
    assert prop_action, "Param update action not found"
    assert (
        prop_action["proposalProcedure"]["govAction"]["tag"]
        == governance_utils.ActionTags.PARAMETER_CHANGE.value
    ), "Incorrect action tag"

    action_ix = prop_action["actionId"]["govActionIx"]
    proposal_names = {p.name for p in proposals}

    return PParamPropRec(
        proposals=proposals,
        action_txid=action_txid,
        action_ix=action_ix,
        proposal_names=proposal_names,
        future_pparams=prop_action["proposalProcedure"]["govAction"]["contents"][1],
    )
