"""Tests for Leios endorser blocks (EBs).

The tests check that the node reports the expected Leios activity in its logs. The
Leios trace messages are emitted only in the Dijkstra era, and only when there is
enough Tx load to fill the mempool, so that the block producer has something to put
into an endorser block.
"""

import logging
import pathlib as pl
import time
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import leios
from cardano_node_tests.tests import markers
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers

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


@pytest.fixture
def cluster_leios(cluster: clusterlib.ClusterLib) -> clusterlib.ClusterLib:
    """Return a cluster instance that is able to produce Leios endorser blocks."""
    leios.skip_if_no_ebs(cluster_obj=cluster)
    return cluster


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

        if leios.is_committee_seated_in_genesis(genesis=cluster.genesis):
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
