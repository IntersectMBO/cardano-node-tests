import enum
import itertools
import logging
import pathlib as pl
import pickle
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import locking

LOGGER = logging.getLogger(__name__)

GOV_DATA_STORE = "governance_data.pickle"

GovClusterT = tp.Tuple[clusterlib.ClusterLib, governance_setup.DefaultGovernance]


class PrevActionRec(tp.NamedTuple):
    txid: str
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
    cluster_obj: clusterlib.ClusterLib, action_txid: str, action_ix: int = 0
) -> tp.Dict[str, tp.Any]:
    proposals = cluster_obj.g_conway_governance.query.gov_state()["proposals"]
    prop: tp.Dict[str, tp.Any] = {}
    for _p in proposals:
        _p_action_id = _p["actionId"]
        if _p_action_id["txId"] == action_txid and _p_action_id["govActionIx"] == action_ix:
            prop = _p
            break
    return prop


def get_default_governance(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
) -> governance_setup.DefaultGovernance:
    with cluster_manager.cache_fixture(key="default_governance") as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        cluster_env = cluster_nodes.get_cluster_env()
        gov_data_dir = cluster_env.state_dir / governance_setup.GOV_DATA_DIR
        gov_data_store = gov_data_dir / GOV_DATA_STORE

        if gov_data_store.exists():
            with open(gov_data_store, "rb") as in_data:
                loaded_gov_data = pickle.load(in_data)
            fixture_cache.value = loaded_gov_data
            return loaded_gov_data  # type: ignore

        with locking.FileLockIfXdist(str(cluster_env.state_dir / f".{GOV_DATA_STORE}.lock")):
            gov_data_dir.mkdir(exist_ok=True, parents=True)

            governance_data = governance_setup.setup(
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
            check_drep_delegation(
                deleg_state=deleg_state, drep_id=drep_id, stake_addr_hash=stake_addr_hash
            )

            fixture_cache.value = governance_data

            with open(gov_data_store, "wb") as out_data:
                pickle.dump(governance_data, out_data)

    return governance_data


def get_pparams_update_args(
    update_proposals: tp.List[clusterlib_utils.UpdateProposal],
) -> tp.List[str]:
    """Get cli arguments for pparams update action."""
    if not update_proposals:
        return []

    _cli_args = [(u.arg, str(u.value)) for u in update_proposals]
    cli_args = list(itertools.chain.from_iterable(_cli_args))
    return cli_args


def check_action_view(
    cluster_obj: clusterlib.ClusterLib,
    action_tag: ActionTags,
    action_file: pl.Path,
    anchor_url: str,
    anchor_data_hash: str,
    deposit_amt: int,
    recv_addr_vkey_hash: str,
    return_addr_vkey_hash: str,
    transfer_amt: int = -1,
) -> None:
    contents = []
    if action_tag == ActionTags.TREASURY_WITHDRAWALS:
        if transfer_amt == -1:
            raise ValueError("`transfer_amt` must be specified for treasury withdrawals")

        contents = [
            [
                {
                    "credential": {"keyHash": recv_addr_vkey_hash},
                    "network": "Testnet",
                },
                transfer_amt,
            ]
        ]
    else:
        raise NotImplementedError(f"Not implemented for action tag `{action_tag}`")

    action_view_out = cluster_obj.g_conway_governance.action.view(action_file=action_file)

    expected_action_out = {
        "anchor": {
            "dataHash": anchor_data_hash,
            "url": anchor_url,
        },
        "deposit": deposit_amt,
        "governance action": {
            "contents": contents,
            "tag": action_tag.value,
        },
        "return address": {
            "credential": {"keyHash": return_addr_vkey_hash},
            "network": "Testnet",
        },
    }

    assert action_view_out == expected_action_out, f"{action_view_out} != {expected_action_out}"
