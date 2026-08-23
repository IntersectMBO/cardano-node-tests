"""Tests for Leios endorser blocks (EBs).

The tests check that the node reports the expected Leios activity in its logs. The
Leios trace messages are emitted only in the Dijkstra era, and only when there is
enough Tx load to fill the mempool, so that the block producer has something to put
into an endorser block.
"""

import dataclasses
import logging
import pathlib as pl
import re
import time
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

_SKIP_REASON = ""
if VERSIONS.cluster_era < VERSIONS.DIJKSTRA_FIRST:
    _SKIP_REASON = "Leios endorser blocks are available only in Dijkstra+ eras"
elif not (configuration.ENABLE_TX_FIREHOSE or configuration.ENABLE_TX_CENTRIFUGE):
    _SKIP_REASON = (
        "needs a Tx load generator: neither `ENABLE_TX_FIREHOSE` nor `ENABLE_TX_CENTRIFUGE` is set"
    )

pytestmark = [
    pytest.mark.leios,
    common.SKIPIF_ON_TESTNET,
    pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON),
]

# EB messages that a node can report only once the Leios voting committee is active.
# They belong to more than one of the groups below, so they are named here.
_MSG_VOTE_ACQUIRED = r"Consensus\.LeiosKernel\.VoteAcquired\].*Leios vote acquired"
_MSG_VOTED = r"Consensus\.LeiosKernel\.Voted\].*Leios voted, weight="
_MSG_CERTIFIED = r"Consensus\.LeiosKernel\.Certified\].*Leios cert assembled for RB"
_MSG_BLOCK_CERTIFIED = r"Consensus\.LeiosKernel\.BlockCertified\].*EB certified at slot"
_MSG_CERTIFIED_AND_ANNOUNCED = (
    r"Consensus\.LeiosKernel\.CertifiedAndAnnounced\].*RB certified an EB and announced a new one"
)

# EB messages that every block producing node takes part in. Every pool sees every EB
# announcement and every vote, and votes on and downloads the EBs forged by the other
# pools. The peer side messages need an EB forged by another pool, so they would be
# missing on a pool that forged every EB in the search window. A `MAX_WAIT_BLOCKS`
# window holds ~17 EBs at the 0.42 EB per RB measured on `leios_fast`, so with 3 pools
# of equal stake the chance of that is 3 * (1/3)^17 = 2.3e-8.
EB_MSGS_ALL_POOLS = (
    r"Consensus\.LeiosPeer\.Announcement\].*EB announcement from peer",
    r"Consensus\.LeiosKernel\.AnnouncementAccepted\].*EB announcement accepted from",
    r"Consensus\.LeiosKernel\.BlockAcquired\].*EB body acquired:",
    r"Consensus\.LeiosKernel\.BlockTxsAcquired\].*EB txs acquired:",
    _MSG_VOTE_ACQUIRED,
    _MSG_VOTED,
    _MSG_CERTIFIED,
)

# EB messages reported only by the pool that won the EB election, so they are expected
# in the log of at least one pool, not in the log of every pool.
EB_MSGS_ANY_POOL = (
    r"Consensus\.LeiosKernel\.BlockForged\].*EB forged at slot",
    r"Consensus\.LeiosKernel\.BlockAnnounced\].*EB announced:",
    r"Consensus\.LeiosKernel\.BlockStored\].*EB stored at slot",
    _MSG_BLOCK_CERTIFIED,
    _MSG_CERTIFIED_AND_ANNOUNCED,
)

# EB messages that prove the voting committee is active - a vote was cast, received or
# turned into a certificate.
VOTING_MSGS = (
    _MSG_VOTE_ACQUIRED,
    _MSG_VOTED,
    _MSG_CERTIFIED,
    _MSG_BLOCK_CERTIFIED,
    _MSG_CERTIFIED_AND_ANNOUNCED,
)

# Reported by a pool for an EB announcement it cannot vote on, because it is not a
# member of the voting committee for the EB.
NOT_ON_COMMITTEE_MSG = r"Consensus\.LeiosKernel\.NotVoted\].*Leios not voted for .*: NotOnCommittee"

_EB_MSGS_COMPILED = {
    r: re.compile(r) for r in (*EB_MSGS_ALL_POOLS, *EB_MSGS_ANY_POOL, NOT_ON_COMMITTEE_MSG)
}
_ALL_POOLS_MSGS_SET = frozenset(EB_MSGS_ALL_POOLS)
_ANY_POOL_MSGS_SET = frozenset(EB_MSGS_ANY_POOL)
_VOTING_MSGS_SET = frozenset(VOTING_MSGS)
_PRE_VOTING_MSGS_SET = frozenset((*VOTING_MSGS, NOT_ON_COMMITTEE_MSG))

# Max number of new blocks to wait for while the expected messages are showing up in
# the logs. Certification of an EB needs a quorum of votes and an RB that announces it,
# so it can take several blocks before all the expected messages are reported.
MAX_WAIT_BLOCKS = 40
# Number of new blocks to wait for between two searches of the logs
WAIT_BLOCKS_STEP = 5

# Min interval between two blocks for the cluster to be usable for the test. An EB is
# forged from exactly the mempool backlog that doesn't fit into a Praos block, so no
# backlog means no EB, no announcement, no votes and no certificate. The block
# production rate is what drains the mempool, so it must be slow enough for a backlog
# to build up. The `leios_fast` testnet variant produces a block every 20 seconds
# (`activeSlotsCoeff` 0.05, `slotLength` 1) and gets EBs, while the 10x faster block
# rate of the `local_fast` variant (and its 4x shorter epoch) drains the mempool.
MIN_BLOCK_INTERVAL_SEC = 10

# The first epoch in which a pool can be a member of the Leios voting committee. The
# committee is drawn from a stake distribution snapshot that is empty for the whole
# lifetime of a freshly started cluster instance until this epoch, so up to then every
# pool answers every EB announcement with `NotOnCommittee` and no EB can be voted on or
# certified.
VOTING_START_EPOCH = 3

# Number of seconds between two searches of the logs while waiting for the pools to
# report that they are not on the voting committee.
NO_VOTE_SEARCH_STEP_SEC = 30

# Max number of seconds to spend searching the logs for the pools reporting that they
# are not on the voting committee. EBs come in bursts, with gaps of up to ~250 sec
# between them on `leios_fast`, so 360 sec is one full gap plus slack - long enough for
# at least one burst to normally land in the window.
MAX_NO_VOTE_SEARCH_SEC = 360

# Number of seconds to keep between the end of the searched log window and the end of
# the epoch it runs in, so that a vote logged in the next epoch cannot land in the
# searched part of the log.
VOTING_EPOCH_MARGIN_SEC = 60


@pytest.fixture
def cluster_leios(cluster: clusterlib.ClusterLib) -> clusterlib.ClusterLib:
    """Return a cluster instance that is able to produce Leios endorser blocks.

    Skips the test when the cluster settings don't allow an EB to be forged. The
    settings are known only once a cluster instance is assigned to the test, so this
    cannot be a `skipif` marker.
    """
    block_interval = cluster.slot_length / float(cluster.genesis["activeSlotsCoeff"])
    if block_interval < MIN_BLOCK_INTERVAL_SEC:
        pytest.skip(
            f"Cannot observe EBs on the '{configuration.TESTNET_VARIANT}' testnet variant: "
            f"a block is produced every {block_interval} sec (epoch is "
            f"{cluster.epoch_length_sec} sec), which is too fast for a mempool backlog to "
            f"build up for an EB; needs at least {MIN_BLOCK_INTERVAL_SEC} sec per block"
        )

    return cluster


@dataclasses.dataclass
class _LogSearch:
    """Where to continue searching a log file, and what was already found in it."""

    seek_offset: int
    inode: int
    timestamp: float
    found: set[str] = dataclasses.field(default_factory=set)


def _get_log_position(logfile: pl.Path) -> tuple[int, int, float]:
    """Return the current end of the log file as (byte offset, inode, timestamp).

    A single `stat` call, so that the offset (file size) and the inode belong to the
    same file even when the file is rotated in between.
    """
    logfile_stat = logfile.stat()
    return logfile_stat.st_size, logfile_stat.st_ino, time.time()


def _find_eb_msgs(
    *,
    regexes: tp.Collection[str],
    logfile: pl.Path,
    seek_offset: int,
    inode: int,
    timestamp: float,
) -> set[str]:
    """Return the searched EB message regexes that are present in the log file.

    Args:
        regexes: The EB message regexes to search for.
        logfile: Path to the node log file.
        seek_offset: Byte offset to start the search at.
        inode: Inode of the log file the `seek_offset` was recorded for.
        timestamp: Time the search offset was recorded at.

    Returns:
        The subset of `regexes` that matched at least one log line.
    """
    # One pass over the log file for all the regexes
    lines = logfiles.find_msgs_in_logs(
        regex="|".join(f"(?:{r})" for r in regexes),
        logfile=logfile,
        seek_offset=seek_offset,
        timestamp=timestamp,
        inode=inode,
    )

    found: set[str] = set()
    for line in lines:
        for regex in regexes:
            if regex not in found and _EB_MSGS_COMPILED[regex].search(line):
                found.add(regex)
        if len(found) == len(regexes):
            break

    return found


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
        for r in EB_MSGS_ALL_POOLS
        if r not in found
    ]

    found_any_pool = {m for found in found_per_pool.values() for m in found}
    errors.extend(
        f"No line matching `{r}` found in any of the pool logs."
        for r in EB_MSGS_ANY_POOL
        if r not in found_any_pool
    )

    return errors


def _init_searches(pool_logs: list[pl.Path]) -> dict[pl.Path, _LogSearch]:
    """Return the search state for each pool log, starting at its current end."""
    searches: dict[pl.Path, _LogSearch] = {}
    for logfile in pool_logs:
        seek_offset, inode, timestamp = _get_log_position(logfile)
        searches[logfile] = _LogSearch(seek_offset=seek_offset, inode=inode, timestamp=timestamp)
    return searches


def _search_round(
    *,
    searches: dict[pl.Path, _LogSearch],
    missing_msgs: tp.Callable[[_LogSearch], tp.Collection[str]],
    log_errors: dict[pl.Path, str],
) -> None:
    """Search the part of each pool log that was appended since the previous round.

    Updates `searches` in place with what was found and where to continue, and
    `log_errors` with the log files that could not be searched.

    Args:
        searches: The search state per pool log.
        missing_msgs: Returns the messages a given search state is still missing, so the
            caller decides what the round looks for.
        log_errors: Only the last outcome per log file, so that repeated rotations don't
            pile up near-duplicate reports - the error names the rotated file the search
            died on. An entry is removed once a later round recovered from the failure.
    """
    for logfile, search in searches.items():
        missing = missing_msgs(search)
        if not missing:
            continue

        try:
            # Record the new search position before the search, so that lines appended
            # while the search is running are not skipped in the next round
            next_position = _get_log_position(logfile)
            search.found |= _find_eb_msgs(
                regexes=missing,
                logfile=logfile,
                seek_offset=search.seek_offset,
                inode=search.inode,
                timestamp=search.timestamp,
            )
        except FileNotFoundError as err:
            # The log file kept getting rotated during the search. Keep the search
            # position, so that the same part of the log is searched again.
            msg = f"Cannot search '{logfile}': {err}"
            LOGGER.warning("%s", msg)
            log_errors[logfile] = msg
            continue

        # The search position was kept on failure, so a failure that a later round
        # recovered from didn't cost any log content
        log_errors.pop(logfile, None)
        search.seek_offset, search.inode, search.timestamp = next_position


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
    searches = _init_searches(pool_logs)
    stall_error = ""
    log_errors: dict[pl.Path, str] = {}

    def _missing_msgs(search: _LogSearch) -> tp.Collection[str]:
        found_any_pool = {m for s in searches.values() for m in s.found}
        return (_ALL_POOLS_MSGS_SET - search.found) | (_ANY_POOL_MSGS_SET - found_any_pool)

    for __ in range(MAX_WAIT_BLOCKS // WAIT_BLOCKS_STEP):
        try:
            cluster_obj.wait_for_new_block(new_blocks=WAIT_BLOCKS_STEP)
        except clusterlib.CLIError as err:
            # The chain stalled. Search the logs one last time, so that the test can
            # report what was still missing and not just the stall itself.
            stall_error = f"The chain stalled while waiting for new blocks: {err}"

        _search_round(searches=searches, missing_msgs=_missing_msgs, log_errors=log_errors)

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
    searches = _init_searches(pool_logs)
    log_errors: dict[pl.Path, str] = {}

    while True:
        time.sleep(min(NO_VOTE_SEARCH_STEP_SEC, max(0.0, deadline - time.monotonic())))

        _search_round(
            searches=searches,
            missing_msgs=lambda search: _PRE_VOTING_MSGS_SET - search.found,
            log_errors=log_errors,
        )

        # Voting activity is what the caller reports, no need to keep searching for it
        if any(s.found & _VOTING_MSGS_SET for s in searches.values()):
            break
        # Every pool reported that it is not a member of the voting committee
        if all(NOT_ON_COMMITTEE_MSG in s.found for s in searches.values()):
            break
        if time.monotonic() >= deadline:
            break

    return {p: s.found for p, s in searches.items()}, list(log_errors.values())


class TestLeios:
    """Tests for Leios endorser blocks."""

    @allure.link(helpers.get_vcs_link())
    # Scheduled at the end of the testrun, so that the cluster instance is already past
    # `VOTING_START_EPOCH` and the wait for it is a no-op
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
        cluster.wait_for_epoch(epoch_no=VOTING_START_EPOCH, padding_seconds=5)

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
    # before `VOTING_START_EPOCH`
    @pytest.mark.order(5)
    @pytest.mark.long
    def test_no_voting_before_committee_epoch(
        self,
        cluster_leios: clusterlib.ClusterLib,
    ):
        """Check that no EB is voted on before the voting committee becomes active.

        * Skip when no epoch before `VOTING_START_EPOCH` has room left for the whole
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

        state_dir = cluster_nodes.get_cluster_env().state_dir
        pool_logs = sorted(state_dir.glob("pool*.stdout"))
        assert pool_logs, f"No pool log files found in '{state_dir}'"

        # The searched log window must not cross an epoch boundary, otherwise a vote from
        # `VOTING_START_EPOCH` could land in it. It fits into an epoch only when it starts
        # at least `epoch_tail_sec` before the end of that epoch.
        epoch_tail_sec = MAX_NO_VOTE_SEARCH_SEC + VOTING_EPOCH_MARGIN_SEC
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
        last_usable_epoch = VOTING_START_EPOCH - 1
        if init_epoch > last_usable_epoch or (
            init_epoch == last_usable_epoch
            and cluster.time_from_epoch_start(tip=tip) > cluster.epoch_length_sec - epoch_tail_sec
        ):
            pytest.skip(
                f"The cluster instance is in epoch {init_epoch} and no epoch before "
                f"{VOTING_START_EPOCH}, in which the Leios voting committee becomes active, "
                f"has {epoch_tail_sec} sec left for the search window"
            )

        clusterlib_utils.wait_for_epoch_interval(cluster_obj=cluster, start=0, stop=-epoch_tail_sec)

        # The wait can cross into the next epoch, so the epoch the search runs in is not
        # necessarily the one seen above
        search_epoch = cluster.g_query.get_epoch()
        if search_epoch >= VOTING_START_EPOCH:
            pytest.skip(
                f"The cluster instance is already in epoch {search_epoch}, the Leios voting "
                f"committee is active since epoch {VOTING_START_EPOCH}"
            )

        found_per_pool, search_problems = _collect_pre_voting_msgs(
            pool_logs=pool_logs, deadline=time.monotonic() + MAX_NO_VOTE_SEARCH_SEC
        )

        # A vote is legitimate from `VOTING_START_EPOCH` on, so the result means nothing
        # if the searched window reached that epoch after all
        end_epoch = cluster.g_query.get_epoch()
        if end_epoch >= VOTING_START_EPOCH:
            pytest.skip(
                f"The search started in epoch {search_epoch} and ended in epoch {end_epoch}, "
                "in which the Leios voting committee is active, so the result is inconclusive"
            )

        errors = [
            f"Found a line matching `{r}` in '{logfile}' in epoch {search_epoch}."
            for logfile, found in found_per_pool.items()
            for r in VOTING_MSGS
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
            if NOT_ON_COMMITTEE_MSG not in found
        )
        if no_evidence:
            pytest.skip(
                "; ".join(
                    [
                        "No pool declined to vote with `NotOnCommittee` in "
                        f"{', '.join(no_evidence)}, so the absence of votes in epoch "
                        f"{search_epoch} is inconclusive",
                        *search_problems,
                    ]
                )
            )
