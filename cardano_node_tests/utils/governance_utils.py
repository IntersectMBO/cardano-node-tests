"""Utilities for Conway governance."""

import dataclasses
import enum
import functools
import itertools
import logging
import pathlib as pl
import typing as tp

from cardano_clusterlib import clusterlib

import cardano_node_tests.utils.types as ttypes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import web

LOGGER = logging.getLogger(__name__)

ActionsAllT = (
    clusterlib.ActionConstitution
    | clusterlib.ActionHardfork
    | clusterlib.ActionInfo
    | clusterlib.ActionNoConfidence
    | clusterlib.ActionPParamsUpdate
    | clusterlib.ActionTreasuryWithdrawal
    | clusterlib.ActionUpdateCommittee
)

VotesAllT = clusterlib.VoteCC | clusterlib.VoteDrep | clusterlib.VoteSPO

DRepStateT = list[list[dict[str, tp.Any]]]


class ScriptTypes(enum.Enum):
    SIMPLE = "simple"
    PLUTUS = "plutus"


@dataclasses.dataclass(frozen=True, order=True)
class DRepRegistration:
    registration_cert: pl.Path
    key_pair: clusterlib.KeyPair
    drep_id: str
    deposit: int


@dataclasses.dataclass(frozen=True, order=True)
class DRepScriptRegRecord:
    registration_cert: pl.Path
    script_hash: str
    deposit: int


@dataclasses.dataclass(frozen=True, order=True)
class DRepScriptRegInputs:
    registration_cert: clusterlib.ComplexCert
    key_pairs: list[clusterlib.KeyPair]
    script_type: ScriptTypes


@dataclasses.dataclass(frozen=True, order=True)
class DRepScriptRegistration:
    registration_cert: clusterlib.ComplexCert
    key_pairs: list[clusterlib.KeyPair]
    script_hash: str
    script_type: ScriptTypes
    deposit: int


@dataclasses.dataclass(frozen=True, order=True)
class CCHotKeys:
    hot_vkey: str = ""
    hot_vkey_file: ttypes.FileType = ""
    hot_vkey_hash: str = ""
    hot_skey: str = ""
    hot_skey_file: ttypes.FileType = ""
    hot_skey_hash: str = ""


@dataclasses.dataclass(frozen=True, order=True)
class CCKeyMember:
    cc_member: clusterlib.CCMember
    hot_keys: CCHotKeys


@dataclasses.dataclass(frozen=True, order=True)
class GovernanceRecords:
    dreps_reg: list[DRepRegistration]
    drep_delegators: list[clusterlib.PoolUser]
    cc_key_members: list[CCKeyMember]
    pools_cold: list[clusterlib.ColdKeyPair]
    drep_scripts_reg: list[DRepScriptRegistration] = dataclasses.field(default_factory=list)
    drep_scripts_delegators: list[clusterlib.PoolUser] = dataclasses.field(default_factory=list)


GovClusterT = tuple[clusterlib.ClusterLib, GovernanceRecords]


@dataclasses.dataclass(frozen=True, order=True)
class CCMemberAuth:
    auth_cert: pl.Path
    cold_key_pair: clusterlib.KeyPair
    hot_key_pair: clusterlib.KeyPair
    key_hash: str


@dataclasses.dataclass(frozen=True, order=True)
class PrevActionRec:
    txid: str
    ix: int

    def __bool__(self) -> bool:
        return bool(self.txid)


@dataclasses.dataclass(frozen=True, order=True)
class StakeDelegation:
    spo: int
    drep: int
    total_lovelace: int


@dataclasses.dataclass(frozen=True, order=True)
class VotedVotes:
    cc: list[clusterlib.VoteCC]
    drep: list[clusterlib.VoteDrep]
    spo: list[clusterlib.VoteSPO]


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


@dataclasses.dataclass(frozen=True)
class AnchorData:
    url: str
    hash: str
    data_file: pl.Path | None


def get_drep_cred_name(*, drep_id: str) -> str:
    if not drep_id:
        return ""
    cred_name = f"keyHash-{drep_id}"
    if drep_id == "always_abstain":
        cred_name = "alwaysAbstain"
    elif drep_id == "always_no_confidence":
        cred_name = "alwaysNoConfidence"

    return cred_name


def get_drep_cred_name_from_addr_info(*, addr_info: clusterlib.StakeAddrInfo) -> str:
    drep_id_raw = addr_info.vote_delegation_hex or addr_info.vote_delegation or ""
    if not drep_id_raw:
        return ""

    # Decode first if working with Bech32 encoded value.
    if drep_id_raw.startswith("drep1"):
        drep_id_sanitized = helpers.decode_bech32(bech32=drep_id_raw)
    else:
        # The prefix was already present for the hex value in the older version of cardano-cli.
        drep_id_sanitized = drep_id_raw.removeprefix("keyHash-")

    if len(drep_id_sanitized) > 56:
        drep_id_sanitized = drep_id_sanitized[-56:]

    # The DRep ID can be either hex string or special names like "alwaysAbstain".
    cred_name = (
        f"keyHash-{drep_id_sanitized}" if len(drep_id_sanitized) == 56 else drep_id_sanitized
    )
    return cred_name


def get_vote_str(*, vote: clusterlib.Votes) -> str:
    if vote == vote.YES:
        return "VoteYes"
    if vote == vote.NO:
        return "VoteNo"
    if vote == vote.ABSTAIN:
        return "Abstain"
    msg = f"Invalid vote `{vote}`"
    raise ValueError(msg)


def check_drep_delegation(*, deleg_state: dict, drep_id: str, stake_addr_hash: str) -> None:
    dstate = deleg_state["dstate"]
    drep_records = dstate.get("accounts") or {}  # In cardano-node >= 10.6.0
    if not drep_records:
        drep_records = dstate.get("unified", {}).get("credentials") or {}

    stake_addr_key = f"keyHash-{stake_addr_hash}"
    stake_addr_val = drep_records.get(stake_addr_key) or {}

    cred_name = get_drep_cred_name(drep_id=drep_id)
    expected_drep = f"drep-{cred_name}"

    assert stake_addr_val.get("drep") == expected_drep


def check_drep_stake_distribution(
    *, distrib_state: dict[str, tp.Any], drep_id: str, min_amount: int
) -> None:
    cred_name = get_drep_cred_name(drep_id=drep_id)
    expected_drep = f"drep-{cred_name}"
    deleg_amount = distrib_state.get(expected_drep)
    assert deleg_amount is not None, f"Record for `{expected_drep}` not found"
    assert deleg_amount >= min_amount, f"The stake amount delegated to DRep < {min_amount}"


def get_prev_action(
    *, action_type: PrevGovActionIds, gov_state: dict[str, tp.Any]
) -> PrevActionRec:
    prev_action_rec = (
        gov_state["nextRatifyState"]["nextEnactState"]["prevGovActionIds"][action_type.value] or {}
    )
    txid = prev_action_rec.get("txId") or ""
    _ix = prev_action_rec.get("govActionIx", None)
    ix = -1 if _ix is None else _ix
    return PrevActionRec(txid=txid, ix=ix)


def _lookup_action(
    *, actions: list[dict[str, tp.Any]], action_txid: str, action_ix: int = 0
) -> dict[str, tp.Any]:
    prop: dict[str, tp.Any] = {}
    for _a in actions:
        _p_action_id = _a["actionId"]
        if _p_action_id["txId"] == action_txid and _p_action_id["govActionIx"] == action_ix:
            prop = _a
            break
    return prop


def lookup_proposal(
    *, gov_state: dict[str, tp.Any], action_txid: str, action_ix: int = 0
) -> dict[str, tp.Any]:
    proposals: list[dict[str, tp.Any]] = gov_state["proposals"]
    return _lookup_action(actions=proposals, action_txid=action_txid, action_ix=action_ix)


def lookup_ratified_actions(
    *, gov_state: dict[str, tp.Any], action_txid: str, action_ix: int = 0
) -> dict[str, tp.Any]:
    ratified_actions: list[dict[str, tp.Any]] = gov_state["nextRatifyState"]["enactedGovActions"]
    return _lookup_action(actions=ratified_actions, action_txid=action_txid, action_ix=action_ix)


def lookup_expired_actions(
    *, gov_state: dict[str, tp.Any], action_txid: str, action_ix: int = 0
) -> dict[str, tp.Any]:
    removed_actions: list[dict[str, tp.Any]] = gov_state["nextRatifyState"]["expiredGovActions"]
    raction: dict[str, tp.Any] = {}
    for _r in removed_actions:
        if _r["txId"] == action_txid and _r["govActionIx"] == action_ix:
            raction = _r
            break
    return raction


def get_drep_reg_record(
    *,
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    deposit_amt: int = -1,
    drep_metadata_url: str = "",
    drep_metadata_hash: str = "",
    destination_dir: clusterlib.FileType = ".",
) -> DRepRegistration:
    """Get DRep registration record."""
    deposit_amt = deposit_amt if deposit_amt != -1 else cluster_obj.g_query.get_drep_deposit()
    drep_keys = cluster_obj.g_governance.drep.gen_key_pair(
        key_name=name_template, destination_dir=destination_dir
    )
    reg_cert = cluster_obj.g_governance.drep.gen_registration_cert(
        cert_name=name_template,
        deposit_amt=deposit_amt,
        drep_vkey_file=drep_keys.vkey_file,
        drep_metadata_url=drep_metadata_url,
        drep_metadata_hash=drep_metadata_hash,
        destination_dir=destination_dir,
    )
    drep_id = cluster_obj.g_governance.drep.get_id(
        drep_vkey_file=drep_keys.vkey_file,
        out_format="hex",
    )

    return DRepRegistration(
        registration_cert=reg_cert,
        key_pair=drep_keys,
        drep_id=drep_id,
        deposit=deposit_amt,
    )


def get_script_drep_reg_record(
    *,
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    script_hash: str,
    deposit_amt: int = -1,
    drep_metadata_url: str = "",
    drep_metadata_hash: str = "",
    destination_dir: clusterlib.FileType = ".",
) -> DRepScriptRegRecord:
    """Get DRep script registration record."""
    deposit_amt = deposit_amt if deposit_amt != -1 else cluster_obj.g_query.get_drep_deposit()
    reg_cert = cluster_obj.g_governance.drep.gen_registration_cert(
        cert_name=name_template,
        deposit_amt=deposit_amt,
        drep_script_hash=script_hash,
        drep_metadata_url=drep_metadata_url,
        drep_metadata_hash=drep_metadata_hash,
        destination_dir=destination_dir,
    )

    return DRepScriptRegRecord(
        registration_cert=reg_cert,
        script_hash=script_hash,
        deposit=deposit_amt,
    )


def get_cc_member_auth_record(
    *,
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    destination_dir: clusterlib.FileType = ".",
) -> CCMemberAuth:
    """Get Constitutional Committee Members key authorization record."""
    committee_cold_keys = cluster_obj.g_governance.committee.gen_cold_key_pair(
        key_name=name_template,
        destination_dir=destination_dir,
    )
    committee_hot_keys = cluster_obj.g_governance.committee.gen_hot_key_pair(
        key_name=name_template,
        destination_dir=destination_dir,
    )
    auth_cert = cluster_obj.g_governance.committee.gen_hot_key_auth_cert(
        key_name=name_template,
        cold_vkey_file=committee_cold_keys.vkey_file,
        hot_key_file=committee_hot_keys.vkey_file,
        destination_dir=destination_dir,
    )
    key_hash = cluster_obj.g_governance.committee.get_key_hash(
        vkey_file=committee_cold_keys.vkey_file,
    )

    return CCMemberAuth(
        auth_cert=auth_cert,
        cold_key_pair=committee_cold_keys,
        hot_key_pair=committee_hot_keys,
        key_hash=key_hash,
    )


def check_action_view(  # noqa: C901
    *,
    cluster_obj: clusterlib.ClusterLib,
    action_data: ActionsAllT,
    return_addr_vkey_hash: str = "",
    recv_addr_vkey_hash: str = "",
) -> None:
    """Check `governance action view` output."""
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

    gov_action: dict[str, tp.Any]

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

        def _get_cvkey_hash(*, member: clusterlib.CCMember) -> str:
            if member.cold_vkey_file:
                cvkey_hash = cluster_obj.g_governance.committee.get_key_hash(
                    vkey_file=member.cold_vkey_file
                )
            elif member.cold_vkey:
                cvkey_hash = cluster_obj.g_governance.committee.get_key_hash(vkey=member.cold_vkey)
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

        removed_members = [
            {"keyHash": _get_cvkey_hash(member=_m)} for _m in action_data.rem_cc_members
        ]

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

    action_view_out = cluster_obj.g_governance.action.view(action_file=action_data.action_file)

    assert action_view_out == expected_action_out, f"{action_view_out} != {expected_action_out}"


def check_vote_view(  # noqa: C901
    *,
    cluster_obj: clusterlib.ClusterLib,
    vote_data: VotesAllT,
) -> None:
    """Check `governance vote view` output."""
    vote_key = ""

    if isinstance(vote_data, clusterlib.VoteCC):
        if vote_data.cc_hot_vkey_file:
            cc_key_hash = cluster_obj.g_governance.committee.get_key_hash(
                vkey_file=vote_data.cc_hot_vkey_file
            )
        elif vote_data.cc_hot_vkey:
            cc_key_hash = cluster_obj.g_governance.committee.get_key_hash(
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
            drep_id = cluster_obj.g_governance.drep.get_id(
                drep_vkey_file=vote_data.drep_vkey_file, out_format="hex"
            )
        elif vote_data.drep_vkey:
            drep_id = cluster_obj.g_governance.drep.get_id(
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

    vote_view_out = cluster_obj.g_governance.vote.view(vote_file=vote_data.vote_file)

    assert vote_view_out == expected_vote_out, f"{vote_view_out} != {expected_vote_out}"


def wait_delayed_ratification(*, cluster_obj: clusterlib.ClusterLib) -> None:
    """Wait until ratification is no longer delayed."""
    for __ in range(3):
        next_rat_state = cluster_obj.g_query.get_gov_state()["nextRatifyState"]
        if not next_rat_state["ratificationDelayed"]:
            break
        cluster_obj.wait_for_new_epoch(padding_seconds=5)
    else:
        msg = "Ratification is still delayed"
        raise TimeoutError(msg)


def get_delegated_stake(*, cluster_obj: clusterlib.ClusterLib) -> StakeDelegation:
    """Get total stake delegated to SPOs and DReps."""
    total_lovelace = cluster_obj.genesis["maxLovelaceSupply"]

    stake_snapshot = cluster_obj.g_query.get_stake_snapshot(all_stake_pools=True)
    total_spo_stake = stake_snapshot["total"]["stakeGo"]

    drep_state = cluster_obj.g_query.get_drep_state()
    total_drep_stake = functools.reduce(lambda x, y: x + (y[1].get("stake") or 0), drep_state, 0)

    return StakeDelegation(
        spo=total_spo_stake, drep=total_drep_stake, total_lovelace=total_lovelace
    )


def is_drep_active(
    *, cluster_obj: clusterlib.ClusterLib, drep_state: DRepStateT, epoch: int = -1
) -> bool:
    """Check if DRep is active."""
    if epoch == -1:
        epoch = cluster_obj.g_query.get_epoch()

    return bool(drep_state[0][1].get("expiry", 0) > epoch)


def is_cc_active(*, cc_member_state: dict[str, tp.Any]) -> bool:
    """Check if CC member is active."""
    if not cc_member_state:
        return False
    if cc_member_state["hotCredsAuthStatus"] != "MemberAuthorized":
        return False
    if cc_member_state["status"] != "Active":  # noqa: SIM103
        return False

    return True


def create_dreps(
    *,
    name_template: str,
    num: int,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    pool_users: list[clusterlib.PoolUser],
    destination_dir: clusterlib.FileType = ".",
) -> tuple[list[DRepRegistration], list[clusterlib.PoolUser]]:
    """Create DReps with keys."""
    no_of_addrs = len(pool_users)

    if no_of_addrs < num:
        msg = "Not enough pool users to create drep registrations"
        raise ValueError(msg)

    stake_deposit = cluster_obj.g_query.get_address_deposit()
    drep_users = pool_users[:num]
    deposit_amt = cluster_obj.g_query.get_drep_deposit()

    # Create DRep registration certs
    drep_reg_records = [
        get_drep_reg_record(
            cluster_obj=cluster_obj,
            name_template=f"{name_template}_{i}",
            deposit_amt=deposit_amt,
            destination_dir=destination_dir,
        )
        for i in range(1, num + 1)
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
        build_method=clusterlib_utils.BuildMethods.BUILD,
        tx_files=tx_files,
        deposit=(drep_reg_records[0].deposit + stake_deposit) * len(drep_reg_records),
        destination_dir=destination_dir,
    )

    return drep_reg_records, drep_users


def create_script_dreps(
    *,
    name_template: str,
    script_inputs: list[DRepScriptRegInputs],
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    pool_users: list[clusterlib.PoolUser],
    destination_dir: clusterlib.FileType = ".",
) -> tuple[list[DRepScriptRegistration], list[clusterlib.PoolUser]]:
    """Create DReps with scripts."""
    no_of_addrs = len(pool_users)
    no_of_scripts = len(script_inputs)

    if no_of_addrs < no_of_scripts:
        msg = "Not enough pool users to create drep registrations"
        raise ValueError(msg)

    stake_deposit = cluster_obj.g_query.get_address_deposit()
    drep_users = pool_users[:no_of_scripts]
    deposit_amt = cluster_obj.g_query.get_drep_deposit()

    # Create DRep script registration certs
    drep_reg_records = [
        get_script_drep_reg_record(
            cluster_obj=cluster_obj,
            name_template=f"{name_template}_{i}",
            script_hash=cluster_obj.g_governance.get_script_hash(
                script_file=s.registration_cert.script_file
            ),
            deposit_amt=deposit_amt,
            destination_dir=destination_dir,
        )
        for i, s in enumerate(script_inputs, start=1)
    ]

    drep_script_data = [
        DRepScriptRegistration(
            registration_cert=dataclasses.replace(
                s.registration_cert, certificate_file=r.registration_cert
            ),
            key_pairs=s.key_pairs,
            script_hash=r.script_hash,
            script_type=s.script_type,
            deposit=r.deposit,
        )
        for s, r in zip(script_inputs, drep_reg_records)
    ]

    # Create stake address registration certs
    stake_reg_certs = [
        clusterlib.ComplexCert(
            certificate_file=cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
                addr_name=f"{name_template}_addr{i}",
                deposit_amt=stake_deposit,
                stake_vkey_file=du.stake.vkey_file,
                destination_dir=destination_dir,
            )
        )
        for i, du in enumerate(drep_users, start=1)
    ]

    # Create vote delegation cert
    stake_deleg_certs = [
        clusterlib.ComplexCert(
            certificate_file=cluster_obj.g_stake_address.gen_vote_delegation_cert(
                addr_name=f"{name_template}_addr{i + 1}",
                stake_vkey_file=du.stake.vkey_file,
                drep_script_hash=drep_reg_records[i].script_hash,
                destination_dir=destination_dir,
            )
        )
        for i, du in enumerate(drep_users)
    ]

    # Make sure we have enough time to finish the registration/delegation in one epoch
    clusterlib_utils.wait_for_epoch_interval(cluster_obj=cluster_obj, start=1, stop=-15)

    witness_keys = itertools.chain.from_iterable(r.key_pairs for r in script_inputs)
    tx_files = clusterlib.TxFiles(
        signing_key_files=[
            payment_addr.skey_file,
            *[r.stake.skey_file for r in drep_users],
            *[r.skey_file for r in witness_keys],
        ],
    )

    clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_reg",
        src_address=payment_addr.address,
        build_method=clusterlib_utils.BuildMethods.BUILD,
        tx_files=tx_files,
        complex_certs=[
            *[c.registration_cert for c in drep_script_data],
            *stake_reg_certs,
            *stake_deleg_certs,
        ],
        deposit=(drep_reg_records[0].deposit + stake_deposit) * len(drep_reg_records),
        destination_dir=destination_dir,
    )

    return drep_script_data, drep_users


def get_anchor_data(
    *, cluster_obj: clusterlib.ClusterLib, name_template: str, anchor_text: str
) -> AnchorData:
    """Publish anchor data and return the URL and data hash."""
    anchor_file = pl.Path(f"{name_template}_anchor.txt")
    anchor_file.write_text(anchor_text, encoding="utf-8")
    url = web.publish(file_path=anchor_file)
    data_hash = cluster_obj.g_governance.get_anchor_data_hash(file_text=anchor_file)
    return AnchorData(url=url, hash=data_hash, data_file=anchor_file)


def get_default_anchor_data() -> AnchorData:
    """Return the default anchor data."""
    data = "Default Anchor"
    data_hash = "2d5975261fae751c4129475badee4ce9e9c84c537c925b728aa02417b2254a3f"

    try:
        url = web.create_file(name="default_anchor.txt", data=data)
    except FileExistsError:
        url = web.get_published_url(name="default_anchor.txt")
    return AnchorData(url=url, hash=data_hash, data_file=None)
