"""Utilities for Conway governance."""
import dataclasses
import enum
import logging
import pathlib as pl
import typing as tp

from cardano_clusterlib import clusterlib

LOGGER = logging.getLogger(__name__)


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


def check_drep_delegation(deleg_state: dict, drep_id: str, stake_addr_hash: str) -> None:
    drep_records = deleg_state["dstate"]["unified"]["credentials"]

    stake_addr_key = f"keyHash-{stake_addr_hash}"
    stake_addr_val = drep_records.get(stake_addr_key) or {}

    expected_drep = f"drep-keyHash-{drep_id}"
    if drep_id == "always_abstain":
        expected_drep = "drep-alwaysAbstain"
    elif drep_id == "always_no_confidence":
        expected_drep = "drep-alwaysNoConfidence"

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


def check_action_view(
    cluster_obj: clusterlib.ClusterLib,
    action_tag: ActionTags,
    action_file: pl.Path,
    anchor_url: str,
    anchor_data_hash: str,
    deposit_amt: int,
    return_addr_vkey_hash: str,
    recv_addr_vkey_hash: str = "",
    transfer_amt: int = -1,
) -> None:
    if action_tag == ActionTags.TREASURY_WITHDRAWALS:
        if transfer_amt == -1:
            raise ValueError("`transfer_amt` must be specified for treasury withdrawals")
        if not recv_addr_vkey_hash:
            raise ValueError("`recv_addr_vkey_hash` must be specified for treasury withdrawals")

        gov_action = {
            "contents": [
                [
                    {
                        "credential": {"keyHash": recv_addr_vkey_hash},
                        "network": "Testnet",
                    },
                    transfer_amt,
                ]
            ],
            "tag": action_tag.value,
        }
    elif action_tag == ActionTags.INFO_ACTION:
        gov_action = {
            "tag": action_tag.value,
        }
    else:
        raise NotImplementedError(f"Not implemented for action tag `{action_tag}`")

    action_view_out = cluster_obj.g_conway_governance.action.view(action_file=action_file)

    expected_action_out = {
        "anchor": {
            "dataHash": anchor_data_hash,
            "url": anchor_url,
        },
        "deposit": deposit_amt,
        "governance action": gov_action,
        "return address": {
            "credential": {"keyHash": return_addr_vkey_hash},
            "network": "Testnet",
        },
    }

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
