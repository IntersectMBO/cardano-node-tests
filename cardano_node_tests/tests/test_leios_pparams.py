"""Tests for governance of the Leios protocol parameters.

All the Leios protocol parameters are in the network group and in the security group
(`PPGroups NetworkGroup SecurityGroup` in the Dijkstra `PParams.hs`), so a change needs
the votes of the DReps, the SPOs and the CC.

The changes don't all take effect at the same epoch boundary:

* At an epoch boundary, the Dijkstra EPOCH rule first enacts the new pparams and only
  then runs SNAP. The mark snapshot that SNAP takes therefore records the
  `leiosCommitteeSize` of the just enacted pparams. The committee is seated from that
  snapshot only when it rotates into the "set" position at the next boundary. A committee
  size enacted at the boundary of epoch E therefore applies to the committee of epoch E+1.
* Consensus reads `leiosQuorumStakeThreshold` from the current pparams, so a quorum
  enacted at the boundary of epoch E applies already in epoch E.
"""

import fractions
import logging
import time

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import leios
from cardano_node_tests.tests import markers
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.leios,
    markers.SKIPIF_ON_TESTNET,
    pytest.mark.skipif(
        VERSIONS.cluster_era < VERSIONS.DIJKSTRA_FIRST,
        reason="Leios protocol parameters are available only in Dijkstra+ eras",
    ),
    pytest.mark.skipif(bool(leios.SKIP_REASON), reason=leios.SKIP_REASON),
]

# How much of an epoch a log search needs: the window itself plus the margin that keeps
# its end away from the epoch boundary.
EPOCH_TAIL_SEC = leios.MAX_SEARCH_SEC + leios.EPOCH_MARGIN_SEC

# The fraction of the smallest pool stake share the lowered quorum is set to, so that
# whichever pool ends up on the single seat committee reaches the quorum alone, even
# when the stake distribution shifts a bit by the time the committee is seated.
QUORUM_STAKE_SHARE_RATIO = 0.8


@pytest.fixture
def cluster_lock_leios_gov(
    cluster_manager: cluster_management.ClusterManager,
) -> governance_utils.GovClusterT:
    """Lock the whole cluster instance for changing the Leios pparams.

    The changed pparams shrink the voting committee for every test on the instance and
    they are not restored, so the instance is respun afterwards - everything that can rule
    the test out is checked before the instance is marked.
    """
    cluster_obj = cluster_manager.get(lock_resources=[cluster_management.Resources.CLUSTER])
    leios.skip_if_no_ebs(cluster_obj=cluster_obj)
    # Up to 1 epoch for delayed ratification, 3 epochs for ratification, enactment and the
    # committee rotation, and the log search in the last epoch
    common.skip_on_long_epochs(cluster_obj=cluster_obj, epochs=5)

    if cluster_obj.epoch_length_sec <= EPOCH_TAIL_SEC + leios.EPOCH_MARGIN_SEC:
        pytest.skip(
            f"An epoch takes only {cluster_obj.epoch_length_sec:.0f} sec on the "
            f"'{configuration.TESTNET_VARIANT}' testnet variant, which is not enough for "
            f"the {EPOCH_TAIL_SEC} sec search window"
        )

    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster_obj
    )
    governance_utils.wait_delayed_ratification(cluster_obj=cluster_obj)

    cluster_manager.set_needs_respin()
    return cluster_obj, governance_data


@pytest.fixture
def pool_user_llg(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_leios_gov: governance_utils.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a registered pool user for "lock Leios governance"."""
    cluster, __ = cluster_lock_leios_gov
    return addrs_common.get_registered_pool_user(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        caching_key=helpers.get_current_line_str(),
        amount=2_000_000_000,
    )


@pytest.fixture
def pool_user_ug(
    cluster_manager: cluster_management.ClusterManager,
    cluster_use_governance: governance_utils.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a registered pool user for "use governance"."""
    cluster, __ = cluster_use_governance
    return addrs_common.get_registered_pool_user(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        caching_key=helpers.get_current_line_str(),
        amount=2_000_000_000,
    )


def _get_pool_shares(*, snapshot: dict, pool_ids: dict[str, str]) -> dict[str, fractions.Fraction]:
    """Return the share of the "set" stake of each cluster pool.

    The committee is seated from the "set" snapshot, and a seat's weight is the pool's
    `stakeSet` over the total of them.

    Args:
        snapshot: The output of the stake snapshot query for all stake pools.
        pool_ids: Hex pool IDs of the cluster pools, by pool name.

    Returns:
        The stake share of each cluster pool, by pool name.
    """
    pools_stake = {p: int(s["stakeSet"]) for p, s in snapshot["pools"].items()}
    total_stake = sum(pools_stake.values())
    assert total_stake, f"The stake snapshot reports no 'set' stake: {snapshot['pools']}"

    unknown_stake = sorted(n for n, i in pool_ids.items() if i not in pools_stake)
    assert not unknown_stake, (
        f"The stake snapshot reports no stake for {', '.join(unknown_stake)}: {sorted(pools_stake)}"
    )

    return {n: fractions.Fraction(pools_stake[i], total_stake) for n, i in pool_ids.items()}


class TestLeiosPParamsGov:
    """Tests for changing the Leios protocol parameters through governance."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.xdist_split(markers.XdSplits.governance, markers.XdSplits.heavy)
    @pytest.mark.long
    def test_committee_size_and_quorum(
        self,
        cluster_lock_leios_gov: governance_utils.GovClusterT,
        pool_user_llg: clusterlib.PoolUser,
        cluster_manager: cluster_management.ClusterManager,
    ):
        """Test that a change of the Leios committee size and quorum takes effect.

        The committee is shrunk to a single seat, and the quorum is lowered below the stake
        share of a single pool. With the default quorum of 0.75 a single pool out of the
        cluster pools of equal stake could never certify an EB alone, so a certificate
        assembled by the single seat committee is what proves that the lowered quorum is
        in effect.

        * Compute a quorum that the smallest cluster pool reaches alone
        * Submit an action changing the committee size to 1, approve it by CC and DReps,
          and leave it without SPO votes
        * Submit an action changing the committee size to 1 and the quorum to the computed
          value, approve it by CC, DReps and SPOs
        * Check that only the action with the SPO votes is ratified in the next epoch
        * Check that the pparams are changed in the enactment epoch, and that the committee
          of that epoch still holds more than one seat
        * In the next epoch, check that the committee holds a single seat, taken by the
          cluster pool with the most stake (ties broken by ascending pool id), and that the
          pool's stake share reaches the lowered quorum
        * Search the pool logs for the rest of the epoch
        * Check that the pools without a seat cast no vote and answered the EB
          announcements with `NotOnCommittee`
        * Check that the seated pool voted, and that a certificate was assembled
        """
        cluster, governance_data = cluster_lock_leios_gov
        temp_template = common.get_test_id(cluster)

        state_dir = cluster_nodes.get_cluster_env().state_dir
        pool_ids = {
            pool_name: helpers.get_pool_id_hex(
                delegation.get_pool_id(
                    cluster_obj=cluster,
                    addrs_data=cluster_manager.cache.addrs_data,
                    pool_name=pool_name,
                )
            )
            for pool_name in cluster_management.Resources.ALL_POOLS
        }
        pool_logs = {
            pool_name: state_dir / f"{pool_name.replace('node-', '')}.stdout"
            for pool_name in pool_ids
        }
        missing_logs = sorted(str(f) for f in pool_logs.values() if not f.exists())
        assert not missing_logs, f"Pool log files not found: {', '.join(missing_logs)}"

        # Whichever pool gets the single seat must reach the quorum alone
        pool_shares = _get_pool_shares(
            snapshot=cluster.g_query.get_stake_snapshot(all_stake_pools=True), pool_ids=pool_ids
        )
        quorum = fractions.Fraction(
            min(pool_shares.values()) * fractions.Fraction(QUORUM_STAKE_SHARE_RATIO)
        ).limit_denominator(1000)
        init_quorum = conway_common.get_rational_pparam(
            cluster.g_query.get_protocol_params()["leiosQuorumStakeThreshold"]
        )
        assert 0 < quorum < init_quorum, (
            f"The computed quorum {quorum} is not between 0 and the current quorum "
            f"{init_quorum}, so it cannot show the effect of the change: {pool_shares}"
        )

        def _propose(
            name_template: str, proposals: list[clusterlib_utils.UpdateProposal]
        ) -> conway_common.PParamPropRec:
            anchor_data = governance_utils.get_default_anchor_data()
            return conway_common.propose_pparams_update(
                cluster_obj=cluster,
                name_template=name_template,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                pool_user=pool_user_llg,
                proposals=proposals,
            )

        committee_size_proposal = clusterlib_utils.UpdateProposal(
            arg="--leios-committee-size",
            value=1,
            name="leiosCommitteeSize",
        )
        quorum_proposal = clusterlib_utils.UpdateProposal(
            arg="--leios-quorum-stake-threshold",
            value=f"{quorum.numerator}/{quorum.denominator}",
            name="leiosQuorumStakeThreshold",
            check_func=conway_common.check_rational_pparam,
        )

        # The proposals and all the votes have to land in the same epoch, so that the
        # epoch arithmetic below holds for both actions
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=-int(cluster.epoch_length_sec // 2)
        )
        approve_epoch = cluster.g_query.get_epoch()

        # Submitted first, so that a ratification it doesn't deserve would be seen
        # before the other action makes it stale
        nospo_prop_rec = _propose(
            name_template=f"{temp_template}_nospo", proposals=[committee_size_proposal]
        )
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_nospo",
            payment_addr=pool_user_llg.payment,
            action_txid=nospo_prop_rec.action_txid,
            action_ix=nospo_prop_rec.action_ix,
            approve_cc=True,
            approve_drep=True,
            approve_spo=None,
        )

        fin_update_proposals = [committee_size_proposal, quorum_proposal]
        fin_prop_rec = _propose(
            name_template=f"{temp_template}_fin", proposals=fin_update_proposals
        )
        clusterlib_utils.check_updated_params(
            update_proposals=fin_update_proposals, protocol_params=fin_prop_rec.future_pparams
        )
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_fin",
            payment_addr=pool_user_llg.payment,
            action_txid=fin_prop_rec.action_txid,
            action_ix=fin_prop_rec.action_ix,
            approve_cc=True,
            approve_drep=True,
            approve_spo=True,
        )

        # The window depends on the epoch length of the testnet variant, so this is an
        # environment limit rather than a failure
        if cluster.g_query.get_epoch() != approve_epoch:
            pytest.skip("The proposals and the votes didn't fit into a single epoch")

        # Check ratification
        cluster.wait_for_epoch(epoch_no=approve_epoch + 1, padding_seconds=5, future_is_ok=False)
        rat_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{approve_epoch + 1}"
        )
        assert governance_utils.lookup_ratified_actions(
            state=rat_gov_state, action_txid=fin_prop_rec.action_txid
        ), "The action approved by CC, DReps and SPOs was not ratified"
        assert not governance_utils.lookup_ratified_actions(
            state=rat_gov_state, action_txid=nospo_prop_rec.action_txid
        ), "The action changing a security group pparam was ratified without the SPO votes"

        # Check enactment. The quorum applies from this epoch on, the committee size only
        # from the next epoch.
        enact_epoch = cluster.wait_for_epoch(
            epoch_no=approve_epoch + 2, padding_seconds=5, future_is_ok=False
        )
        clusterlib_utils.check_updated_params(
            update_proposals=fin_update_proposals,
            protocol_params=cluster.g_query.get_protocol_params(),
        )
        enact_committee = (
            cluster.g_query.get_stake_snapshot(all_stake_pools=True).get("leiosCommittee") or []
        )
        assert len(enact_committee) > 1, (
            f"The Leios committee of the enactment epoch {enact_epoch} holds "
            f"{len(enact_committee)} seats, but the new committee size is expected to apply "
            f"only from the next epoch: {enact_committee}"
        )

        # The committee of the next epoch is seated from the snapshot with the new size.
        # The padding lets the votes on the EBs of the previous epoch settle.
        cluster.wait_for_epoch(
            epoch_no=enact_epoch + 1, padding_seconds=leios.EPOCH_MARGIN_SEC, future_is_ok=False
        )

        # One query for the committee and for the stake it was selected from, so that an
        # epoch boundary cannot split the two
        snapshot = cluster.g_query.get_stake_snapshot(all_stake_pools=True)
        search_epoch = cluster.g_query.get_epoch()
        assert search_epoch == enact_epoch + 1, (
            f"The cluster instance is in epoch {search_epoch}, expected {enact_epoch + 1}"
        )
        assert cluster.time_to_epoch_end() > EPOCH_TAIL_SEC, (
            f"Not enough time left in epoch {search_epoch} for the log search"
        )

        committee = snapshot.get("leiosCommittee") or []
        seated_ids = {s["poolId"] for s in committee}
        assert len(committee) == 1, (
            f"The Leios committee of epoch {search_epoch} holds {len(committee)} seats, "
            f"expected 1: {committee}"
        )

        # The single seat goes to the pool with the most stake, ties broken by ascending
        # pool id. Only the cluster pools are ranked - a pool registered by another test
        # holds a tiny fraction of their stake.
        search_shares = _get_pool_shares(snapshot=snapshot, pool_ids=pool_ids)
        top_pool_name = min(pool_ids, key=lambda n: (-search_shares[n], pool_ids[n]))
        assert seated_ids == {pool_ids[top_pool_name]}, (
            f"The Leios committee of epoch {search_epoch} seated {committee}, expected the "
            f"top ranked pool '{top_pool_name}': {search_shares}"
        )
        assert search_shares[top_pool_name] >= quorum, (
            f"The seated pool '{top_pool_name}' holds {search_shares[top_pool_name]} of the "
            f"stake, less than the quorum {quorum}"
        )

        found_per_pool, search_problems = leios.collect_vote_outcome_msgs(
            pool_logs=list(pool_logs.values()),
            deadline=time.monotonic() + leios.MAX_SEARCH_SEC,
        )

        # A vote cast in the next epoch would come from a committee this test never read
        end_epoch = cluster.g_query.get_epoch()
        if end_epoch != search_epoch:
            pytest.skip(
                f"The search started in epoch {search_epoch} and ended in epoch {end_epoch}, "
                "which seats a committee of its own, so the result is inconclusive"
            )

        errors, skip_reasons = leios.get_voting_problems(
            found_per_pool={n: found_per_pool[f] for n, f in pool_logs.items()},
            out_pool_names=frozenset(n for n in pool_ids if n != top_pool_name),
            epoch=search_epoch,
        )

        if errors:
            errors.extend(search_problems)
        assert not errors, "\n".join(errors)

        if skip_reasons:
            pytest.skip("; ".join([*skip_reasons, *search_problems]))

    @allure.link(helpers.get_vcs_link())
    def test_edge_values_well_formed(
        self,
        cluster_use_governance: governance_utils.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test that the ledger accepts edge values of the Leios committee size and quorum.

        The Dijkstra `ppuWellFormed` has no bounds checks for the Leios pparams, so an
        action with the edge values is accepted. The actions are never voted on, so they
        are never enacted. A committee size of 0 would seat an empty committee, so no EB
        could be voted on or certified - that consequence is not checked here.

        * Submit an action changing the committee size to 0 and the quorum to 0
        * Submit an action changing the committee size to the max Word16 value and the
          quorum to 1
        * Check that both actions are in the governance state with the proposed values
        """
        cluster, __ = cluster_use_governance
        temp_template = common.get_test_id(cluster)

        edge_proposals = {
            "min": [
                clusterlib_utils.UpdateProposal(
                    arg="--leios-committee-size",
                    value=0,
                    name="leiosCommitteeSize",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--leios-quorum-stake-threshold",
                    value="0/1",
                    name="leiosQuorumStakeThreshold",
                    check_func=conway_common.check_rational_pparam,
                ),
            ],
            "max": [
                clusterlib_utils.UpdateProposal(
                    arg="--leios-committee-size",
                    value=2**16 - 1,
                    name="leiosCommitteeSize",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--leios-quorum-stake-threshold",
                    value="1/1",
                    name="leiosQuorumStakeThreshold",
                    check_func=conway_common.check_rational_pparam,
                ),
            ],
        }

        errors = []
        for edge, proposals in edge_proposals.items():
            anchor_data = governance_utils.get_default_anchor_data()
            # Fails with a CLI error when the ledger rejects the action
            prop_rec = conway_common.propose_pparams_update(
                cluster_obj=cluster,
                name_template=f"{temp_template}_{edge}",
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                pool_user=pool_user_ug,
                proposals=proposals,
            )
            try:
                clusterlib_utils.check_updated_params(
                    update_proposals=proposals, protocol_params=prop_rec.future_pparams
                )
            except AssertionError as err:
                errors.append(f"The '{edge}' action: {err}")

        assert not errors, "\n".join(errors)
