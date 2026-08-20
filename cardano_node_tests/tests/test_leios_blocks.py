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
    r"Consensus\.LeiosKernel\.VoteAcquired\].*Leios vote acquired",
    r"Consensus\.LeiosKernel\.Voted\].*Leios voted, weight=",
    r"Consensus\.LeiosKernel\.Certified\].*Leios cert assembled for RB",
)

# EB messages reported only by the pool that won the EB election, so they are expected
# in the log of at least one pool, not in the log of every pool.
EB_MSGS_ANY_POOL = (
    r"Consensus\.LeiosKernel\.BlockForged\].*EB forged at slot",
    r"Consensus\.LeiosKernel\.BlockAnnounced\].*EB announced:",
    r"Consensus\.LeiosKernel\.BlockStored\].*EB stored at slot",
    r"Consensus\.LeiosKernel\.BlockCertified\].*EB certified at slot",
    r"Consensus\.LeiosKernel\.CertifiedAndAnnounced\].*RB certified an EB and announced a new one",
)

_EB_MSGS_COMPILED = {r: re.compile(r) for r in EB_MSGS_ALL_POOLS + EB_MSGS_ANY_POOL}
_ALL_POOLS_MSGS_SET = frozenset(EB_MSGS_ALL_POOLS)
_ANY_POOL_MSGS_SET = frozenset(EB_MSGS_ANY_POOL)

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
    searches: dict[pl.Path, _LogSearch] = {}
    for logfile in pool_logs:
        seek_offset, inode, timestamp = _get_log_position(logfile)
        searches[logfile] = _LogSearch(seek_offset=seek_offset, inode=inode, timestamp=timestamp)

    stall_error = ""
    # Only the last outcome per log file, so that repeated rotations don't pile up
    # near-duplicate reports - the error names the rotated file the search died on
    log_errors: dict[pl.Path, str] = {}

    for __ in range(MAX_WAIT_BLOCKS // WAIT_BLOCKS_STEP):
        try:
            cluster_obj.wait_for_new_block(new_blocks=WAIT_BLOCKS_STEP)
        except clusterlib.CLIError as err:
            # The chain stalled. Search the logs one last time, so that the test can
            # report what was still missing and not just the stall itself.
            stall_error = f"The chain stalled while waiting for new blocks: {err}"

        found_any_pool = {m for s in searches.values() for m in s.found}
        for logfile, search in searches.items():
            missing = (_ALL_POOLS_MSGS_SET - search.found) | (_ANY_POOL_MSGS_SET - found_any_pool)
            if not missing:
                continue

            try:
                # Record the new search position before the search, so that lines
                # appended while the search is running are not skipped in the next round
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
            found_any_pool |= search.found

        found_per_pool = {p: s.found for p, s in searches.items()}
        if stall_error or not _get_missing_msgs_errors(found_per_pool=found_per_pool):
            break

    problems = list(log_errors.values())
    if stall_error:
        problems.append(stall_error)

    return {p: s.found for p, s in searches.items()}, problems


class TestLeios:
    """Tests for Leios endorser blocks."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_eb_logs(
        self,
        cluster_leios: clusterlib.ClusterLib,
    ):
        """Check that the nodes report the expected endorser block activity.

        * Record the current end of each pool log file
        * Wait for new blocks to be created
        * Check that each pool reports EB announcements, votes and certificates
        * Check that at least one pool reports forging, announcing, storing and
          certifying an EB
        """
        cluster = cluster_leios
        common.get_test_id(cluster)

        state_dir = cluster_nodes.get_cluster_env().state_dir
        pool_logs = sorted(state_dir.glob("pool*.stdout"))
        assert pool_logs, f"No pool log files found in '{state_dir}'"

        found_per_pool, search_problems = _collect_eb_msgs(cluster_obj=cluster, pool_logs=pool_logs)

        errors = _get_missing_msgs_errors(found_per_pool=found_per_pool)
        if errors:
            errors.extend(search_problems)

        assert not errors, "\n".join(errors)
