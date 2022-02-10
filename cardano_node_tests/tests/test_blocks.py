"""Tests for blocks production.

Other block production checks may be present in `test_staking.py`.
"""
import logging
from typing import Dict
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def cluster_use_pool3(cluster_manager: cluster_management.ClusterManager) -> clusterlib.ClusterLib:
    return cluster_manager.get(use_resources=[cluster_management.Resources.POOL3])


class TestLeadershipSchedule:
    """Tests for cardano-cli leadership-schedule."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.needs_dbsync
    def test_pool_blocks(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_use_pool3: clusterlib.ClusterLib,
    ):
        """Check that blocks were minted according to leadership schedule.

        * get leadership schedule for selected pool at the beginning of an epoch
        * wait for next epoch
        * get info about minted blocks in previous epoch for the selected pool
        * compare leadership schedule with blocks that were actually minted
        * compare db-sync records with ledger state dump
        """
        cluster = cluster_use_pool3
        temp_template = common.get_test_id(cluster)

        if not clusterlib_utils.cli_has("query leadership-schedule"):
            pytest.skip("The `cardano-cli query leadership-schedule` command is not available.")

        pool_name = "node-pool3"
        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_id = cluster.get_stake_pool_id(pool_rec["cold_key_pair"].vkey_file)

        # wait for beginning of an epoch
        epoch = cluster.wait_for_new_epoch(padding_seconds=5)

        # get leadership schedule for selected pool
        leadership_schedule = cluster.get_leadership_schedule(
            vrf_skey_file=pool_rec["vrf_key_pair"].skey_file,
            cold_vkey_file=pool_rec["cold_key_pair"].vkey_file,
        )

        # wait for next epoch
        cluster.wait_for_new_epoch()

        # get info about minted blocks in previous epoch for the selected pool
        minted_blocks = list(
            dbsync_queries.query_blocks(pool_id_bech32=pool_id, epoch_from=epoch, epoch_to=epoch)
        )
        slots_when_minted = {r.slot_no for r in minted_blocks}

        errors: List[str] = []

        # compare leadership schedule with blocks that were actually minted
        slots_when_scheduled = {r.slot_no for r in leadership_schedule}

        difference_scheduled = slots_when_minted.difference(slots_when_scheduled)
        if difference_scheduled:
            errors.append(
                f"Some blocks were minted in other slots than scheduled: {difference_scheduled}"
            )

        difference_minted = slots_when_scheduled.difference(slots_when_minted)
        if len(difference_minted) > len(leadership_schedule) // 2:
            errors.append(f"Lot of slots missed: {difference_minted}")

        # compare db-sync records with ledger state dump
        ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
        clusterlib_utils.save_ledger_state(
            cluster_obj=cluster,
            state_name=temp_template,
            ledger_state=ledger_state,
        )
        blocks_before: Dict[str, int] = ledger_state["blocksBefore"]
        pool_id_dec = helpers.decode_bech32(pool_id)
        minted_blocks_ledger = blocks_before.get(pool_id_dec) or 0
        minted_blocks_db = len(slots_when_minted)
        if minted_blocks_ledger != minted_blocks_db:
            errors.append(
                "Numbers of minted blocks reported by ledger state and db-sync don't match: "
                f"{minted_blocks_ledger} vs {minted_blocks_db}"
            )

        if errors:
            err_joined = "\n".join(errors)
            pytest.fail(f"Errors:\n{err_joined}")
