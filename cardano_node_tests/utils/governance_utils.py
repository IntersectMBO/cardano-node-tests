"""Utilities for Conway governance."""

import dataclasses
import enum
import functools
import logging
import pathlib as pl
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

ActionsAllT = tp.Union[  # pylint: disable=invalid-name
    clusterlib.ActionConstitution,
    clusterlib.ActionHardfork,
    clusterlib.ActionInfo,
    clusterlib.ActionNoConfidence,
    clusterlib.ActionPParamsUpdate,
    clusterlib.ActionTreasuryWithdrawal,
    clusterlib.ActionUpdateCommittee,
]

VotesAllT = tp.Union[  # pylint: disable=invalid-name
    clusterlib.VoteCC,
    clusterlib.VoteDrep,
    clusterlib.VoteSPO,
]

DRepStateT = tp.List[tp.List[tp.Dict[str, tp.Any]]]


@dataclasses.dataclass(frozen=True, order=True)
class DRepRegistration:
    registration_cert: pl.Path
    key_pair: clusterlib.KeyPair
    drep_id: str
    deposit: int


@dataclasses.dataclass(frozen=True, order=True)
class CCMemberAuth:
    auth_cert: pl.Path
    cold_key_pair: clusterlib.KeyPair
    hot_key_pair: clusterlib.KeyPair
    key_hash: str


@dataclasses.dataclass(frozen=True, order=True)
class PrevActionRec:
    txid: str
    # pylint: disable-next=invalid-name
    ix: int

    def __bool__(self) -> bool:
        return bool(self.txid)


@dataclasses.dataclass(frozen=True, order=True)
class StakeDelegation:
    spo: int
    drep: int
    total_lovelace: int


class PrevGovActionIds(enum.Enum):
    COMMITTEE = "Committee"
    CONSTITUTION = "Constitution"
    HARDFORK = "HardFork"
    PPARAM_UPDATE = "PParamUpdate"


class ActionTags(enum.Enum):
    NEW_CONSTITUTION = "NewConstitution"
    UPDATE_COMMITTEE = "UpdateCommittee"
    PARAMETER_CHANGE = "ParameterChange"
    TREASURY_WITHDRAWALS = "TreasuryWithdrawals"
    INFO_ACTION = "InfoAction"
    NO_CONFIDENCE = "NoConfidence"
    HARDFORK_INIT = "HardForkInitiation"


def get_drep_cred_name(drep_id: str) -> str:
    cred_name = f"keyHash-{drep_id}"
    if drep_id == "always_abstain":
        cred_name = "alwaysAbstain"
    elif drep_id == "always_no_confidence":
        cred_name = "alwaysNoConfidence"

    return cred_name


def get_vote_str(vote: clusterlib.Votes) -> str:
    if vote == vote.YES:
        return "VoteYes"
    if vote == vote.NO:
        return "VoteNo"
    if vote == vote.ABSTAIN:
        return "Abstain"
    msg = f"Invalid vote `{vote}`"
    raise ValueError(msg)


def check_drep_delegation(deleg_state: dict, drep_id: str, stake_addr_hash: str) -> None:
    drep_records = deleg_state["dstate"]["unified"]["credentials"]

    stake_addr_key = f"keyHash-{stake_addr_hash}"
    stake_addr_val = drep_records.get(stake_addr_key) or {}

    cred_name = get_drep_cred_name(drep_id=drep_id)
    expected_drep = f"drep-{cred_name}"

    assert stake_addr_val.get("drep") == expected_drep


def check_drep_stake_distribution(
    distrib_state: tp.List[list], drep_id: str, min_amount: int
) -> None:
    cred_name = get_drep_cred_name(drep_id=drep_id)
    expected_drep = f"drep-{cred_name}"

    found_rec = []
    for r in distrib_state:
        if r[0] == expected_drep:
            found_rec = r
            break
    else:
        msg = f"Record for `{expected_drep}` not found"
        raise AssertionError(msg)

    assert found_rec[1] >= min_amount, f"The stake amount delegated to DRep < {min_amount}"


def get_prev_action(
    action_type: PrevGovActionIds,
    gov_state: tp.Dict[str, tp.Any],
) -> PrevActionRec:
    prev_action_rec = (
        gov_state["nextRatifyState"]["nextEnactState"]["prevGovActionIds"][action_type.value] or {}
    )
    txid = prev_action_rec.get("txId") or ""
    _ix = prev_action_rec.get("govActionIx", None)
    ix = -1 if _ix is None else _ix
    return PrevActionRec(txid=txid, ix=ix)


def _lookup_action(
    actions: tp.List[tp.Dict[str, tp.Any]], action_txid: str, action_ix: int = 0
) -> tp.Dict[str, tp.Any]:
    prop: tp.Dict[str, tp.Any] = {}
    for _a in actions:
        _p_action_id = _a["actionId"]
        if _p_action_id["txId"] == action_txid and _p_action_id["govActionIx"] == action_ix:
            prop = _a
            break
    return prop


def lookup_proposal(
    gov_state: tp.Dict[str, tp.Any], action_txid: str, action_ix: int = 0
) -> tp.Dict[str, tp.Any]:
    proposals: tp.List[tp.Dict[str, tp.Any]] = gov_state["proposals"]
    return _lookup_action(actions=proposals, action_txid=action_txid, action_ix=action_ix)


def lookup_ratified_actions(
    gov_state: tp.Dict[str, tp.Any], action_txid: str, action_ix: int = 0
) -> tp.Dict[str, tp.Any]:
    ratified_actions: tp.List[tp.Dict[str, tp.Any]] = gov_state["nextRatifyState"][
        "enactedGovActions"
    ]
    return _lookup_action(actions=ratified_actions, action_txid=action_txid, action_ix=action_ix)


def lookup_expired_actions(
    gov_state: tp.Dict[str, tp.Any],
    action_txid: str,
    action_ix: int = 0,
) -> tp.Dict[str, tp.Any]:
    removed_actions: tp.List[tp.Dict[str, tp.Any]] = gov_state["nextRatifyState"][
        "expiredGovActions"
    ]
    raction: tp.Dict[str, tp.Any] = {}
    for _r in removed_actions:
        if _r["txId"] == action_txid and _r["govActionIx"] == action_ix:
            raction = _r
            break
    return raction


def get_drep_reg_record(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    deposit_amt: int = -1,
    drep_metadata_url: str = "",
    drep_metadata_hash: str = "",
    destination_dir: clusterlib.FileType = ".",
) -> DRepRegistration:
    """Get DRep registration record."""
    deposit_amt = deposit_amt if deposit_amt != -1 else cluster_obj.conway_genesis["dRepDeposit"]
    drep_keys = cluster_obj.g_conway_governance.drep.gen_key_pair(
        key_name=name_template, destination_dir=destination_dir
    )
    reg_cert = cluster_obj.g_conway_governance.drep.gen_registration_cert(
        cert_name=name_template,
        deposit_amt=deposit_amt,
        drep_vkey_file=drep_keys.vkey_file,
        drep_metadata_url=drep_metadata_url,
        drep_metadata_hash=drep_metadata_hash,
        destination_dir=destination_dir,
    )
    drep_id = cluster_obj.g_conway_governance.drep.get_id(
        drep_vkey_file=drep_keys.vkey_file,
        out_format="hex",
    )

    return DRepRegistration(
        registration_cert=reg_cert,
        key_pair=drep_keys,
        drep_id=drep_id,
        deposit=deposit_amt,
    )


def get_cc_member_auth_record(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    destination_dir: clusterlib.FileType = ".",
) -> CCMemberAuth:
    """Get Constitutional Committee Members key authorization record."""
    committee_cold_keys = cluster_obj.g_conway_governance.committee.gen_cold_key_pair(
        key_name=name_template,
        destination_dir=destination_dir,
    )
    committee_hot_keys = cluster_obj.g_conway_governance.committee.gen_hot_key_pair(
        key_name=name_template,
        destination_dir=destination_dir,
    )
    auth_cert = cluster_obj.g_conway_governance.committee.gen_hot_key_auth_cert(
        key_name=name_template,
        cold_vkey_file=committee_cold_keys.vkey_file,
        hot_key_file=committee_hot_keys.vkey_file,
        destination_dir=destination_dir,
    )
    key_hash = cluster_obj.g_conway_governance.committee.get_key_hash(
        vkey_file=committee_cold_keys.vkey_file,
    )

    return CCMemberAuth(
        auth_cert=auth_cert,
        cold_key_pair=committee_cold_keys,
        hot_key_pair=committee_hot_keys,
        key_hash=key_hash,
    )


def check_action_view(  # noqa: C901
    cluster_obj: clusterlib.ClusterLib,
    action_data: ActionsAllT,
    return_addr_vkey_hash: str = "",
    recv_addr_vkey_hash: str = "",
) -> None:
    """Check `governance action view` output."""
    # pylint: disable=too-many-branches
    if not return_addr_vkey_hash:
        if action_data.deposit_return_stake_vkey_file:
            return_addr_vkey_hash = cluster_obj.g_stake_address.get_stake_vkey_hash(
                stake_vkey_file=action_data.deposit_return_stake_vkey_file
            )
        elif action_data.deposit_return_stake_vkey:
            return_addr_vkey_hash = cluster_obj.g_stake_address.get_stake_vkey_hash(
                stake_vkey=action_data.deposit_return_stake_vkey
            )
        elif action_data.deposit_return_stake_key_hash:
            return_addr_vkey_hash = action_data.deposit_return_stake_key_hash
        else:
            msg = "No return stake key was specified"
            raise ValueError(msg)

    prev_action_txid = getattr(action_data, "prev_action_txid", None)
    prev_action_ix = getattr(action_data, "prev_action_ix", None)

    gov_action: tp.Dict[str, tp.Any]

    if isinstance(action_data, clusterlib.ActionTreasuryWithdrawal):
        if not recv_addr_vkey_hash:
            if action_data.funds_receiving_stake_vkey_file:
                recv_addr_vkey_hash = cluster_obj.g_stake_address.get_stake_vkey_hash(
                    stake_vkey_file=action_data.funds_receiving_stake_vkey_file
                )
            elif action_data.funds_receiving_stake_vkey:
                recv_addr_vkey_hash = cluster_obj.g_stake_address.get_stake_vkey_hash(
                    stake_vkey=action_data.funds_receiving_stake_vkey
                )
            elif action_data.funds_receiving_stake_key_hash:
                recv_addr_vkey_hash = action_data.funds_receiving_stake_key_hash
            else:
                msg = "No funds receiving stake key was specified"
                raise ValueError(msg)

        gov_action = {
            "contents": [
                [
                    [
                        {
                            "credential": {"keyHash": recv_addr_vkey_hash},
                            "network": "Testnet",
                        },
                        action_data.transfer_amt,
                    ],
                ],
                None,  # TODO 8.8: what is this?
            ],
            "tag": ActionTags.TREASURY_WITHDRAWALS.value,
        }
    elif isinstance(action_data, clusterlib.ActionInfo):
        gov_action = {
            "tag": ActionTags.INFO_ACTION.value,
        }
    elif isinstance(action_data, clusterlib.ActionConstitution):
        gov_action = {
            "contents": [
                {"govActionIx": prev_action_ix, "txId": prev_action_txid}
                if prev_action_txid
                else None,
                {
                    "anchor": {
                        "dataHash": action_data.constitution_hash,
                        "url": action_data.constitution_url,
                    }
                },
            ],
            "tag": ActionTags.NEW_CONSTITUTION.value,
        }
    elif isinstance(action_data, clusterlib.ActionUpdateCommittee):

        def _get_cvkey_hash(member: clusterlib.CCMember) -> str:
            if member.cold_vkey_file:
                cvkey_hash = cluster_obj.g_conway_governance.committee.get_key_hash(
                    vkey_file=member.cold_vkey_file
                )
            elif member.cold_vkey:
                cvkey_hash = cluster_obj.g_conway_governance.committee.get_key_hash(
                    vkey=member.cold_vkey
                )
            elif member.cold_vkey_hash:
                cvkey_hash = member.cold_vkey_hash
            else:
                msg = "No cold key was specified"
                raise ValueError(msg)

            return cvkey_hash

        added_members = {}
        for _m in action_data.add_cc_members:
            cvkey_hash = _get_cvkey_hash(member=_m)
            added_members[f"keyHash-{cvkey_hash}"] = _m.epoch

        removed_members = []
        for _m in action_data.rem_cc_members:
            removed_members.append({"keyHash": _get_cvkey_hash(member=_m)})

        gov_action = {
            "contents": [
                {"govActionIx": prev_action_ix, "txId": prev_action_txid}
                if prev_action_txid
                else None,
                removed_members if removed_members else [],
                added_members,
                float(action_data.threshold),
            ],
            "tag": ActionTags.UPDATE_COMMITTEE.value,
        }
    elif isinstance(action_data, clusterlib.ActionNoConfidence):
        gov_action = {
            "contents": {"govActionIx": prev_action_ix, "txId": prev_action_txid}
            if prev_action_txid
            else None,
            "tag": ActionTags.NO_CONFIDENCE.value,
        }
    elif isinstance(action_data, clusterlib.ActionHardfork):
        gov_action = {
            "contents": [
                None,  # TODO 8.11: what is this?
                {
                    "major": action_data.protocol_major_version,
                    "minor": action_data.protocol_minor_version,
                },
            ],
            "tag": ActionTags.HARDFORK_INIT.value,
        }
    else:
        msg = f"Not implemented for action `{action_data}`"
        raise NotImplementedError(msg)

    expected_action_out = {
        "anchor": {
            "dataHash": action_data.anchor_data_hash,
            "url": action_data.anchor_url,
        },
        "deposit": action_data.deposit_amt,
        "governance action": gov_action,
        "return address": {
            "credential": {"keyHash": return_addr_vkey_hash},
            "network": "Testnet",
        },
    }

    action_view_out = cluster_obj.g_conway_governance.action.view(
        action_file=action_data.action_file
    )

    assert action_view_out == expected_action_out, f"{action_view_out} != {expected_action_out}"


def check_vote_view(  # noqa: C901
    cluster_obj: clusterlib.ClusterLib,
    vote_data: VotesAllT,
) -> None:
    """Check `governance vote view` output."""
    # pylint: disable=too-many-branches
    vote_key = ""

    if isinstance(vote_data, clusterlib.VoteCC):
        if vote_data.cc_hot_vkey_file:
            cc_key_hash = cluster_obj.g_conway_governance.committee.get_key_hash(
                vkey_file=vote_data.cc_hot_vkey_file
            )
        elif vote_data.cc_hot_vkey:
            cc_key_hash = cluster_obj.g_conway_governance.committee.get_key_hash(
                vkey=vote_data.cc_hot_vkey
            )
        elif vote_data.cc_hot_key_hash:
            cc_key_hash = vote_data.cc_hot_key_hash
        else:
            msg = "No hot key was specified"
            raise ValueError(msg)

        vote_key = f"committee-keyHash-{cc_key_hash}"
    elif isinstance(vote_data, clusterlib.VoteDrep):
        if vote_data.drep_vkey_file:
            drep_id = cluster_obj.g_conway_governance.drep.get_id(
                drep_vkey_file=vote_data.drep_vkey_file, out_format="hex"
            )
        elif vote_data.drep_vkey:
            drep_id = cluster_obj.g_conway_governance.drep.get_id(
                drep_vkey=vote_data.drep_vkey, out_format="hex"
            )
        elif vote_data.drep_key_hash:
            drep_id = vote_data.drep_key_hash
        else:
            msg = "No drep key was specified"
            raise ValueError(msg)

        if drep_id.startswith("drep1"):
            drep_id = helpers.decode_bech32(bech32=drep_id)

        vote_key = f"drep-keyHash-{drep_id}"
    elif isinstance(vote_data, clusterlib.VoteSPO):
        if vote_data.cold_vkey_file:
            pool_id = cluster_obj.g_stake_pool.get_stake_pool_id(
                cold_vkey_file=vote_data.cold_vkey_file
            )
        elif vote_data.stake_pool_vkey:
            pool_id = cluster_obj.g_stake_pool.get_stake_pool_id(
                stake_pool_vkey=vote_data.stake_pool_vkey
            )
        elif vote_data.stake_pool_id:
            pool_id = vote_data.stake_pool_id
        else:
            msg = "No stake pool key was specified"
            raise ValueError(msg)

        if pool_id.startswith("pool1"):
            pool_id = helpers.decode_bech32(bech32=pool_id)

        vote_key = f"stakepool-keyHash-{pool_id}"
    else:
        msg = f"Not implemented for vote `{vote_data}`"
        raise NotImplementedError(msg)

    assert vote_key, "No vote key was specified"

    anchor = (
        {"dataHash": vote_data.anchor_data_hash, "url": vote_data.anchor_url}
        if vote_data.anchor_data_hash
        else None
    )
    expected_vote_out = {
        vote_key: {
            f"{vote_data.action_txid}#{vote_data.action_ix}": {
                "anchor": anchor,
                "decision": get_vote_str(vote=vote_data.vote),
            }
        }
    }

    action_view_out = cluster_obj.g_conway_governance.vote.view(vote_file=vote_data.vote_file)

    assert action_view_out == expected_vote_out, f"{action_view_out} != {expected_vote_out}"


def wait_delayed_ratification(
    cluster_obj: clusterlib.ClusterLib,
) -> None:
    """Wait until ratification is no longer delayed."""
    for __ in range(3):
        next_rat_state = cluster_obj.g_conway_governance.query.gov_state()["nextRatifyState"]
        if not next_rat_state["ratificationDelayed"]:
            break
        cluster_obj.wait_for_new_epoch(padding_seconds=5)
    else:
        msg = "Ratification is still delayed"
        raise AssertionError(msg)


def get_delegated_stake(cluster_obj: clusterlib.ClusterLib) -> StakeDelegation:
    """Get total stake delegated to SPOs and DReps."""
    total_lovelace = cluster_obj.genesis["maxLovelaceSupply"]

    stake_snapshot = cluster_obj.g_query.get_stake_snapshot(all_stake_pools=True)
    total_spo_stake = stake_snapshot["total"]["stakeGo"]

    drep_state = cluster_obj.g_conway_governance.query.drep_state()
    total_drep_stake = functools.reduce(lambda x, y: x + (y[1].get("stake") or 0), drep_state, 0)

    return StakeDelegation(
        spo=total_spo_stake, drep=total_drep_stake, total_lovelace=total_lovelace
    )


def is_drep_active(
    cluster_obj: clusterlib.ClusterLib,
    drep_state: DRepStateT,
    epoch: int = -1,
) -> bool:
    """Check if DRep is active."""
    if epoch == -1:
        epoch = cluster_obj.g_query.get_epoch()

    return bool(drep_state[0][1].get("expiry", 0) > epoch)
