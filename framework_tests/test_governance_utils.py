"""Unit tests for governance action checks without a running cluster."""

import dataclasses
import pathlib as pl
import typing as tp
from unittest import mock

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import governance_utils

CommitteeActionViewT = tuple[mock.Mock, clusterlib.ActionUpdateCommittee, dict[str, tp.Any]]


@pytest.fixture
def committee_action_view() -> CommitteeActionViewT:
    """Return a committee action and its mocked CLI view, including non-threshold fields."""
    action = clusterlib.ActionUpdateCommittee(
        action_file=pl.Path("committee.action"),
        deposit_amt=100,
        anchor_url="https://example.com/anchor",
        anchor_data_hash="anchor-hash",
        threshold="2/3",
        add_cc_members=[clusterlib.CCMember(epoch=10, cold_vkey_hash="added")],
        rem_cc_members=[clusterlib.CCMember(epoch=0, cold_vkey_hash="removed")],
        prev_action_txid="previous-tx",
        prev_action_ix=2,
        deposit_return_stake_key_hash="return-key",
    )
    view = {
        "anchor": {"dataHash": "anchor-hash", "url": "https://example.com/anchor"},
        "deposit": 100,
        "governance action": {
            "tag": governance_utils.ActionTags.UPDATE_COMMITTEE.value,
            "contents": [
                {"govActionIx": 2, "txId": "previous-tx"},
                [{"keyHash": "removed"}],
                {"keyHash-added": 10},
                {"numerator": 2, "denominator": 3},
            ],
        },
        "return address": {"credential": {"keyHash": "return-key"}, "network": "Testnet"},
    }
    cluster = mock.Mock(spec=clusterlib.ClusterLib)
    cluster.g_governance.action.view.return_value = view
    return cluster, action, view


class TestCommitteeActionView:
    """Compare committee thresholds numerically without weakening other action checks."""

    @pytest.mark.parametrize(
        ("threshold", "reported"),
        [
            ("2/3", {"numerator": 2, "denominator": 3}),
            ("2/3", {"numerator": 4, "denominator": 6}),
            ("8337/16000", 0.5210625),
            ("0.5210625", {"numerator": 8337, "denominator": 16000}),
            ("0.8457565493", 0.8457565493),
            ("0", 0),
            ("1", 1),
        ],
    )
    def test_equivalent_thresholds(
        self,
        committee_action_view: CommitteeActionViewT,
        threshold: str,
        reported: int | float | dict[str, int],
    ):
        """Accept equal fraction strings, numeric thresholds, and rational objects."""
        cluster, action, view = committee_action_view
        action = dataclasses.replace(action, threshold=threshold)
        view["governance action"]["contents"][3] = reported

        governance_utils.check_action_view(cluster_obj=cluster, action_data=action)

        cluster.g_governance.action.view.assert_called_once_with(action_file=action.action_file)

    @pytest.mark.parametrize("reported", [0.5, {"numerator": 1, "denominator": 2}])
    def test_wrong_threshold(
        self,
        committee_action_view: CommitteeActionViewT,
        reported: float | dict[str, int],
    ):
        """Reject a genuinely different threshold in either JSON representation."""
        cluster, action, view = committee_action_view
        view["governance action"]["contents"][3] = reported

        with pytest.raises(AssertionError, match="!="):
            governance_utils.check_action_view(cluster_obj=cluster, action_data=action)

    def test_wrong_committee_member(
        self,
        committee_action_view: CommitteeActionViewT,
    ):
        """Keep checking committee members after normalizing the threshold."""
        cluster, action, view = committee_action_view
        view["governance action"]["contents"][2] = {"keyHash-unexpected": 10}

        with pytest.raises(AssertionError, match="!="):
            governance_utils.check_action_view(cluster_obj=cluster, action_data=action)

    def test_wrong_deposit(
        self,
        committee_action_view: CommitteeActionViewT,
    ):
        """Keep checking the action envelope after normalizing the threshold."""
        cluster, action, view = committee_action_view
        view["deposit"] = 200

        with pytest.raises(AssertionError, match="!="):
            governance_utils.check_action_view(cluster_obj=cluster, action_data=action)
