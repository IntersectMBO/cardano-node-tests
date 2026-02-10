"""Tests for ledger state."""

import functools
import itertools
import logging
import pickle
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


LEDGER_STATE_KEYS = frozenset(
    {
        "blocksBefore",
        "blocksCurrent",
        "lastEpoch",
        "possibleRewardUpdate",
        "stakeDistrib",
        "stateBefore",
    }
)


class TestLedgerState:
    """Basic tests for ledger state."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(-1)
    @common.SKIPIF_WRONG_ERA
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_stake_snapshot(self, cluster: clusterlib.ClusterLib):  # noqa: C901
        """Test the `stake-snapshot` and `ledger-state` commands and ledger state values."""
        temp_template = common.get_test_id(cluster)

        # Make sure the queries can be finished in single epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        stake_pool_ids = cluster.g_query.get_stake_pools()
        if not stake_pool_ids:
            pytest.skip("No stake pools are available.")
        if len(stake_pool_ids) > 200:
            pytest.skip("Skipping on this testnet, there's too many pools.")

        try:
            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
        except RuntimeError as err:
            if "Invalid numeric literal at line" not in str(err):
                raise
            issues.node_3859.finish_test()
            raise

        clusterlib_utils.save_ledger_state(
            cluster_obj=cluster,
            state_name=temp_template,
            ledger_state=ledger_state,
        )
        es_snapshot: dict = ledger_state["stateBefore"]["esSnapshots"]

        def _get_hashes(snapshot: str) -> dict[str, int]:
            hashes: dict = clusterlib_utils.get_snapshot_rec(
                ledger_snapshot=es_snapshot[snapshot]["stake"]
            )
            return hashes

        def _get_delegations(snapshot: str) -> dict[str, list[str]]:
            delegations: dict = clusterlib_utils.get_snapshot_delegations(
                ledger_snapshot=es_snapshot[snapshot]["delegations"]
            )
            return delegations

        errors: list[str] = []
        ledger_state_data: dict[str, tp.Any] = {}

        ledger_state_keys = set(ledger_state)
        ledger_state_data["ledger_state_keys"] = ledger_state_keys
        if ledger_state_keys != LEDGER_STATE_KEYS:
            errors.append(
                "unexpected ledger state keys: "
                f"{ledger_state_keys.difference(LEDGER_STATE_KEYS)} and "
                f"{LEDGER_STATE_KEYS.difference(ledger_state_keys)}"
            )

        # Stake addresses (hashes) and corresponding amounts
        stake_mark = _get_hashes("pstakeMark")
        stake_set = _get_hashes("pstakeSet")
        stake_go = _get_hashes("pstakeGo")
        ledger_state_data.update(
            {
                "stake_mark": stake_mark,
                "stake_set": stake_set,
                "stake_go": stake_go,
            }
        )

        # Pools (hashes) and stake addresses (hashes) delegated to corresponding pool
        delegations_mark = _get_delegations("pstakeMark")
        delegations_set = _get_delegations("pstakeSet")
        delegations_go = _get_delegations("pstakeGo")
        ledger_state_data.update(
            {
                "delegations_mark": delegations_mark,
                "delegations_set": delegations_set,
                "delegations_go": delegations_go,
            }
        )

        # All delegated stake addresses (hashes)
        delegated_hashes_mark = set(itertools.chain.from_iterable(delegations_mark.values()))
        delegated_hashes_set = set(itertools.chain.from_iterable(delegations_set.values()))
        delegated_hashes_go = set(itertools.chain.from_iterable(delegations_go.values()))
        ledger_state_data.update(
            {
                "delegated_hashes_mark": delegated_hashes_mark,
                "delegated_hashes_set": delegated_hashes_set,
                "delegated_hashes_go": delegated_hashes_go,
            }
        )

        sum_mark = sum_set = sum_go = 0
        seen_hashes_mark: set[str] = set()
        seen_hashes_set: set[str] = set()
        seen_hashes_go: set[str] = set()
        delegation_pool_ids = {*delegations_mark, *delegations_set, *delegations_go}
        ledger_state_data["delegation_pool_ids"] = delegation_pool_ids
        pool_details: dict[str, tp.Any] = {}
        stake_snapshot = {}
        for pool_id_dec in delegation_pool_ids:
            pool_id = helpers.encode_bech32(prefix="pool", data=pool_id_dec)
            pool_entry: dict[str, tp.Any] = {"pool_id_bech32": pool_id}

            # Get stake info from ledger state
            pstake_hashes_mark = delegations_mark.get(pool_id_dec) or ()
            seen_hashes_mark.update(pstake_hashes_mark)
            pstake_amounts_mark = [stake_mark.get(h, 0) for h in pstake_hashes_mark]
            pstake_sum_mark = functools.reduce(lambda x, y: x + y, pstake_amounts_mark, 0)
            pool_entry["ledger_mark"] = {
                "hashes": pstake_hashes_mark,
                "amounts": pstake_amounts_mark,
                "sum": pstake_sum_mark,
            }

            pstake_hashes_set = delegations_set.get(pool_id_dec) or ()
            seen_hashes_set.update(pstake_hashes_set)
            pstake_amounts_set = [stake_set.get(h, 0) for h in pstake_hashes_set]
            pstake_sum_set = functools.reduce(lambda x, y: x + y, pstake_amounts_set, 0)
            pool_entry["ledger_set"] = {
                "hashes": pstake_hashes_set,
                "amounts": pstake_amounts_set,
                "sum": pstake_sum_set,
            }

            pstake_hashes_go = delegations_go.get(pool_id_dec) or ()
            seen_hashes_go.update(pstake_hashes_go)
            pstake_amounts_go = [stake_go.get(h, 0) for h in pstake_hashes_go]
            pstake_sum_go = functools.reduce(lambda x, y: x + y, pstake_amounts_go, 0)
            pool_entry["ledger_go"] = {
                "hashes": pstake_hashes_go,
                "amounts": pstake_amounts_go,
                "sum": pstake_sum_go,
            }

            # Get stake info from `stake-snapshot` command
            stake_snapshot = cluster.g_query.get_stake_snapshot(stake_pool_ids=[pool_id])
            if "pools" in stake_snapshot:
                pstake_mark_cmd = stake_snapshot["pools"][pool_id_dec]["stakeMark"]
                pstake_set_cmd = stake_snapshot["pools"][pool_id_dec]["stakeSet"]
                pstake_go_cmd = stake_snapshot["pools"][pool_id_dec]["stakeGo"]
            else:
                pstake_mark_cmd = stake_snapshot["poolStakeMark"]
                pstake_set_cmd = stake_snapshot["poolStakeSet"]
                pstake_go_cmd = stake_snapshot["poolStakeGo"]
            pool_entry["snapshot"] = {
                "stake_mark": pstake_mark_cmd,
                "stake_set": pstake_set_cmd,
                "stake_go": pstake_go_cmd,
            }

            if pstake_sum_mark != pstake_mark_cmd:
                errors.append(f"pool: {pool_id}, mark:\n  {pstake_sum_mark} != {pstake_mark_cmd}")
            if pstake_sum_set != pstake_set_cmd:
                errors.append(f"pool: {pool_id}, set:\n  {pstake_sum_set} != {pstake_set_cmd}")
            if pstake_sum_go != pstake_go_cmd:
                errors.append(f"pool: {pool_id}, go:\n  {pstake_sum_go} != {pstake_go_cmd}")

            sum_mark += pstake_mark_cmd
            sum_set += pstake_set_cmd
            sum_go += pstake_go_cmd
            pool_details[pool_id_dec] = pool_entry

        ledger_state_data.update(
            {
                "seen_hashes_mark": seen_hashes_mark,
                "seen_hashes_set": seen_hashes_set,
                "seen_hashes_go": seen_hashes_go,
                "sum_mark": sum_mark,
                "sum_set": sum_set,
                "sum_go": sum_go,
                "stake_snapshot": stake_snapshot,
                "pool_details": pool_details,
            }
        )

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
            ledger_state_data["snapshot_totals"] = {
                "stake_mark": stake_snapshot["total"]["stakeMark"],
                "stake_set": stake_snapshot["total"]["stakeSet"],
                "stake_go": stake_snapshot["total"]["stakeGo"],
            }
            if sum_mark != stake_snapshot["total"]["stakeMark"]:
                errors.append(f"total_mark: {sum_mark} != {stake_snapshot['total']['stakeMark']}")
            if sum_set != stake_snapshot["total"]["stakeSet"]:
                errors.append(f"total_set: {sum_set} != {stake_snapshot['total']['stakeSet']}")
            if sum_go != stake_snapshot["total"]["stakeGo"]:
                errors.append(f"total_go: {sum_go} != {stake_snapshot['total']['stakeGo']}")
        # Active stake can be lower than sum of stakes, as some pools may not be running
        # and forging blocks
        else:
            ledger_state_data["snapshot_totals"] = {
                "active_mark": stake_snapshot["activeStakeMark"],
                "active_set": stake_snapshot["activeStakeSet"],
                "active_go": stake_snapshot["activeStakeGo"],
            }
            if sum_mark < stake_snapshot["activeStakeMark"]:
                errors.append(f"active_mark: {sum_mark} < {stake_snapshot['activeStakeMark']}")
            if sum_set < stake_snapshot["activeStakeSet"]:
                errors.append(f"active_set: {sum_set} < {stake_snapshot['activeStakeSet']}")
            if sum_go < stake_snapshot["activeStakeGo"]:
                errors.append(f"active_go: {sum_go} < {stake_snapshot['activeStakeGo']}")

        if errors:
            with open(f"{temp_template}.pickle", "wb") as out_data:
                pickle.dump(ledger_state_data, out_data)
            err_joined = "\n".join(errors)
            pytest.fail(f"Errors:\n{err_joined}")
