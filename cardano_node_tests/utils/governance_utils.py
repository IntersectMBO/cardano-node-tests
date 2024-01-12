"""Utilities for Conway governance."""
import dataclasses
import enum
import logging
import pathlib as pl
import typing as tp

from cardano_clusterlib import clusterlib

LOGGER = logging.getLogger(__name__)

ActionsAllT = tp.Union[  # pylint: disable=invalid-name
    clusterlib.ActionConstitution,
    clusterlib.ActionInfo,
    clusterlib.ActionNoConfidence,
    clusterlib.ActionPParamsUpdate,
    clusterlib.ActionUpdateCommittee,
    clusterlib.ActionTreasuryWithdrawal,
]


@dataclasses.dataclass(frozen=True, order=True)
class DRepRegistration:
    registration_cert: pl.Path
    key_pair: clusterlib.KeyPair
    drep_id: str
    deposit: int


@dataclasses.dataclass(frozen=True, order=True)
class CCMemberRegistration:
    registration_cert: pl.Path
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


class PrevGovActionIds(enum.Enum):
    COMMITTEE = "pgaCommittee"
    CONSTITUTION = "pgaConstitution"
    HARDFORK = "pgaHardFork"
    PPARAM_UPDATE = "pgaPParamUpdate"


class ActionTags(enum.Enum):
    NEW_CONSTITUTION = "NewConstitution"
    UPDATE_COMMITTEE = "UpdateCommittee"
    PARAMETER_CHANGE = "ParameterChange"
    TREASURY_WITHDRAWALS = "TreasuryWithdrawals"
    INFO_ACTION = "InfoAction"


def get_drep_cred_name(drep_id: str) -> str:
    cred_name = f"keyHash-{drep_id}"
    if drep_id == "always_abstain":
        cred_name = "alwaysAbstain"
    elif drep_id == "always_no_confidence":
        cred_name = "alwaysNoConfidence"

    return cred_name


def check_drep_delegation(deleg_state: dict, drep_id: str, stake_addr_hash: str) -> None:
    drep_records = deleg_state["dstate"]["unified"]["credentials"]

    stake_addr_key = f"keyHash-{stake_addr_hash}"
    stake_addr_val = drep_records.get(stake_addr_key) or {}

    cred_name = get_drep_cred_name(drep_id=drep_id)
    expected_drep = f"drep-{cred_name}"

    assert stake_addr_val.get("drep") == expected_drep


def get_prev_action(
    cluster_obj: clusterlib.ClusterLib, action_type: PrevGovActionIds
) -> PrevActionRec:
    prev_action_rec = (
        cluster_obj.g_conway_governance.query.gov_state()["nextRatifyState"]["nextEnactState"][
            "prevGovActionIds"
        ][action_type.value]
        or {}
    )
    txid = prev_action_rec.get("txId") or ""
    _ix = prev_action_rec.get("govActionIx", None)
    ix = -1 if _ix is None else _ix
    return PrevActionRec(txid=txid, ix=ix)


def lookup_proposal(
    gov_state: tp.Dict[str, tp.Any], action_txid: str, action_ix: int = 0
) -> tp.Dict[str, tp.Any]:
    proposals: tp.List[tp.Dict[str, tp.Any]] = gov_state["proposals"]
    prop: tp.Dict[str, tp.Any] = {}
    for _p in proposals:
        _p_action_id = _p["actionId"]
        if _p_action_id["txId"] == action_txid and _p_action_id["govActionIx"] == action_ix:
            prop = _p
            break
    return prop


def lookup_removed_actions(
    gov_state: tp.Dict[str, tp.Any], action_txid: str, action_ix: int = 0
) -> tp.Dict[str, tp.Any]:
    removed_actions: tp.List[tp.Dict[str, tp.Any]] = gov_state["nextRatifyState"][
        "removedGovActions"
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


def get_cc_member_reg_record(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    destination_dir: clusterlib.FileType = ".",
) -> CCMemberRegistration:
    """Get Constitutional Committee Members registration record."""
    committee_cold_keys = cluster_obj.g_conway_governance.committee.gen_cold_key_pair(
        key_name=name_template,
        destination_dir=destination_dir,
    )
    committee_hot_keys = cluster_obj.g_conway_governance.committee.gen_hot_key_pair(
        key_name=name_template,
        destination_dir=destination_dir,
    )
    reg_cert = cluster_obj.g_conway_governance.committee.gen_hot_key_auth_cert(
        key_name=name_template,
        cold_vkey_file=committee_cold_keys.vkey_file,
        hot_key_file=committee_hot_keys.vkey_file,
        destination_dir=destination_dir,
    )
    key_hash = cluster_obj.g_conway_governance.committee.get_key_hash(
        vkey_file=committee_cold_keys.vkey_file,
    )

    return CCMemberRegistration(
        registration_cert=reg_cert,
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
            raise ValueError("No return stake key was specified")

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
                raise ValueError("No funds receiving stake key was specified")

        gov_action = {
            "contents": [
                [
                    {
                        "credential": {"keyHash": recv_addr_vkey_hash},
                        "network": "Testnet",
                    },
                    action_data.transfer_amt,
                ]
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
        added_members = []
        for _m in action_data.add_cc_members:
            if _m.cold_vkey_file:
                cvkey_hash = cluster_obj.g_conway_governance.committee.get_key_hash(
                    vkey_file=_m.cold_vkey_file
                )
            elif _m.cold_vkey:
                cvkey_hash = cluster_obj.g_conway_governance.committee.get_key_hash(
                    vkey=_m.cold_vkey
                )
            elif _m.cold_vkey_hash:
                cvkey_hash = _m.cold_vkey_hash
            else:
                raise ValueError("No cold key was specified")
            added_members.append({f"keyHash-{cvkey_hash}": _m.epoch})

        gov_action = {
            "contents": [
                {"govActionIx": prev_action_ix, "txId": prev_action_txid}
                if prev_action_txid
                else None,
                [],  # TODO: removed members?
                *added_members,
                float(action_data.quorum),
            ],
            "tag": ActionTags.UPDATE_COMMITTEE.value,
        }
    else:
        raise NotImplementedError(f"Not implemented for action `{action_data}`")

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
        raise AssertionError("Ratification is still delayed")
