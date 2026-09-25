"""Leios trace messages and log search helpers shared by the Leios tests.

The Leios activity of a node is visible only in its trace messages, and the messages are
emitted only in the Dijkstra era and only when there is enough Tx load for a block
producer to have something to put into an endorser block (EB). Tests therefore search the
node log files for the messages they expect, over the log content that got appended since
they started looking.
"""

import dataclasses
import logging
import pathlib as pl
import re
import time
import typing as tp

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

# Why the Leios tests cannot run, empty when they can. The Tx load generator is needed
# because an EB is forged from the mempool backlog that doesn't fit into a Praos block.
SKIP_REASON = ""
if VERSIONS.cluster_era < VERSIONS.DIJKSTRA_FIRST:
    SKIP_REASON = "Leios endorser blocks are available only in Dijkstra+ eras"
elif not (configuration.ENABLE_TX_FIREHOSE or configuration.ENABLE_TX_CENTRIFUGE):
    SKIP_REASON = (
        "needs a Tx load generator: neither `ENABLE_TX_FIREHOSE` nor `ENABLE_TX_CENTRIFUGE` is set"
    )

# EB messages that a node can report only once the Leios voting committee is active.
# They belong to more than one of the groups below, so they are named here.
MSG_ANNOUNCEMENT_ACCEPTED = (
    r"Consensus\.LeiosKernel\.AnnouncementAccepted\].*EB announcement accepted from"
)
MSG_VOTE_ACQUIRED = r"Consensus\.LeiosKernel\.VoteAcquired\].*Leios vote acquired"
MSG_VOTED = r"Consensus\.LeiosKernel\.Voted\].*Leios voted, weight="
MSG_CERTIFIED = r"Consensus\.LeiosKernel\.Certified\].*Leios cert assembled for RB"
MSG_BLOCK_CERTIFIED = r"Consensus\.LeiosKernel\.BlockCertified\].*EB certified at slot"
MSG_CERTIFIED_AND_ANNOUNCED = (
    r"Consensus\.LeiosKernel\.CertifiedAndAnnounced\].*RB certified an EB and announced a new one"
)

# EB messages that every block producing node takes part in. Every pool sees every EB
# announcement and every vote, and votes on and downloads the EBs forged by the other
# pools. The peer side messages need an EB forged by another pool, so they would be
# missing on a pool that forged every EB in the search window. A 40 block window holds
# ~17 EBs at the 0.42 EB per RB measured on `leios_fast`, so with 3 pools of equal stake
# the chance of that is 3 * (1/3)^17 = 2.3e-8.
EB_MSGS_ALL_POOLS = (
    r"Consensus\.LeiosPeer\.Announcement\].*EB announcement from peer",
    MSG_ANNOUNCEMENT_ACCEPTED,
    r"Consensus\.LeiosKernel\.BlockAcquired\].*EB body acquired:",
    r"Consensus\.LeiosKernel\.BlockTxsAcquired\].*EB txs acquired:",
    MSG_VOTE_ACQUIRED,
    MSG_VOTED,
    MSG_CERTIFIED,
)

# EB messages reported only by the pool that won the EB election, so they are expected
# in the log of at least one pool, not in the log of every pool.
EB_MSGS_ANY_POOL = (
    r"Consensus\.LeiosKernel\.BlockForged\].*EB forged at slot",
    r"Consensus\.LeiosKernel\.BlockAnnounced\].*EB announced:",
    r"Consensus\.LeiosKernel\.BlockStored\].*EB stored at slot",
    MSG_BLOCK_CERTIFIED,
    MSG_CERTIFIED_AND_ANNOUNCED,
)

# EB messages that prove the voting committee is active - a vote was cast, received or
# turned into a certificate.
VOTING_MSGS = (
    MSG_VOTE_ACQUIRED,
    MSG_VOTED,
    MSG_CERTIFIED,
    MSG_BLOCK_CERTIFIED,
    MSG_CERTIFIED_AND_ANNOUNCED,
)

# Reported by a pool for an EB announcement it cannot vote on, because it is not a
# member of the voting committee for the EB.
NOT_ON_COMMITTEE_MSG = r"Consensus\.LeiosKernel\.NotVoted\].*Leios not voted for .*: NotOnCommittee"

# Reported by a pool that declined to vote because none of the BLS signing keys the node
# was started with matches a seat of the current committee. That is what a producer runs
# into when its registered key was rotated but the node still holds only the old one, or
# when it holds only a key that is not seated yet. The node has more than one way to say
# it, so either message counts as evidence.
NOT_VOTED_KEY_MSGS = (
    r"Consensus\.LeiosKernel\.NotVoted\].*Leios not voted for .*: SignerNotInCommittee",
    r"Consensus\.LeiosKernel\.NotVoted\].*Leios not voted for .*: SignerHasNoKey",
)

# Every reason a pool can report for being unable to vote on an EB announcement it
# received - the pool is off the committee, or the key its node holds matches no seat.
# They are not interchangeable: a pool the committee has no room for reports
# `NotOnCommittee`, and only a pool that does hold a seat gets one of the key messages.
# Grouped for a check that has to notice a pool declining without caring which of these
# it was, e.g. because any of them is wrong in the situation being checked.
NOT_VOTED_MSGS = (NOT_ON_COMMITTEE_MSG, *NOT_VOTED_KEY_MSGS)

# The first epoch in which a pool registered by a transaction can be a member of the
# Leios voting committee. The committee is drawn from a stake distribution snapshot
# that is empty for the whole lifetime of a freshly started cluster instance until this
# epoch, so up to then every pool answers every EB announcement with `NotOnCommittee`
# and no EB can be voted on or certified. It doesn't apply to a testnet whose pools
# come with their BLS key straight from the genesis, see `is_committee_seated_in_genesis`.
VOTING_START_EPOCH = 3

# Number of seconds between two searches of the logs while waiting for messages.
SEARCH_STEP_SEC = 30

# Max number of seconds to spend searching the logs for a group of messages. EBs come in
# bursts, with gaps of up to ~250 sec between them on `leios_fast`, so 360 sec is one
# full gap plus slack - long enough for at least one burst to normally land in the
# window.
MAX_SEARCH_SEC = 360

# Number of seconds to keep between the end of a searched log window and the end of the
# epoch it runs in, so that a message logged in the next epoch cannot land in the
# searched part of the log.
EPOCH_MARGIN_SEC = 60

# Shortest search window that can still be conclusive. A window that has to fit into the
# rest of an epoch can come out shorter than `MAX_SEARCH_SEC`, and below this it holds
# too few EB bursts for the absence of a message to mean anything.
MIN_SEARCH_SEC = 120

# Min interval between two blocks for the cluster to be usable for the Leios tests. An
# EB is forged from exactly the mempool backlog that doesn't fit into a Praos block, so
# no backlog means no EB, no announcement, no votes and no certificate. The block
# production rate is what drains the mempool, so it must be slow enough for a backlog to
# build up. The `leios_fast` testnet variant produces a block every 20 seconds
# (`activeSlotsCoeff` 0.05, `slotLength` 1) and gets EBs, while the 10x faster block
# rate of the `local_fast` variant (and its 4x shorter epoch) drains the mempool.
MIN_BLOCK_INTERVAL_SEC = 10


def is_committee_seated_in_genesis(*, genesis: dict) -> bool:
    """Check whether the Leios voting committee is seated from epoch 0.

    A pool can vote once its BLS key is in a stake distribution snapshot the committee
    is drawn from. A pool registered by a transaction on a freshly started cluster
    instance gets there only in `VOTING_START_EPOCH`, while a pool whose parameters,
    BLS key included, are already in the genesis is in the initial ledger state and so
    on the committee from the first slot.

    The genesis always lists the pools created by `genesis create-staked --gen-pools`,
    but their entries carry a BLS key only when the cluster start script wrote the real
    pool parameters into them instead of submitting a registration transaction. The key
    is therefore what tells the two setups apart, not the presence of the pools. The
    entries live under `extraConfig` since cardano-cli 11.2 and under `staking` before
    that.

    Args:
        genesis: The Shelley genesis of the cluster instance.

    Returns:
        `True` when a pool is on the voting committee from epoch 0.
    """
    pools: dict = genesis.get("extraConfig", {}).get("stakePools", {}).get("data") or genesis.get(
        "staking", {}
    ).get("pools", {})
    return any(p.get("blsKey") for p in pools.values())


def skip_if_no_ebs_in_genesis(*, genesis: dict) -> None:
    """Skip the test when the genesis settings don't allow an EB to be forged.

    Takes the genesis instead of a cluster instance, so that a test which starts a
    cluster of its own can be ruled out before paying for the startup.

    Args:
        genesis: The Shelley genesis of the cluster instance, or the genesis spec the
            instance is started from.
    """
    slot_length = float(genesis["slotLength"])
    block_interval = slot_length / float(genesis["activeSlotsCoeff"])
    if block_interval >= MIN_BLOCK_INTERVAL_SEC:
        return

    epoch_length_sec = float(genesis["epochLength"]) * slot_length
    pytest.skip(
        f"Cannot observe EBs on the '{configuration.TESTNET_VARIANT}' testnet variant: "
        f"a block is produced every {block_interval} sec (epoch is "
        f"{epoch_length_sec:.0f} sec), which is too fast for a mempool backlog to "
        f"build up for an EB; needs at least {MIN_BLOCK_INTERVAL_SEC} sec per block"
    )


def skip_if_no_ebs(*, cluster_obj: clusterlib.ClusterLib) -> None:
    """Skip the test when the cluster settings don't allow an EB to be forged.

    The settings are known only once a cluster instance is assigned to the test, so this
    cannot be a `skipif` marker.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
    """
    skip_if_no_ebs_in_genesis(genesis=cluster_obj.genesis)


@dataclasses.dataclass
class LogSearch:
    """Where to continue searching a log file, and what was already found in it."""

    seek_offset: int
    inode: int
    timestamp: float
    found: set[str] = dataclasses.field(default_factory=set)


def get_log_position(logfile: pl.Path) -> tuple[int, int, float]:
    """Return the current end of the log file as (byte offset, inode, timestamp).

    A single `stat` call, so that the offset (file size) and the inode belong to the
    same file even when the file is rotated in between.
    """
    logfile_stat = logfile.stat()
    return logfile_stat.st_size, logfile_stat.st_ino, time.time()


def find_msgs(
    *,
    regexes: tp.Collection[str],
    logfile: pl.Path,
    seek_offset: int,
    inode: int,
    timestamp: float,
) -> set[str]:
    """Return the searched message regexes that are present in the log file.

    Args:
        regexes: The message regexes to search for. Duplicates are collapsed, so that
            the "found everything" early exit is reachable.
        logfile: Path to the node log file.
        seek_offset: Byte offset to start the search at.
        inode: Inode of the log file the `seek_offset` was recorded for.
        timestamp: Time the search offset was recorded at.

    Returns:
        The subset of `regexes` that matched at least one log line.
    """
    regexes = frozenset(regexes)

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
            if regex not in found and re.search(regex, line):
                found.add(regex)
        if len(found) == len(regexes):
            break

    return found


def init_searches(logfiles_list: tp.Iterable[pl.Path]) -> dict[pl.Path, LogSearch]:
    """Return the search state for each log file, starting at its current end."""
    searches: dict[pl.Path, LogSearch] = {}
    for logfile in logfiles_list:
        seek_offset, inode, timestamp = get_log_position(logfile)
        searches[logfile] = LogSearch(seek_offset=seek_offset, inode=inode, timestamp=timestamp)
    return searches


def search_round(
    *,
    searches: dict[pl.Path, LogSearch],
    missing_msgs: tp.Callable[[LogSearch], tp.Collection[str]],
    log_errors: dict[pl.Path, str],
) -> None:
    """Search the part of each log file that was appended since the previous round.

    Updates `searches` in place with what was found and where to continue, and
    `log_errors` with the log files that could not be searched.

    Args:
        searches: The search state per log file.
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
            next_position = get_log_position(logfile)
            search.found |= find_msgs(
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


def wait_for_msgs(
    *,
    logfile: pl.Path,
    regexes: tp.Collection[str],
    deadline: float,
    stop_on: tp.Collection[str] = (),
) -> tuple[set[str], list[str]]:
    """Search one log file for messages until they are all found, or the deadline passes.

    Only the log content that gets appended while the search runs is searched, and each
    round searches only the part that is new since the previous one.

    Args:
        logfile: Path to the node log file.
        regexes: The message regexes to search for.
        deadline: A `time.monotonic()` value the search must not go past.
        stop_on: Messages that end the search as soon as any of them is found, on top of
            having found all of `regexes`. Used when the searched messages are mutually
            exclusive outcomes and any one of them answers the question.

    Returns:
        The messages that were found, and the problems that got in the way of the search.
        A problem explains a missing message, so it is reported only together with one.
    """
    searches = init_searches([logfile])
    log_errors: dict[pl.Path, str] = {}
    all_msgs = frozenset(regexes)
    stop_msgs = frozenset(stop_on)

    while True:
        time.sleep(min(SEARCH_STEP_SEC, max(0.0, deadline - time.monotonic())))

        search_round(
            searches=searches,
            missing_msgs=lambda search: all_msgs - search.found,
            log_errors=log_errors,
        )

        found = searches[logfile].found
        if all_msgs <= found or found & stop_msgs or time.monotonic() >= deadline:
            break

    return searches[logfile].found, list(log_errors.values())
