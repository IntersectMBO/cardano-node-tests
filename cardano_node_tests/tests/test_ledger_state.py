"""Tests for ledger state."""
import functools
import itertools
import logging
from typing import Dict
from typing import List
from typing import Set

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


LEDGER_STATE_KEYS = {
    "blocksBefore",
    "blocksCurrent",
    "lastEpoch",
    "possibleRewardUpdate",
    "stakeDistrib",
    "stateBefore",
}


class TestLedgerState:
    """Basic tests for ledger state."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(-1)
    @common.SKIPIF_WRONG_ERA
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_stake_snapshot(self, cluster: clusterlib.ClusterLib):  # noqa: C901
        """Test the `stake-snapshot` and `ledger-state` commands and ledger state values."""
        # pylint: disable=too-many-statements,too-many-locals,too-many-branches
        temp_template = common.get_test_id(cluster)

        # make sure the queries can be finished in single epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        stake_pool_ids = cluster.g_query.get_stake_pools()
        if not stake_pool_ids:
            pytest.skip("No stake pools are available.")
        if len(stake_pool_ids) > 200:
            pytest.skip("Skipping on this testnet, there's too many pools.")

        # TODO: xfail for node issue #3859
        try:
            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
        except AssertionError as err:
            if "Invalid numeric literal at line" not in str(err):
                raise
            pytest.xfail("expected JSON, got CBOR - see node issue #3859")

        clusterlib_utils.save_ledger_state(
            cluster_obj=cluster,
            state_name=temp_template,
            ledger_state=ledger_state,
        )
        es_snapshot: dict = ledger_state["stateBefore"]["esSnapshots"]

        def _get_hashes(snapshot: str) -> Dict[str, int]:
            hashes: Dict[str, int] = {}
            for r in es_snapshot[snapshot]["stake"]:
                r_hash_rec = r[0]
                r_hash = r_hash_rec.get("script hash") or r_hash_rec.get("key hash")
                if r_hash in hashes:
                    hashes[r_hash] += r[1]
                else:
                    hashes[r_hash] = r[1]
            return hashes

        def _get_delegations(snapshot: str) -> Dict[str, List[str]]:
            delegations: Dict[str, List[str]] = {}
            for r in es_snapshot[snapshot]["delegations"]:
                r_hash_rec = r[0]
                r_hash = r_hash_rec.get("script hash") or r_hash_rec.get("key hash")
                r_pool_id = r[1]
                if r_pool_id in delegations:
                    delegations[r_pool_id].append(r_hash)
                else:
                    delegations[r_pool_id] = [r_hash]
            return delegations

        errors = []

        ledger_state_keys = set(ledger_state)
        if ledger_state_keys != LEDGER_STATE_KEYS:
            errors.append(
                "unexpected ledger state keys: "
                f"{ledger_state_keys.difference(LEDGER_STATE_KEYS)} and "
                f"{LEDGER_STATE_KEYS.difference(ledger_state_keys)}"
            )

        # stake addresses (hashes) and corresponding amounts
        stake_mark = _get_hashes("pstakeMark")
        stake_set = _get_hashes("pstakeSet")
        stake_go = _get_hashes("pstakeGo")

        # pools (hashes) and stake addresses (hashes) delegated to corresponding pool
        delegations_mark = _get_delegations("pstakeMark")
        delegations_set = _get_delegations("pstakeSet")
        delegations_go = _get_delegations("pstakeGo")

        # all delegated stake addresses (hashes)
        delegated_hashes_mark = set(itertools.chain.from_iterable(delegations_mark.values()))
        delegated_hashes_set = set(itertools.chain.from_iterable(delegations_set.values()))
        delegated_hashes_go = set(itertools.chain.from_iterable(delegations_go.values()))

        # check if all delegated addresses are listed among stake addresses
        stake_hashes_mark = set(stake_mark)
        if not delegated_hashes_mark.issubset(stake_hashes_mark):
            errors.append(
                "for 'mark', some delegations are not listed in 'stake': "
                f"{delegated_hashes_mark.difference(stake_hashes_mark)}"
            )

        stake_hashes_set = set(stake_set)
        if not delegated_hashes_set.issubset(stake_hashes_set):
            errors.append(
                "for 'set', some delegations are not listed in 'stake': "
                f"{delegated_hashes_set.difference(stake_hashes_set)}"
            )

        stake_hashes_go = set(stake_go)
        if not delegated_hashes_go.issubset(stake_hashes_go):
            errors.append(
                "for 'go', some delegations are not listed in 'stake': "
                f"{delegated_hashes_go.difference(stake_hashes_go)}"
            )

        sum_mark = sum_set = sum_go = 0
        seen_hashes_mark: Set[str] = set()
        seen_hashes_set: Set[str] = set()
        seen_hashes_go: Set[str] = set()
        delegation_pool_ids = {*delegations_mark, *delegations_set, *delegations_go}
        for pool_id_dec in delegation_pool_ids:
            pool_id = helpers.encode_bech32(prefix="pool", data=pool_id_dec)

            # get stake info from ledger state
            pstake_hashes_mark = delegations_mark.get(pool_id_dec) or ()
            seen_hashes_mark.update(pstake_hashes_mark)
            pstake_amounts_mark = [stake_mark[h] for h in pstake_hashes_mark]
            pstake_sum_mark = functools.reduce(lambda x, y: x + y, pstake_amounts_mark, 0)

            pstake_hashes_set = delegations_set.get(pool_id_dec) or ()
            seen_hashes_set.update(pstake_hashes_set)
            pstake_amounts_set = [stake_set[h] for h in pstake_hashes_set]
            pstake_sum_set = functools.reduce(lambda x, y: x + y, pstake_amounts_set, 0)

            pstake_hashes_go = delegations_go.get(pool_id_dec) or ()
            seen_hashes_go.update(pstake_hashes_go)
            pstake_amounts_go = [stake_go[h] for h in pstake_hashes_go]
            pstake_sum_go = functools.reduce(lambda x, y: x + y, pstake_amounts_go, 0)

            # get stake info from `stake-snapshot` command
            stake_snapshot = cluster.g_query.get_stake_snapshot(stake_pool_ids=[pool_id])
            if "pools" in stake_snapshot:
                pstake_mark_cmd = stake_snapshot["pools"][pool_id_dec]["stakeMark"]
                pstake_set_cmd = stake_snapshot["pools"][pool_id_dec]["stakeSet"]
                pstake_go_cmd = stake_snapshot["pools"][pool_id_dec]["stakeGo"]
            else:
                pstake_mark_cmd = stake_snapshot["poolStakeMark"]
                pstake_set_cmd = stake_snapshot["poolStakeSet"]
                pstake_go_cmd = stake_snapshot["poolStakeGo"]

            if pstake_sum_mark != pstake_mark_cmd:
                errors.append(f"pool: {pool_id}, mark:\n  {pstake_sum_mark} != {pstake_mark_cmd}")
            if pstake_sum_set != pstake_set_cmd:
                errors.append(f"pool: {pool_id}, set:\n  {pstake_sum_set} != {pstake_set_cmd}")
            if pstake_sum_go != pstake_go_cmd:
                errors.append(f"pool: {pool_id}, go:\n  {pstake_sum_go} != {pstake_go_cmd}")

            sum_mark += pstake_mark_cmd
            sum_set += pstake_set_cmd
            sum_go += pstake_go_cmd

        if seen_hashes_mark != delegated_hashes_mark:
            errors.append(
                "seen hashes and existing hashes differ for 'mark': "
                f"{seen_hashes_mark.difference(delegated_hashes_mark)} and "
                f"{delegated_hashes_mark.difference(seen_hashes_mark)}"
            )

        if seen_hashes_set != delegated_hashes_set:
            errors.append(
                "seen hashes and existing hashes differ for 'set': "
                f"{seen_hashes_set.difference(delegated_hashes_set)} and "
                f"{delegated_hashes_set.difference(seen_hashes_set)}"
            )

        if seen_hashes_go != delegated_hashes_go:
            errors.append(
                "seen hashes and existing hashes differ for 'go': "
                f"{seen_hashes_go.difference(delegated_hashes_go)} and "
                f"{delegated_hashes_go.difference(seen_hashes_go)}"
            )

        if "pools" in stake_snapshot:
            if sum_mark != stake_snapshot["total"]["stakeMark"]:
                errors.append(f"total_mark: {sum_mark} != {stake_snapshot['total']['stakeMark']}")
            if sum_set != stake_snapshot["total"]["stakeSet"]:
                errors.append(f"total_set: {sum_set} != {stake_snapshot['total']['stakeSet']}")
            if sum_go != stake_snapshot["total"]["stakeGo"]:
                errors.append(f"total_go: {sum_go} != {stake_snapshot['total']['stakeGo']}")
        # active stake can be lower than sum of stakes, as some pools may not be running
        # and minting blocks
        else:
            if sum_mark < stake_snapshot["activeStakeMark"]:
                errors.append(f"active_mark: {sum_mark} < {stake_snapshot['activeStakeMark']}")
            if sum_set < stake_snapshot["activeStakeSet"]:
                errors.append(f"active_set: {sum_set} < {stake_snapshot['activeStakeSet']}")
            if sum_go < stake_snapshot["activeStakeGo"]:
                errors.append(f"active_go: {sum_go} < {stake_snapshot['activeStakeGo']}")

        if errors:
            err_joined = "\n".join(errors)
            pytest.fail(f"Errors:\n{err_joined}")
