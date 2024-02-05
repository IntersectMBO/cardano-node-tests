"""Common functionality for Conway governance tests."""
import dataclasses
import json
import logging
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils

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
