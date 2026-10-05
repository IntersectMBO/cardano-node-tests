"""Tests for Leios endorser blocks (EBs).

The tests check that the node reports the expected Leios activity in its logs. The
Leios trace messages are emitted only in the Dijkstra era, and only when there is
enough Tx load to fill the mempool, so that the block producer has something to put
into an endorser block.

Who may vote on an EB is decided once per epoch by the Leios voting committee, which
the ledger seats from the stake snapshot: the ``leiosCommitteeSize`` pools with the
most stake, largest first, ties broken by ascending pool id. A local testnet has far
fewer pools than the committee has room for, so every pool is normally seated and the
ranking never shows. `TestLeiosCommitteeRank` starts a cluster instance whose committee
has fewer seats than the cluster has pools, which is what makes the ranking, and the
pools it leaves out, observable.

`TestLeiosEbTxs` follows particular txs from the mempool through an EB and its
certificate into the ledger.
"""

import dataclasses
import datetime
import fractions
import json
import logging
import pathlib as pl
import re
import time
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import markers
from cardano_node_tests.tests.tests_dijkstra import bls
from cardano_node_tests.tests.tests_dijkstra import leios
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.leios,
    markers.SKIPIF_ON_TESTNET,
    pytest.mark.skipif(bool(leios.SKIP_REASON), reason=leios.SKIP_REASON),
]

_ALL_POOLS_MSGS_SET = frozenset(leios.EB_MSGS_ALL_POOLS)
_ANY_POOL_MSGS_SET = frozenset(leios.EB_MSGS_ANY_POOL)
_VOTING_MSGS_SET = frozenset(leios.VOTING_MSGS)
_PRE_VOTING_MSGS_SET = frozenset((*leios.VOTING_MSGS, leios.NOT_ON_COMMITTEE_MSG))

# Max number of new blocks to wait for while the expected messages are showing up in
# the logs. Certification of an EB needs a quorum of votes and an RB that announces it,
# so it can take several blocks before all the expected messages are reported.
MAX_WAIT_BLOCKS = 40
# Number of new blocks to wait for between two searches of the logs
WAIT_BLOCKS_STEP = 5
# Max number of new blocks to wait for the certifying RB to settle. A testnet with a big
# `securityParam` would take hours to make the RB immutable, so only the local clusters
# with a small one wait for all of `securityParam`.
MAX_SETTLE_BLOCKS = 20

# The Leios committee size the `small_committee_start_cluster` fixture sets: one seat
# short of the number of pools, so that the committee has to leave exactly one pool out.
SMALL_COMMITTEE_SIZE = configuration.NUM_POOLS - 1

# The quorum the same fixture sets. A seat is weighted by the pool's share of the
# *active* stake, not by its share of the committee, so a committee that doesn't hold
# every pool cannot reach the default 0.75 - with pools of equal stake its total weight
# is only `SMALL_COMMITTEE_SIZE / NUM_POOLS`. The quorum is lowered along with the
# committee, so that the instance still certifies EBs and the test observes a committee
# that works rather than one that is merely smaller.
#
# That ratio is the bound to keep in mind when changing the committee size: the quorum
# has to stay below `SMALL_COMMITTEE_SIZE / NUM_POOLS`, or nothing can ever be certified
# and the test only ever reports that. One seat short of every pool leaves it at 2/3 or
# better, so 0.5 clears it for any pool count the cluster allows.
SMALL_COMMITTEE_QUORUM = 0.5

# The pool whose node `TestLeiosQuorum` stops. The Tx load generator submits to `pool1`,
# so stopping that one would also stop the load the EBs are made of.
QUORUM_STOPPED_POOL = cluster_management.Resources.POOL3

# Seconds to wait after the stop for the votes on the EBs announced before it to be
# settled. A vote is cast 3 sec after the EB arrives and is accepted for 4 more sec,
# so 10 sec cover the vote window with a margin.
QUORUM_VOTE_SETTLE_SEC = 10

# Number of payment Txs to submit while the quorum is lost
QUORUM_OUTAGE_TXS = 3

# Min number of blocks the running pools must forge while the quorum is lost. With 2/3
# of the stake left, ~12 blocks are expected in `leios.MAX_SEARCH_SEC` on `leios_fast`.
QUORUM_OUTAGE_MIN_BLOCKS = 3

# Max number of seconds to wait for a certificate once the stopped pool is back. The
# node needs to start and catch up with the chain before its votes count again.
QUORUM_RECOVERY_SEARCH_SEC = 2 * leios.MAX_SEARCH_SEC

# How much of an epoch a log search needs: the window itself plus the margin that keeps
# its end away from the epoch boundary.
EPOCH_TAIL_SEC = leios.MAX_SEARCH_SEC + leios.EPOCH_MARGIN_SEC


@pytest.fixture
def cluster_leios(cluster: clusterlib.ClusterLib) -> clusterlib.ClusterLib:
    """Return a cluster instance that is able to produce Leios endorser blocks."""
    leios.skip_if_no_ebs(cluster_obj=cluster)
    return cluster


@pytest.fixture(scope="module")
def small_committee_start_cluster() -> pl.Path:
    """Return startup scripts whose Leios committee has no room for every pool.

    The committee size and the quorum are Dijkstra protocol parameters, so they are set
    in the Dijkstra genesis spec and not in the Shelley one the other custom cluster
    fixtures edit.
    """
    shared_tmp = temptools.get_pytest_shared_tmp()

    # Need to lock because this same fixture can run on several workers in parallel
    with locking.FileLockIfXdist(f"{shared_tmp}/startup_files_small_leios_committee.lock"):
        destdir = shared_tmp / "startup_files_small_leios_committee"
        destdir.mkdir(exist_ok=True)

        # Return the existing scripts dir if it was already generated by another worker
        destdir_ls = list(destdir.glob("start-cluster*"))
        if destdir_ls:
            return destdir_ls[0].parent

        startup_files = cluster_nodes.get_cluster_type().cluster_scripts.copy_scripts_files(
            destdir=destdir
        )
        genesis_spec_file = startup_files.genesis_spec.parent / "genesis.dijkstra.spec.json"
        with open(genesis_spec_file, encoding="utf-8") as fp_in:
            genesis_spec = json.load(fp_in)

        genesis_spec["leiosCommitteeSize"] = SMALL_COMMITTEE_SIZE
        genesis_spec["leiosQuorumStakeThreshold"] = SMALL_COMMITTEE_QUORUM

        with open(genesis_spec_file, "w", encoding="utf-8") as fp_out:
            json.dump(genesis_spec, fp_out)

        return startup_files.start_script.parent


@pytest.fixture
def cluster_small_committee(
    cluster_manager: cluster_management.ClusterManager,
    small_committee_start_cluster: pl.Path,
) -> clusterlib.ClusterLib:
    """Return a cluster instance whose Leios committee has no room for every pool.

    Spinning the instance up means starting a dedicated cluster from custom genesis, so
    what can rule the test out is checked first, off the startup scripts.
    """
    with open(small_committee_start_cluster / "genesis.spec.json", encoding="utf-8") as in_fp:
        genesis_spec = json.load(in_fp)
    leios.skip_if_no_ebs_in_genesis(genesis=genesis_spec)

    # The whole log search has to fit into a single epoch, so that a vote cast in the
    # next epoch - by a committee this test never read - cannot land in it
    epoch_length_sec = float(genesis_spec["epochLength"]) * float(genesis_spec["slotLength"])
    if epoch_length_sec <= EPOCH_TAIL_SEC:
        pytest.skip(
            f"An epoch takes only {epoch_length_sec:.0f} sec on the "
            f"'{configuration.TESTNET_VARIANT}' testnet variant, which is not enough for "
            f"the {EPOCH_TAIL_SEC} sec search window"
        )

    cluster_obj = cluster_manager.get(
        lock_resources=[cluster_management.Resources.CLUSTER],
        prio=True,
        cleanup=True,
        scriptsdir=small_committee_start_cluster,
    )
    return cluster_obj


@pytest.fixture
def cluster_leios_lock(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Lock the whole cluster instance and skip unless Leios EBs can be observed on it.

    The whole instance is locked because the test stops one of the pool nodes, which
    takes a part of the stake out of block production and out of the Leios voting.
    """
    cluster_obj = cluster_manager.get(lock_resources=[cluster_management.Resources.CLUSTER])
    leios.skip_if_no_ebs(cluster_obj=cluster_obj)
    return cluster_obj


def _get_missing_msgs_errors(*, found_per_pool: dict[pl.Path, set[str]]) -> list[str]:
    """Return error messages for the expected EB messages that are missing.

    Args:
        found_per_pool: The EB messages that were found in each pool log.

    Returns:
        An error message per missing EB message. Empty when all the expected messages
        were found.
    """
    errors = [
        f"No line matching `{r}` found in '{logfile}'."
        for logfile, found in found_per_pool.items()
        for r in leios.EB_MSGS_ALL_POOLS
        if r not in found
    ]

    found_any_pool = {m for found in found_per_pool.values() for m in found}
    errors.extend(
        f"No line matching `{r}` found in any of the pool logs."
        for r in leios.EB_MSGS_ANY_POOL
        if r not in found_any_pool
    )

    return errors


def _collect_eb_msgs(
    *, cluster_obj: clusterlib.ClusterLib, pool_logs: list[pl.Path]
) -> tuple[dict[pl.Path, set[str]], list[str]]:
    """Wait for new blocks and collect the expected EB messages from the pool logs.

    Each round searches only the part of a log that was appended since the previous
    round, and only for the messages that are still missing there, so waiting for many
    blocks doesn't mean reading the whole log over and over.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pool_logs: Log files of the block producing nodes.

    Returns:
        The EB messages found in each pool log, and the problems that got in the way of
        the search - a log file that could not be searched, or a stalled chain. They
        explain a missing message, so they are reported only together with one.
    """
    searches = leios.init_searches(pool_logs)
    stall_error = ""
    log_errors: dict[pl.Path, str] = {}

    def _missing_msgs(search: leios.LogSearch) -> tp.Collection[str]:
        found_any_pool = {m for s in searches.values() for m in s.found}
        return (_ALL_POOLS_MSGS_SET - search.found) | (_ANY_POOL_MSGS_SET - found_any_pool)

    for __ in range(MAX_WAIT_BLOCKS // WAIT_BLOCKS_STEP):
        try:
            cluster_obj.wait_for_new_block(new_blocks=WAIT_BLOCKS_STEP)
        except clusterlib.CLIError as err:
            # The chain stalled. Search the logs one last time, so that the test can
            # report what was still missing and not just the stall itself.
            stall_error = f"The chain stalled while waiting for new blocks: {err}"

        leios.search_round(searches=searches, missing_msgs=_missing_msgs, log_errors=log_errors)

        found_per_pool = {p: s.found for p, s in searches.items()}
        if stall_error or not _get_missing_msgs_errors(found_per_pool=found_per_pool):
            break

    problems = list(log_errors.values())
    if stall_error:
        problems.append(stall_error)

    return {p: s.found for p, s in searches.items()}, problems


def _collect_pre_voting_msgs(
    *, pool_logs: list[pl.Path], deadline: float
) -> tuple[dict[pl.Path, set[str]], list[str]]:
    """Search the pool logs for voting activity while the voting committee is empty.

    The search stops as soon as every pool log holds a `NotOnCommittee` message, as that
    is the proof that the pool could not vote, or as soon as any voting message shows up,
    as that is what the caller reports. Each round searches only the part of a log that
    was appended since the previous round.

    Args:
        pool_logs: Log files of the block producing nodes.
        deadline: A `time.monotonic()` value the search must not go past, so that the
            searched part of the logs stays inside the epoch the search started in.

    Returns:
        The searched messages found in each pool log, and the problems that got in the
        way of the search - a log file that could not be searched. They explain a missing
        message, so they are reported only together with one.
    """
    searches = leios.init_searches(pool_logs)
    log_errors: dict[pl.Path, str] = {}

    while True:
        time.sleep(min(leios.SEARCH_STEP_SEC, max(0.0, deadline - time.monotonic())))

        leios.search_round(
            searches=searches,
            missing_msgs=lambda search: _PRE_VOTING_MSGS_SET - search.found,
            log_errors=log_errors,
        )

        # Voting activity is what the caller reports, no need to keep searching for it
        if any(s.found & _VOTING_MSGS_SET for s in searches.values()):
            break
        # Every pool reported that it is not a member of the voting committee
        if all(leios.NOT_ON_COMMITTEE_MSG in s.found for s in searches.values()):
            break
        if time.monotonic() >= deadline:
            break

    return {p: s.found for p, s in searches.items()}, list(log_errors.values())


def _get_voting_share_without(
    *, cluster_obj: clusterlib.ClusterLib, pool_id: str
) -> tuple[fractions.Fraction, int]:
    """Return the committee weight that is left when a pool stops voting.

    A seat is weighted by the pool's share of the stake the committee was selected
    from, which the stake snapshot query reports as `stakeSet`.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pool_id: A hex-encoded ID of the stake pool that stops voting.

    Returns:
        The summed weight of the committee seats of all the other pools, and the epoch
        of the committee.
    """
    # One query for the committee and for the stake it was selected from, so that an
    # epoch boundary cannot split the two
    snapshot = cluster_obj.g_query.get_stake_snapshot(all_stake_pools=True)
    epoch = cluster_obj.g_query.get_epoch()

    pools_stake = {p: int(s["stakeSet"]) for p, s in snapshot["pools"].items()}
    total_stake = sum(pools_stake.values())
    assert total_stake, f"The stake snapshot of epoch {epoch} reports no stake: {snapshot}"

    seated_ids = {s["poolId"] for s in snapshot.get("leiosCommittee") or []}
    assert pool_id in seated_ids, (
        f"The pool '{pool_id}' holds no seat on the Leios committee of epoch {epoch}, so "
        f"stopping it cannot take the committee below the quorum: {sorted(seated_ids)}"
    )

    left_stake = sum(pools_stake.get(p, 0) for p in seated_ids if p != pool_id)
    return fractions.Fraction(left_stake, total_stake), epoch


def _get_outage_errors(
    *,
    found_per_pool: dict[pl.Path, set[str]],
    missing_txs: list[str],
    outage_blocks: int,
    left_share: fractions.Fraction,
    quorum: fractions.Fraction,
) -> list[str]:
    """Judge what the running pools did while the stopped pool took the quorum with it.

    Args:
        found_per_pool: The searched messages found in the log of each running pool.
        missing_txs: IDs of the Txs submitted meanwhile whose outputs are not in the UTxO.
        outage_blocks: Number of blocks forged meanwhile.
        left_share: The committee weight of the running pools.
        quorum: The quorum the certificates need.

    Returns:
        The failures. Empty when the chain kept going without certifying any EB.
    """
    errors = [
        f"Found a line matching `{r}` in '{logfile}' while the pool "
        f"'{QUORUM_STOPPED_POOL}' was stopped and the committee held only "
        f"{float(left_share):.3f} of the stake, below the quorum {float(quorum):.3f}."
        for logfile, found in found_per_pool.items()
        for r in (leios.MSG_CERTIFIED, leios.MSG_BLOCK_CERTIFIED)
        if r in found
    ]
    if missing_txs:
        errors.append(
            f"Outputs of Txs submitted while the quorum was lost are not in the UTxO: {missing_txs}"
        )
    if outage_blocks < QUORUM_OUTAGE_MIN_BLOCKS:
        errors.append(
            f"Only {outage_blocks} blocks were forged while the pool '{QUORUM_STOPPED_POOL}' "
            f"was stopped, expected at least {QUORUM_OUTAGE_MIN_BLOCKS}."
        )
    return errors


class TestLeios:
    """Tests for Leios endorser blocks."""

    @allure.link(helpers.get_vcs_link())
    # Scheduled at the end of the testrun, so that the cluster instance is already past
    # `leios.VOTING_START_EPOCH` and the wait for it is a no-op
    @pytest.mark.order(-10)
    @pytest.mark.long
    def test_eb_logs(
        self,
        cluster_leios: clusterlib.ClusterLib,
    ):
        """Check that the nodes report the expected endorser block activity.

        * Wait for the epoch in which the voting committee becomes active
        * Record the current end of each pool log file
        * Wait for new blocks to be created
        * Check that each pool reports EB announcements, votes and certificates
        * Check that at least one pool reports forging, announcing, storing and
          certifying an EB
        """
        cluster = cluster_leios
        # Waits for up to `leios.VOTING_START_EPOCH` epochs, and then up to
        # `MAX_WAIT_BLOCKS` blocks (an epoch on `leios_fast`)
        common.skip_on_long_epochs(cluster_obj=cluster, epochs=leios.VOTING_START_EPOCH + 1)
        common.get_test_id(cluster)

        # Votes and certificates cannot show up in the logs before the voting committee
        # is active, so searching for them earlier would always fail
        cluster.wait_for_epoch(epoch_no=leios.VOTING_START_EPOCH, padding_seconds=5)

        state_dir = cluster_nodes.get_cluster_env().state_dir
        pool_logs = sorted(state_dir.glob("pool*.stdout"))
        assert pool_logs, f"No pool log files found in '{state_dir}'"

        found_per_pool, search_problems = _collect_eb_msgs(cluster_obj=cluster, pool_logs=pool_logs)

        errors = _get_missing_msgs_errors(found_per_pool=found_per_pool)
        if errors:
            errors.extend(search_problems)

        assert not errors, "\n".join(errors)

    @allure.link(helpers.get_vcs_link())
    # Scheduled near the start of the testrun, while the cluster instance is still
    # before `leios.VOTING_START_EPOCH`
    @pytest.mark.order(5)
    @pytest.mark.long
    def test_no_voting_before_committee_epoch(
        self,
        cluster_leios: clusterlib.ClusterLib,
    ):
        """Check that no EB is voted on before the voting committee becomes active.

        * Skip when the genesis seats the voting committee from epoch 0, as there is
          then no epoch in which a vote would be premature
        * Skip when no epoch before `leios.VOTING_START_EPOCH` has room left for the
          whole
          search window
        * Wait for a point in an epoch where the window fits before the next epoch
          boundary
        * Record the current end of each pool log file
        * Search the log content that gets appended, until every pool reports that it
          declined to vote because it is not a member of the voting committee
        * Check that no pool reported a vote, a vote from a peer or a certificate
        """
        cluster = cluster_leios
        common.get_test_id(cluster)

        if leios.is_committee_seated_in_genesis(cluster_obj=cluster):
            pytest.skip(
                "The pools are on the Leios voting committee from epoch 0, as their BLS keys "
                "come from the genesis, so there is no epoch in which a vote is premature"
            )

        state_dir = cluster_nodes.get_cluster_env().state_dir
        pool_logs = sorted(state_dir.glob("pool*.stdout"))
        assert pool_logs, f"No pool log files found in '{state_dir}'"

        # The searched log window must not cross an epoch boundary, otherwise a vote from
        # `leios.VOTING_START_EPOCH` could land in it. It fits into an epoch only when it starts
        # at least `epoch_tail_sec` before the end of that epoch.
        epoch_tail_sec = leios.MAX_SEARCH_SEC + leios.EPOCH_MARGIN_SEC
        if cluster.epoch_length_sec <= epoch_tail_sec:
            pytest.skip(
                f"An epoch takes only {cluster.epoch_length_sec:.0f} sec on the "
                f"'{configuration.TESTNET_VARIANT}' testnet variant, which is not enough for "
                f"the {epoch_tail_sec} sec search window"
            )

        # One tip for both values, so that the epoch cannot flip between them. Check
        # before waiting for the interval, as that wait can take a whole epoch.
        tip = cluster.g_query.get_tip()
        init_epoch = int(tip["epoch"])
        last_usable_epoch = leios.VOTING_START_EPOCH - 1
        if init_epoch > last_usable_epoch or (
            init_epoch == last_usable_epoch
            and cluster.time_from_epoch_start(tip=tip) > cluster.epoch_length_sec - epoch_tail_sec
        ):
            pytest.skip(
                f"The cluster instance is in epoch {init_epoch} and no epoch before "
                f"{leios.VOTING_START_EPOCH}, in which the Leios voting committee becomes active, "
                f"has {epoch_tail_sec} sec left for the search window"
            )

        clusterlib_utils.wait_for_epoch_interval(cluster_obj=cluster, start=0, stop=-epoch_tail_sec)

        # The wait can cross into the next epoch, so the epoch the search runs in is not
        # necessarily the one seen above
        search_epoch = cluster.g_query.get_epoch()
        if search_epoch >= leios.VOTING_START_EPOCH:
            pytest.skip(
                f"The cluster instance is already in epoch {search_epoch}, the Leios voting "
                f"committee is active since epoch {leios.VOTING_START_EPOCH}"
            )

        found_per_pool, search_problems = _collect_pre_voting_msgs(
            pool_logs=pool_logs, deadline=time.monotonic() + leios.MAX_SEARCH_SEC
        )

        # A vote is legitimate from `leios.VOTING_START_EPOCH` on, so the result means nothing
        # if the searched window reached that epoch after all
        end_epoch = cluster.g_query.get_epoch()
        if end_epoch >= leios.VOTING_START_EPOCH:
            pytest.skip(
                f"The search started in epoch {search_epoch} and ended in epoch {end_epoch}, "
                "in which the Leios voting committee is active, so the result is inconclusive"
            )

        errors = [
            f"Found a line matching `{r}` in '{logfile}' in epoch {search_epoch}."
            for logfile, found in found_per_pool.items()
            for r in leios.VOTING_MSGS
            if r in found
        ]
        if errors:
            errors.extend(search_problems)

        assert not errors, "\n".join(errors)

        # Without a pool declining to vote there was nothing to vote on, so the absence
        # of votes doesn't say anything about the voting committee
        no_evidence = sorted(
            str(logfile)
            for logfile, found in found_per_pool.items()
            if leios.NOT_ON_COMMITTEE_MSG not in found
        )
        if no_evidence:
            pytest.skip(
                "; ".join(
                    [
                        (
                            "No pool declined to vote with `NotOnCommittee` in "
                            f"{', '.join(no_evidence)}, so the absence of votes in epoch "
                            f"{search_epoch} is inconclusive"
                        ),
                        *search_problems,
                    ]
                )
            )


class TestLeiosCommitteeRank:
    """Tests for the pools the Leios voting committee has no room for."""

    @allure.link(helpers.get_vcs_link())
    # It would be better to use `cluster_nodes.get_cluster_type().uses_shortcut`, but we
    # would need to get a cluster instance first. That would be too expensive in this test,
    # as we are using custom startup scripts.
    @pytest.mark.skipif(
        "_fast" not in configuration.TESTNET_VARIANT,
        reason="Runs only on local cluster with HF shortcut.",
    )
    @pytest.mark.order(5)
    @pytest.mark.xdist_split(markers.XdSplits.heavy)
    @pytest.mark.long
    def test_ranked_out_pool_doesnt_vote(
        self,
        cluster_small_committee: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
    ):
        """Check that the pools the Leios committee left out are not voting.

        * Start a local cluster instance whose Leios committee is one seat short of the
          number of pools, with a quorum the smaller committee can still reach
        * Wait for the epoch in which the voting committee becomes active
        * Find the cluster pools the committee left out - at least one, as it has fewer
          seats than the cluster has pools
        * Check that they were left out on rank alone - they have a registered BLS key,
          and no seated pool ranks behind them by stake, ties broken by ascending pool id
        * Search the pool logs for the rest of the epoch the committee was read in
        * Check that the pools without a seat cast no vote and answered the EB
          announcements they did see with `NotOnCommittee`
        * Check that the seated pools did vote, so that the silence of the others is the
          committee and not a quiet cluster
        * Check that a certificate was assembled, so that the seats the committee has
          left still add up to the lowered quorum
        """
        cluster = cluster_small_committee
        # Waits for up to `leios.VOTING_START_EPOCH` epochs, and then the log search
        common.skip_on_long_epochs(cluster_obj=cluster, epochs=leios.VOTING_START_EPOCH + 1)
        common.get_test_id(cluster)

        committee_size = cluster.g_query.get_protocol_params()["leiosCommitteeSize"]
        assert committee_size == SMALL_COMMITTEE_SIZE, (
            f"The cluster instance seats {committee_size} pools on the Leios committee, "
            f"expected {SMALL_COMMITTEE_SIZE}"
        )

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

        # A pool registered by a transaction is on no committee until this epoch. The
        # wait is a no-op when the pools come with their BLS key straight from the
        # genesis, which is the setup this test normally runs on.
        if not leios.is_committee_seated_in_genesis(cluster_obj=cluster):
            cluster.wait_for_epoch(epoch_no=leios.VOTING_START_EPOCH, padding_seconds=5)

        # Start the search early enough in an epoch for the whole window to fit into it
        clusterlib_utils.wait_for_epoch_interval(cluster_obj=cluster, start=0, stop=-EPOCH_TAIL_SEC)

        # One query for the committee and for the stake it was selected from, so that an
        # epoch boundary cannot split the two
        snapshot = cluster.g_query.get_stake_snapshot(all_stake_pools=True)
        search_epoch = cluster.g_query.get_epoch()

        committee = snapshot.get("leiosCommittee") or []
        assert len(committee) <= SMALL_COMMITTEE_SIZE, (
            f"The Leios committee of epoch {search_epoch} holds {len(committee)} seats, "
            f"more than the {SMALL_COMMITTEE_SIZE} it is configured for: {committee}"
        )

        # The committee has fewer seats than there are cluster pools, and the instance
        # holds no other pool - it is respun from the custom scripts for this test, which
        # locks it for the whole run. How many pools that leaves out depends on how many
        # the cluster was started with, so the test doesn't pin the number down.
        seated_ids = {s["poolId"] for s in committee}
        out_pools = {n: i for n, i in pool_ids.items() if i not in seated_ids}
        assert out_pools, (
            f"The Leios committee of epoch {search_epoch} seated every one of the "
            f"{len(pool_ids)} cluster pools, so there is none left out to check: {committee}"
        )

        # The pools have to be off the committee on rank, not for want of a key
        keyless = sorted(
            pool_name
            for pool_name, pool_id in out_pools.items()
            if not bls.get_registered_bls_key(cluster_obj=cluster, pool_id=pool_id)
        )
        assert not keyless, (
            f"The Leios committee of epoch {search_epoch} left out {', '.join(keyless)}, "
            "which have no registered BLS key, so they could not have been seated anyway"
        )

        # `selectLeiosCommittee` seats the pools with the most stake, largest first, ties
        # broken by ascending pool id. Every seated pool therefore has to rank before
        # every pool that was left out, so the best rank among those is the cut off.
        # It runs on the snapshot that rotates into the set position, which is the one
        # the query reports as `stakeSet` - a seat's weight is exactly that pool's
        # `stakeSet` over the total of them.
        pools_stake = {p: int(s["stakeSet"]) for p, s in snapshot["pools"].items()}
        unknown_stake = sorted(n for n, i in pool_ids.items() if i not in pools_stake)
        assert not unknown_stake, (
            f"The stake snapshot of epoch {search_epoch} reports no stake for "
            f"{', '.join(unknown_stake)}, so the committee ranking cannot be checked "
            f"against it: {sorted(pools_stake)}"
        )
        ranks = {pool_id: (-pools_stake[pool_id], pool_id) for pool_id in pool_ids.values()}
        cut_off_rank = min(ranks[pool_id] for pool_id in out_pools.values())
        misranked = sorted(
            pool_name
            for pool_name, pool_id in pool_ids.items()
            if pool_id in seated_ids and ranks[pool_id] > cut_off_rank
        )
        assert not misranked, (
            f"The Leios committee of epoch {search_epoch} seated {', '.join(misranked)}, "
            f"which rank behind {', '.join(sorted(out_pools))} that it left out: {pools_stake}"
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
            out_pool_names=frozenset(out_pools),
            epoch=search_epoch,
        )

        if errors:
            errors.extend(search_problems)
        assert not errors, "\n".join(errors)

        if skip_reasons:
            pytest.skip("; ".join([*skip_reasons, *search_problems]))


@dataclasses.dataclass(frozen=True)
class _CertifiedEb:
    """An EB with test txs in it, that was certified by an RB the network adopted."""

    eb_slot: int
    eb_hash: str
    cert_slot: int
    cert_rb_hash: str
    # When the block producer adopted the certifying RB
    cert_rb_adopted_at: datetime.datetime
    tx_hashes: frozenset[str]


def _get_line_time(line: str) -> datetime.datetime:
    """Return the time a node log line was logged at."""
    m = re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)Z\]", line)
    assert m, f"No timestamp found in the log line: {line}"
    return datetime.datetime.fromisoformat(m.group(1)).replace(tzinfo=datetime.UTC)


def _get_fork_switches_after(
    *, lines_per_log: dict[pl.Path, list[str]], eb: _CertifiedEb
) -> list[str]:
    """Return the fork switches that could have dropped the certifying RB of an EB.

    Those are the fork switches any of the nodes logged after the block producer adopted
    the RB. A switch whose new tip is the RB itself is left out - that is a node that had
    the rival of the RB from a slot battle, and switched to the RB.

    Args:
        lines_per_log: The EB life cycle lines found in each node log.
        eb: The certified EB.

    Returns:
        The log lines of the fork switches, prefixed with the log file name.
    """
    switches = []
    for logfile, lines in lines_per_log.items():
        for line in lines:
            if not re.search(leios.FORK_SWITCH_RE, line):
                continue
            if _get_line_time(line) <= eb.cert_rb_adopted_at:
                continue
            m = re.search(leios.NEW_TIP_RE, line)
            if m and m.group(1) == eb.cert_rb_hash:
                continue
            switches.append(f"{logfile.name}: {line}")
    return switches


def _get_eb_tx_refs(eb_txs: str) -> dict[str, int]:
    """Return the `(tx hash, tx size)` pairs listed in an `EB forged` message."""
    return {h: int(s) for h, s in re.findall(leios.EB_TX_REF_RE, eb_txs)}


@dataclasses.dataclass
class _PoolEbLines:
    """The EB life cycle as seen in the log of one pool."""

    # EB point -> test txs it holds, for the EBs the pool forged and announced
    test_ebs: dict[tuple[int, str], frozenset[str]] = dataclasses.field(default_factory=dict)
    # Slot -> hash of the RB the pool forged and adopted, and when it adopted it
    adopted: dict[int, tuple[str, datetime.datetime]] = dataclasses.field(default_factory=dict)
    # Hashes of the blocks that became the tip of the pool's chain
    tips: set[str] = dataclasses.field(default_factory=set)
    # (cert RB slot, EB slot, EB hash) of the certificates the pool put into an RB
    certs: list[tuple[int, int, str]] = dataclasses.field(default_factory=list)


def _parse_pool_eb_lines(
    *, logfile: pl.Path, lines: list[str], tx_refs: dict[str, int]
) -> tuple[_PoolEbLines, list[str]]:
    """Parse the EB life cycle lines of one pool log.

    Args:
        logfile: The pool log file the lines come from, for the reported problems.
        lines: The lines matching any of the EB life cycle regexes.
        tx_refs: The test txs, as EB tx hash -> serialized tx size.

    Returns:
        The parsed lines, and the test txs that an EB lists with a size other than the
        size of the signed tx.
    """
    parsed = _PoolEbLines()
    problems: list[str] = []
    forged: dict[int, frozenset[str]] = {}
    announced: dict[int, str] = {}

    for line in lines:
        if m := re.search(leios.EB_FORGED_RE, line):
            eb_refs = _get_eb_tx_refs(m.group(2))
            in_eb = frozenset(eb_refs.keys() & tx_refs.keys())
            problems.extend(
                f"The test tx {h} is listed in the EB forged at slot {m.group(1)} in "
                f"'{logfile}' with size {eb_refs[h]}, expected {tx_refs[h]}."
                for h in sorted(in_eb)
                if eb_refs[h] != tx_refs[h]
            )
            if in_eb:
                forged[int(m.group(1))] = in_eb
        elif m := re.search(leios.EB_ANNOUNCED_RE, line):
            announced[int(m.group(1))] = m.group(2)
        elif m := re.search(leios.EB_CERTIFIED_RE, line):
            parsed.certs.append((int(m.group(1)), int(m.group(2)), m.group(3)))
        elif m := re.search(leios.BLOCK_ADOPTED_RE, line):
            parsed.adopted[int(m.group(1))] = (m.group(2), _get_line_time(line))
        elif m := re.search(leios.NEW_TIP_RE, line):
            parsed.tips.add(m.group(1))

    # The `EB forged` message has no EB hash, the `EB announced` message of the same slot
    # gives it
    parsed.test_ebs = {
        (slot, announced[slot]): t for slot, t in forged.items() if slot in announced
    }
    return parsed, problems


def _find_certified_ebs(
    *, lines_per_pool: dict[pl.Path, list[str]], tx_refs: dict[str, int]
) -> tuple[list[_CertifiedEb], list[str]]:
    """Find the certified EBs that hold any of the test txs.

    An EB counts only when every link from the tx to the chain is in the logs:

    * a pool forged the EB with the tx in it, and announced the EB in the RB of the same
      slot
    * a pool forged an RB with a certificate for that EB, and adopted the RB
    * another pool took that RB onto its chain, so the certificate is not just on the
      chain of the pool that forged it

    Args:
        lines_per_pool: The EB life cycle lines found in each pool log.
        tx_refs: The test txs, as EB tx hash -> serialized tx size.

    Returns:
        The certified EBs with test txs in them, and the problems found on the way - a test
        tx listed with a size other than the size of the signed tx.
    """
    problems: list[str] = []
    parsed: dict[pl.Path, _PoolEbLines] = {}
    for logfile, lines in lines_per_pool.items():
        parsed[logfile], pool_problems = _parse_pool_eb_lines(
            logfile=logfile, lines=lines, tx_refs=tx_refs
        )
        problems.extend(pool_problems)

    test_ebs = {k: v for p in parsed.values() for k, v in p.test_ebs.items()}

    certified: list[_CertifiedEb] = []
    for logfile, pool_lines in parsed.items():
        for cert_slot, eb_slot, eb_hash in pool_lines.certs:
            in_eb = test_ebs.get((eb_slot, eb_hash))
            adopted = pool_lines.adopted.get(cert_slot)
            if not (in_eb and adopted):
                continue
            rb_hash, adopted_at = adopted
            if not any(rb_hash in p.tips for f, p in parsed.items() if f != logfile):
                continue
            certified.append(
                _CertifiedEb(
                    eb_slot=eb_slot,
                    eb_hash=eb_hash,
                    cert_slot=cert_slot,
                    cert_rb_hash=rb_hash,
                    cert_rb_adopted_at=adopted_at,
                    tx_hashes=in_eb,
                )
            )

    return certified, problems


def _collect_lines(
    *,
    searches: dict[pl.Path, leios.LogSearch],
    regex: str,
    log_errors: dict[pl.Path, str],
    lines_per_log: dict[pl.Path, list[str]],
) -> None:
    """Add the lines matching a regex that were appended since the previous search, in place.

    Args:
        searches: The search state per log file.
        regex: The regex to search for.
        log_errors: The log files that could not be searched.
        lines_per_log: The lines found so far in each log file.
    """
    for logfile, lines in leios.search_lines_round(
        searches=searches, regex=regex, log_errors=log_errors
    ).items():
        lines_per_log[logfile].extend(lines)


def _get_big_metadata_file(*, name_template: str) -> pl.Path:
    """Write a tx metadata file that makes a tx almost as big as the max tx size.

    A metadata string can have at most 64 bytes, so the metadata is a list of them. 200
    strings of 64 bytes are ~13.3 kB of CBOR, which leaves room for the rest of a simple
    payment tx under the 16 kB max tx size.
    """
    metadata = {"674": {"msg": [f"{i:03d}{'x' * 61}" for i in range(200)]}}
    out_file = pl.Path(f"{name_template}_big_metadata.json")
    helpers.write_json(out_file=out_file, content=metadata)
    return out_file


class TestLeiosEbTxs:
    """Tests for the txs that get into the ledger through a certified EB."""

    # Number of txs submitted at once. With ~14 kB per tx, they need ~5 RBs of the 90 kB
    # max block body size on `leios_fast`. Each RB that cannot take the rest of them has
    # its block producer forge an EB with the rest, so there are several EBs for the
    # txs to be certified in - an EB is certified only when the next RB comes late
    # enough after the RB that announced it, which happens for about half of the EBs.
    NUM_TXS = 30
    # Max number of times to submit a new batch of txs, when none of the txs of the
    # previous batch got into a certified EB
    MAX_ROUNDS = 3
    # Max number of new blocks to wait for in one round
    ROUND_MAX_BLOCKS = 20
    # Amount of each UTxO a test tx spends
    TX_IN_AMOUNT = 5_000_000

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_txs_in_certified_eb(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_leios: clusterlib.ClusterLib,
    ):
        """Check that txs that don't fit into an RB reach the ledger through a certified EB.

        The oracle for "the tx came through an EB" are the node logs. An EB lists its txs by
        the Blake2b-256 hash of the serialized tx, which the test computes from the signed
        tx file (`leios.get_eb_tx_ref`). The `EB forged` message of the block producer
        lists the hashes, the `EB announced` message of the same slot gives the EB its
        hash, and the `EB certified` message of a later block producer names the
        certified EB. A certifying RB carries no txs of its own, and the ledger applies the
        txs of the certified EB in its place. The RB is checked to be adopted by its block
        producer and to become the tip of the chain of another pool, so that it is not an
        RB that lost a slot battle.

        Rejected oracles: the node-to-client chain sync that would serve the block
        contents has no client in the framework and no `cardano-cli` query; db-sync is
        not part of the Leios regression setup; and the statistical argument that more txs
        reached the ledger than the RBs can hold is weaker than following particular txs.

        The txs of an EB that is not certified stay in the mempool and get into a later RB
        or EB, so the test submits another batch when none of the txs of a batch was
        certified in an EB.

        * Wait for the epoch in which the voting committee becomes active, unless it is
          seated from the genesis
        * Create `NUM_TXS` UTxOs and build a tx with ~13 kB of metadata spending each one,
          so that the txs together are several times the max block body size
        * Submit all the txs at once
        * Wait until all the txs are in the ledger
        * Search the pool logs for an EB holding a test tx, that a pool certified in an RB
          that it adopted and that another pool took onto its chain
        * Repeat with a new batch of txs when no such EB was found
        * Check that the outputs of the txs in the certified EB are in the UTxO set, also
          after `securityParam` more blocks (at most `MAX_SETTLE_BLOCKS`)
        * Check that no node switched to a fork since the certifying RB was adopted, so that
          the RB is still on every chain - skip as inconclusive when one did, as the log
          doesn't say where the chains split
        """
        cluster = cluster_leios
        temp_template = common.get_test_id(cluster)

        if not leios.is_committee_seated_in_genesis(cluster_obj=cluster):
            common.skip_on_long_epochs(cluster_obj=cluster, epochs=leios.VOTING_START_EPOCH)
            cluster.wait_for_epoch(epoch_no=leios.VOTING_START_EPOCH, padding_seconds=5)

        state_dir = cluster_nodes.get_cluster_env().state_dir
        pool_logs = sorted(state_dir.glob("pool*.stdout"))
        assert pool_logs, f"No pool log files found in '{state_dir}'"
        # The UTxO queries go to the node of `bft1`, so its chain matters for the fork
        # switch check too
        node_logs = [*pool_logs, state_dir / "bft1.stdout"]

        payment_rec = addrs_common.get_payment_addr(
            name_template=temp_template,
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            amount=(self.NUM_TXS * self.TX_IN_AMOUNT + 10_000_000) * self.MAX_ROUNDS,
        )
        dst_rec = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_dst", cluster_obj=cluster
        )[0]
        metadata_file = _get_big_metadata_file(name_template=temp_template)

        # A fixed fee that covers a tx of the max size, so that the fee doesn't have to be
        # calculated for each of the txs
        pparams = cluster.g_query.get_protocol_params()
        fee = pparams["txFeePerByte"] * pparams["maxTxSize"] + pparams["txFeeFixed"]

        lines_regex = "|".join(
            f"(?:{r})"
            for r in (
                leios.EB_FORGED_RE,
                leios.EB_ANNOUNCED_RE,
                leios.EB_CERTIFIED_RE,
                leios.BLOCK_ADOPTED_RE,
                leios.NEW_TIP_RE,
            )
        )

        certified: list[_CertifiedEb] = []
        txids: dict[str, str] = {}  # EB tx hash -> tx id
        # A test tx listed in an EB with a size other than the size of the signed tx - the
        # EB tx hash would then not be the hash of the tx the test submitted
        size_problems: list[str] = []
        # Log files that could not be searched. They explain a missing certified EB, so they
        # are reported only together with one.
        search_problems: list[str] = []
        for round_no in range(1, self.MAX_ROUNDS + 1):
            round_template = f"{temp_template}_r{round_no}"

            # Create the UTxOs for the txs to spend
            fanout_output = cluster.g_transaction.send_tx(
                src_address=payment_rec.address,
                tx_name=f"{round_template}_fanout",
                txouts=[clusterlib.TxOut(address=payment_rec.address, amount=self.TX_IN_AMOUNT)]
                * self.NUM_TXS,
                tx_files=clusterlib.TxFiles(signing_key_files=[payment_rec.skey_file]),
                # Keep the outputs to the same address apart, one for each tx
                join_txouts=False,
            )
            # The change output can have the same amount by chance, it doesn't matter
            # which of the outputs are used
            fanout_utxos = [
                u
                for u in cluster.g_query.get_utxo(tx_raw_output=fanout_output)
                if u.address == payment_rec.address and u.amount == self.TX_IN_AMOUNT
            ][: self.NUM_TXS]
            assert len(fanout_utxos) == self.NUM_TXS, (
                f"Expected {self.NUM_TXS} UTxOs from the fan-out tx, got {len(fanout_utxos)}"
            )

            # Build all the txs before submitting any of them, so that they are submitted
            # together and pile up in the mempool
            tx_refs: dict[str, int] = {}
            tx_files: list[pl.Path] = []
            for i, utxo in enumerate(fanout_utxos):
                tx_name = f"{round_template}_tx{i}"
                tx_raw = cluster.g_transaction.build_raw_tx(
                    src_address=payment_rec.address,
                    tx_name=tx_name,
                    txins=[utxo],
                    txouts=[clusterlib.TxOut(address=dst_rec.address, amount=utxo.amount - fee)],
                    tx_files=clusterlib.TxFiles(metadata_json_files=[metadata_file]),
                    fee=fee,
                )
                tx_file = cluster.g_transaction.sign_tx(
                    tx_body_file=tx_raw.out_file,
                    signing_key_files=[payment_rec.skey_file],
                    tx_name=tx_name,
                )
                tx_hash, tx_size = leios.get_eb_tx_ref(tx_file=tx_file)
                tx_refs[tx_hash] = tx_size
                txids[tx_hash] = cluster.g_transaction.get_txid(tx_file=tx_file)
                tx_files.append(tx_file)

            searches = leios.init_searches(node_logs)
            log_errors: dict[pl.Path, str] = {}
            lines_per_log: dict[pl.Path, list[str]] = {p: [] for p in node_logs}

            for tx_file in tx_files:
                cluster.g_transaction.submit_tx_bare(tx_file=tx_file)

            round_txins = [f"{txids[h]}#0" for h in tx_refs]
            for __ in range(self.ROUND_MAX_BLOCKS // WAIT_BLOCKS_STEP):
                cluster.wait_for_new_block(new_blocks=WAIT_BLOCKS_STEP)
                _collect_lines(
                    searches=searches,
                    regex=lines_regex,
                    log_errors=log_errors,
                    lines_per_log=lines_per_log,
                )

                if len(cluster.g_query.get_utxo(txin=round_txins)) == len(round_txins):
                    break
            else:
                pytest.fail(
                    f"Not all of the {self.NUM_TXS} txs of round {round_no} got into the "
                    f"ledger within {self.ROUND_MAX_BLOCKS} blocks."
                )

            # The lines of the RB that brought the last txs can be logged after the search
            # above - by the pool that forged it, and later still by the pools that took it
            # onto their chain. One more block gives them time to show up.
            cluster.wait_for_new_block(new_blocks=1)
            _collect_lines(
                searches=searches,
                regex=lines_regex,
                log_errors=log_errors,
                lines_per_log=lines_per_log,
            )

            certified, round_problems = _find_certified_ebs(
                lines_per_pool={p: lines_per_log[p] for p in pool_logs}, tx_refs=tx_refs
            )
            size_problems.extend(round_problems)
            search_problems.extend(log_errors.values())
            if certified:
                break
            LOGGER.info(f"No tx of round {round_no} got into a certified EB, trying again.")

        assert certified, "\n".join(
            [
                f"None of the test txs got into a certified EB in {self.MAX_ROUNDS} rounds.",
                *size_problems,
                *search_problems,
            ]
        )
        LOGGER.info(f"Test txs in certified EBs: {certified}")

        # The size of a tx in the EB is the size of the signed tx, so the EB tx hash
        # is the hash of the very tx the test submitted
        assert not size_problems, "\n".join(size_problems)

        def _check_utxos() -> None:
            eb_txins = [f"{txids[h]}#0" for eb in certified for h in eb.tx_hashes]
            eb_utxos = cluster.g_query.get_utxo(txin=eb_txins)
            found_txins = {f"{u.utxo_hash}#{u.utxo_ix}" for u in eb_utxos}
            missing = sorted(set(eb_txins) - found_txins)
            assert not missing, f"Outputs of txs from certified EBs not in the UTxO: {missing}"
            wrong = [
                u
                for u in eb_utxos
                if u.address != dst_rec.address or u.amount != self.TX_IN_AMOUNT - fee
            ]
            assert not wrong, f"Unexpected outputs of txs from certified EBs: {wrong}"

        _check_utxos()

        # The certifying RB could still be rolled back until it is `securityParam` blocks
        # deep. The txs would then come back from the mempool in a later RB with the same
        # outputs, so the UTxO set alone cannot tell, but a node drops a block from its
        # chain only by switching to a fork. The wait is capped, so with a big
        # `securityParam` the RB is only unlikely to be rolled back, not immutable.
        cluster.wait_for_new_block(
            new_blocks=min(cluster.genesis["securityParam"] + 1, MAX_SETTLE_BLOCKS)
        )
        _check_utxos()
        _collect_lines(
            searches=searches,
            regex=lines_regex,
            log_errors=log_errors,
            lines_per_log=lines_per_log,
        )
        # A fork switch can be relevant to more than one of the EBs, list it once
        fork_switches = list(
            dict.fromkeys(
                s
                for eb in certified
                for s in _get_fork_switches_after(lines_per_log=lines_per_log, eb=eb)
            )
        )
        # A log that could not be searched may hide a fork switch
        if fork_switches or log_errors:
            pytest.skip(
                "; ".join(
                    [
                        (
                            "Cannot tell whether the certifying RB is still on every chain - "
                            "a node switched to a fork after the RB was adopted, and the log "
                            "doesn't say where the chains split, or a log could not be "
                            "searched - so the EB path of the txs is inconclusive"
                        ),
                        *fork_switches,
                        *log_errors.values(),
                    ]
                )
            )


class TestLeiosQuorum:
    """Tests for a Leios committee that loses and regains its quorum."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.xdist_split(markers.XdSplits.heavy)
    @pytest.mark.long
    def test_quorum_loss_and_recovery(
        self,
        cluster_leios_lock: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
    ):
        """Check that the chain stays live without a quorum, and certifies again after.

        * Wait for the epoch in which the voting committee becomes active
        * Check that stopping one pool takes the committee below the quorum - the seats
          of the remaining pools weigh less than `leiosQuorumStakeThreshold`
        * Search the pool logs until a certificate is assembled, so that the cluster is
          known to certify EBs before the stop
        * Stop the pool node, and wait for the votes and certificates of the EBs that
          were announced before the stop to settle
        * Record the current end of the logs of the running pools
        * Submit payment Txs and check that their outputs are in the UTxO
        * Search the logs of the running pools for the rest of the window
        * Check that the running pools assembled no certificate and no block certified
          an EB, while they did see EBs to vote on - skip as inconclusive when they saw
          none, or when a log could not be searched
        * Check that the running pools kept forging blocks
        * Start the pool node again
        * Check that a certificate is assembled again within a bounded time
        """
        cluster = cluster_leios_lock
        # Waits for up to `leios.VOTING_START_EPOCH` epochs, and then for ~25 min of log
        # searches and block waits, which is up to 2 epochs on `leios_fast`
        common.skip_on_long_epochs(cluster_obj=cluster, epochs=leios.VOTING_START_EPOCH + 2)
        temp_template = common.get_test_id(cluster)

        state_dir = cluster_nodes.get_cluster_env().state_dir
        stopped_node = QUORUM_STOPPED_POOL.replace("node-", "")
        pool_logs = {
            pool_name: state_dir / f"{pool_name.replace('node-', '')}.stdout"
            for pool_name in cluster_management.Resources.ALL_POOLS
        }
        missing_logs = sorted(str(f) for f in pool_logs.values() if not f.exists())
        assert not missing_logs, f"Pool log files not found: {', '.join(missing_logs)}"
        running_logs = [f for n, f in pool_logs.items() if n != QUORUM_STOPPED_POOL]

        # A pool registered by a transaction is on no committee until this epoch. The
        # wait is a no-op when the pools come with their BLS key straight from the
        # genesis, which is the setup this test normally runs on.
        if not leios.is_committee_seated_in_genesis(cluster_obj=cluster):
            cluster.wait_for_epoch(epoch_no=leios.VOTING_START_EPOCH, padding_seconds=5)

        # The quorum is read from the current protocol parameters, and the seat weights
        # from the committee of the current epoch. Neither changes at an epoch boundary
        # unless the pools or their stake change, which the cluster lock rules out.
        quorum = fractions.Fraction(
            cluster.g_query.get_protocol_params()["leiosQuorumStakeThreshold"]
        ).limit_denominator()
        stopped_pool_id = helpers.get_pool_id_hex(
            delegation.get_pool_id(
                cluster_obj=cluster,
                addrs_data=cluster_manager.cache.addrs_data,
                pool_name=QUORUM_STOPPED_POOL,
            )
        )
        left_share, committee_epoch = _get_voting_share_without(
            cluster_obj=cluster, pool_id=stopped_pool_id
        )
        if left_share >= quorum:
            pytest.skip(
                f"Without '{QUORUM_STOPPED_POOL}', the Leios committee of epoch "
                f"{committee_epoch} still holds {float(left_share):.3f} of the stake, which "
                f"reaches the quorum {float(quorum):.3f}, so stopping it cannot lose the quorum"
            )

        # Make sure the cluster does certify EBs, otherwise the absence of certificates
        # while the pool is stopped would say nothing
        baseline_searches = leios.init_searches(pool_logs.values())
        baseline_problems = leios.search_logs_until(
            searches=baseline_searches,
            regexes=[leios.MSG_CERTIFIED],
            deadline=time.monotonic() + leios.MAX_SEARCH_SEC,
            stop_on=[leios.MSG_CERTIFIED],
        )
        if not any(leios.MSG_CERTIFIED in s.found for s in baseline_searches.values()):
            pytest.skip(
                "; ".join(
                    [
                        (
                            "No pool assembled a Leios certificate before the pool node was "
                            "stopped, so the absence of certificates without the quorum "
                            "would be inconclusive"
                        ),
                        *baseline_problems,
                    ]
                )
            )

        payment_addrs = addrs_common.get_payment_addrs(
            name_template=temp_template,
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )

        # Stopping a node drops the connections the other nodes have to it
        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="MuxBearerClosed",
            ignore_file_id=cluster_manager.worker_id,
        )

        node_stopped = False
        with cluster_manager.respin_on_failure():
            try:
                cluster_nodes.stop_nodes([stopped_node])
                node_stopped = True

                # A certificate for an EB announced before the stop can still be
                # assembled from votes cast before the stop, and it can be included in
                # the very next block, wherever that one falls. Only the EBs announced
                # after that block depend on the votes of the running pools alone.
                cluster.wait_for_new_block(new_blocks=2)
                time.sleep(QUORUM_VOTE_SETTLE_SEC)

                outage_searches = leios.init_searches(running_logs)
                outage_deadline = time.monotonic() + leios.MAX_SEARCH_SEC
                outage_start_block = cluster.g_query.get_block_no()

                # No EB can be certified without the quorum, so the Txs can make it to
                # the chain only in a ranking block
                dst_address = payment_addrs[1].address
                tx_outputs = [
                    cluster.g_transaction.send_tx(
                        src_address=payment_addrs[0].address,
                        tx_name=f"{temp_template}_outage_{i}",
                        txouts=[clusterlib.TxOut(address=dst_address, amount=2_000_000)],
                        tx_files=clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file]),
                    )
                    for i in range(QUORUM_OUTAGE_TXS)
                ]
                missing_txs = [
                    cluster.g_transaction.get_txid(tx_body_file=t.out_file)
                    for t in tx_outputs
                    if not clusterlib.filter_utxos(
                        utxos=cluster.g_query.get_utxo(tx_raw_output=t), address=dst_address
                    )
                ]

                outage_problems = leios.search_logs_until(
                    searches=outage_searches,
                    regexes=[
                        leios.MSG_CERTIFIED,
                        leios.MSG_BLOCK_CERTIFIED,
                        leios.MSG_VOTED,
                        leios.MSG_ANNOUNCEMENT_ACCEPTED,
                    ],
                    deadline=outage_deadline,
                )
                outage_blocks = cluster.g_query.get_block_no() - outage_start_block
                outage_found = {p: s.found for p, s in outage_searches.items()}

                errors = _get_outage_errors(
                    found_per_pool=outage_found,
                    missing_txs=missing_txs,
                    outage_blocks=outage_blocks,
                    left_share=left_share,
                    quorum=quorum,
                )
                if errors:
                    errors.extend(outage_problems)
                assert not errors, "\n".join(errors)

                # Without an EB to vote on there is nothing to certify, and a log that
                # could not be searched may hide a certificate. Either way the absence of
                # certificates says nothing.
                eb_seen = any(
                    found & {leios.MSG_VOTED, leios.MSG_ANNOUNCEMENT_ACCEPTED}
                    for found in outage_found.values()
                )
                if not eb_seen or outage_problems:
                    # A skip is not an `Exception`, so `respin_on_failure` doesn't see it.
                    # The pool node gets started again, but nothing shows the committee
                    # certifies again.
                    cluster_manager.set_needs_respin()
                    pytest.skip(
                        "; ".join(
                            [
                                (
                                    "No running pool voted on or accepted an EB announcement "
                                    "while the quorum was lost, or a log could not be "
                                    "searched, so the absence of certificates is inconclusive"
                                ),
                                *outage_problems,
                            ]
                        )
                    )

                # Search from right before the start, so that a certificate assembled
                # as soon as the node is back is not missed
                recovery_searches = leios.init_searches(pool_logs.values())
                cluster_nodes.start_nodes([stopped_node])
                node_stopped = False

                recovery_problems = leios.search_logs_until(
                    searches=recovery_searches,
                    regexes=[
                        leios.MSG_CERTIFIED,
                        leios.MSG_VOTED,
                        leios.MSG_ANNOUNCEMENT_ACCEPTED,
                    ],
                    deadline=time.monotonic() + QUORUM_RECOVERY_SEARCH_SEC,
                    stop_on=[leios.MSG_CERTIFIED],
                )
            finally:
                # Never hand the instance over with a stopped pool node
                if node_stopped:
                    cluster_nodes.start_nodes([stopped_node])

            # Still within the respin context - a committee that doesn't certify again
            # leaves the instance in a state the next test cannot rely on. A skip is not
            # an `Exception`, so it has to ask for the respin itself.
            recovery_found = {m for s in recovery_searches.values() for m in s.found}
            if leios.MSG_CERTIFIED in recovery_found:
                return

            if recovery_found & {leios.MSG_VOTED, leios.MSG_ANNOUNCEMENT_ACCEPTED}:
                error = (
                    "No pool assembled a Leios certificate within "
                    f"{QUORUM_RECOVERY_SEARCH_SEC} sec after the pool '{QUORUM_STOPPED_POOL}' "
                    "was started again, although the pools saw EBs to vote on."
                )
                raise AssertionError("\n".join([error, *recovery_problems]))

            cluster_manager.set_needs_respin()
            pytest.skip(
                "; ".join(
                    [
                        (
                            "No pool voted on or accepted an EB announcement after the pool "
                            f"'{QUORUM_STOPPED_POOL}' was started again, so the absence of "
                            "certificates is inconclusive"
                        ),
                        *recovery_problems,
                    ]
                )
            )
