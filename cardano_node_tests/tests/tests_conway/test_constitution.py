"""Tests for Conway governance constitution."""

# pylint: disable=expression-not-assigned
import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import blockers
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import requirements
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


@pytest.fixture
def pool_user_lg(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_governance: governance_setup.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "lock governance"."""
    cluster, __ = cluster_lock_governance
    key = helpers.get_current_line_str()
    return conway_common.get_pool_user(
        cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key
    )


class TestConstitution:
    """Tests for constitution."""

    @allure.link(helpers.get_vcs_link())
    def test_change_constitution(
        self,
        cluster_lock_governance: governance_setup.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test enactment of change of constitution.

        * submit a "create constitution" action
        * check that SPOs cannot vote on a "create constitution" action
        * vote to disapprove the action
        * vote to approve the action
        * check that the action is ratified
        * try to disapprove the ratified action, this shouldn't have any effect
        * check that the action is enacted
        * check that it's not possible to vote on enacted action
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        # Linked user stories
        req_cli1 = requirements.Req(id="CLI001", group=requirements.GroupsKnown.CHANG_US)
        req_cli2 = requirements.Req(id="CLI002", group=requirements.GroupsKnown.CHANG_US)
        req_cli13 = requirements.Req(id="CLI013", group=requirements.GroupsKnown.CHANG_US)
        req_cli20 = requirements.Req(id="CLI020", group=requirements.GroupsKnown.CHANG_US)
        req_cli36 = requirements.Req(id="CLI036", group=requirements.GroupsKnown.CHANG_US)
        req_cip1a = requirements.Req(id="CIP001a", group=requirements.GroupsKnown.CHANG_US)
        req_cip1b = requirements.Req(id="CIP001b", group=requirements.GroupsKnown.CHANG_US)
        req_cip31a = requirements.Req(id="intCIP031a-02", group=requirements.GroupsKnown.CHANG_US)
        req_cip42 = requirements.Req(id="CIP042", group=requirements.GroupsKnown.CHANG_US)
        req_cip73_1 = requirements.Req(id="intCIP073-01", group=requirements.GroupsKnown.CHANG_US)
        req_cip73_4 = requirements.Req(id="intCIP073-04", group=requirements.GroupsKnown.CHANG_US)

        # Create an action

        anchor_url = "http://www.const-action.com"
        anchor_data_hash = cluster.g_conway_governance.get_anchor_data_hash(text=anchor_url)

        constitution_file = f"{temp_template}_constitution.txt"
        constitution_text = (
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, "
            "sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
            "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi "
            "ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit "
            "in voluptate velit esse cillum dolore eu fugiat nulla pariatur. "
            "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia "
            "deserunt mollit anim id est laborum."
        )
        with open(constitution_file, "w", encoding="utf-8") as out_fp:
            out_fp.write(constitution_text)

        constitution_url = "http://www.const-new.com"
        req_cli2.start(url=helpers.get_vcs_link())
        constitution_hash = cluster.g_conway_governance.get_anchor_data_hash(
            file_text=constitution_file
        )
        req_cli2.success()

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (req_cli13, req_cip31a)]
        (
            constitution_action,
            action_txid,
            action_ix,
        ) = conway_common.propose_change_constitution(
            cluster_obj=cluster,
            name_template=f"{temp_template}_constitution",
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            constitution_url=constitution_url,
            constitution_hash=constitution_hash,
            pool_user=pool_user_lg,
        )
        [r.success() for r in (req_cli13, req_cip31a)]

        # Check that SPOs cannot vote on change of constitution action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_with_spos",
                payment_addr=pool_user_lg.payment,
                action_txid=action_txid,
                action_ix=action_ix,
                approve_cc=False,
                approve_drep=False,
                approve_spo=False,
            )
        err_str = str(excinfo.value)
        assert "StakePoolVoter" in err_str, err_str

        # Vote & disapprove the action
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_no",
            payment_addr=pool_user_lg.payment,
            action_txid=action_txid,
            action_ix=action_ix,
            approve_cc=False,
            approve_drep=False,
        )

        # Vote & approve the action
        req_cip42.start(url=helpers.get_vcs_link())
        voted_votes = conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_yes",
            payment_addr=pool_user_lg.payment,
            action_txid=action_txid,
            action_ix=action_ix,
            approve_cc=True,
            approve_drep=True,
        )

        def _assert_anchor(anchor: dict):
            assert (
                anchor["dataHash"]
                == constitution_hash
                == "d6d9034f61e2f7ada6e58c252e15684c8df7f0b197a95d80f42ca0a3685de26e"
            ), "Incorrect constitution data hash"
            assert anchor["url"] == constitution_url, "Incorrect constitution data URL"

        def _check_state(state: dict):
            anchor = state["constitution"]["anchor"]
            _assert_anchor(anchor)

        def _check_cli_query():
            anchor = cluster.g_conway_governance.query.constitution()["anchor"]
            _assert_anchor(anchor)

        # Check ratification
        xfail_ledger_3979_msgs = set()
        for __ in range(3):
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            rat_gov_state = cluster.g_conway_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}"
            )
            rat_action = governance_utils.lookup_ratified_actions(
                gov_state=rat_gov_state, action_txid=action_txid
            )
            if rat_action:
                break

            # Known ledger issue where only one expired action gets removed in one epoch.
            # See https://github.com/IntersectMBO/cardano-ledger/issues/3979
            if not rat_action and conway_common.possible_rem_issue(
                gov_state=rat_gov_state, epoch=_cur_epoch
            ):
                xfail_ledger_3979_msgs.add("Only single expired action got removed")
                continue

            msg = "Action not found in removed actions"
            raise AssertionError(msg)

        # Disapprove ratified action, the voting shouldn't have any effect
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_after_ratification",
            payment_addr=pool_user_lg.payment,
            action_txid=action_txid,
            action_ix=action_ix,
            approve_cc=False,
            approve_drep=False,
        )

        next_rat_state = rat_gov_state["nextRatifyState"]
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (req_cli1, req_cip1a, req_cip1b, req_cip73_1, req_cip73_4)]
        _check_state(next_rat_state["nextEnactState"])
        [r.success() for r in (req_cli1, req_cip1a, req_cip1b, req_cip73_1)]
        assert next_rat_state["ratificationDelayed"], "Ratification not delayed"

        # Check enactment
        _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
        )
        _check_state(enact_gov_state["enactState"])
        [r.success() for r in (req_cip42, req_cip73_4)]

        req_cli36.start(url=helpers.get_vcs_link())
        _check_cli_query()
        req_cli36.success()

        # Try to vote on enacted action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_enacted",
                payment_addr=pool_user_lg.payment,
                action_txid=action_txid,
                action_ix=action_ix,
                approve_cc=False,
                approve_drep=False,
            )
        err_str = str(excinfo.value)
        assert "(GovActionsDoNotExist" in err_str, err_str

        # Check action view
        req_cli20.start(url=helpers.get_vcs_link())
        governance_utils.check_action_view(cluster_obj=cluster, action_data=constitution_action)
        req_cli20.success()

        # Check vote view
        if voted_votes.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.drep[0])

        if xfail_ledger_3979_msgs:
            blockers.GH(
                issue=3979,
                repo="IntersectMBO/cardano-ledger",
                message="; ".join(xfail_ledger_3979_msgs),
                check_on_devel=False,
            ).finish_test()
