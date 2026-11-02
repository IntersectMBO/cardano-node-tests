"""Tests for the user-facing Leios committee query, without compatibility workarounds."""

import json
import re

import allure
import pytest

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

pytestmark = [
    pytest.mark.leios,
    pytest.mark.skipif(
        VERSIONS.cluster_era < VERSIONS.DIJKSTRA_FIRST,
        reason="Leios committees are available only in Dijkstra+ eras",
    ),
]


class TestLeiosQueries:
    """Tests for identifying Leios committee members directly from CLI output."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("committee_name", ("currentLeiosCommittee", "futureLeiosCommittee"))
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_leios_committee_member_details(
        self,
        cluster_manager: cluster_management.ClusterManager,
        committee_name: str,
    ):
        """Check that every queried seat identifies its pool and exposes key/voting state.

        Query the current or future committee without enriching its seats from ledger
        state. Report ledger issue 6245 when member details are missing. Skip an empty
        or unavailable committee, since it cannot establish support for seat fields.
        No transaction load generator or voting activity is needed.
        """
        cluster = cluster_manager.get(use_resources=cluster_management.Resources.ALL_POOLS)
        common.get_test_id(cluster)

        query_help = cluster.cli(["query", "--help"]).stdout.decode("utf-8")
        if not re.search(r"(?m)^\s+leios-committee\s", query_help):
            pytest.skip("The CLI does not support query leios-committee")

        # Inspect the raw result, so that the helper's workaround cannot hide the bug.
        result = json.loads(cluster.g_query.query_cli(["leios-committee"]))
        committee = result[committee_name]
        if committee is None and committee_name == "futureLeiosCommittee":
            pytest.skip(f"{committee_name} is not available yet")
        assert isinstance(committee, list) and all(isinstance(s, dict) for s in committee), (
            f"Unexpected Leios committee format: {committee}"
        )
        if not committee:
            pytest.skip(f"{committee_name} has no seats to check")

        if any(
            not seat.get("poolId")
            or "key" not in seat
            or (seat["key"] is not None and not isinstance(seat["key"], dict))
            or not isinstance(seat.get("voting"), bool)
            for seat in committee
        ):
            issues.ledger_6245.finish_test()
