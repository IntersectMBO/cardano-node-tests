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
"""

import json
import logging
import pathlib as pl
import time
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import bls
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import leios
from cardano_node_tests.tests import markers
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
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
_NOT_VOTED_MSGS_SET = frozenset(leios.NOT_VOTED_MSGS)
_NOT_VOTED_KEY_MSGS_SET = frozenset(leios.NOT_VOTED_KEY_MSGS)
_VOTE_OUTCOME_MSGS_SET = frozenset(
    (
        leios.MSG_VOTED,
        leios.MSG_ANNOUNCEMENT_ACCEPTED,
        leios.MSG_CERTIFIED,
        *leios.NOT_VOTED_MSGS,
    )
)
# What makes the absence of votes of a pool mean something: either the pool said it
# declined to vote, or it accepted an EB announcement it then didn't answer with a vote.
_NO_VOTE_EVIDENCE_MSGS_SET = _NOT_VOTED_MSGS_SET | {leios.MSG_ANNOUNCEMENT_ACCEPTED}

# Max number of new blocks to wait for while the expected messages are showing up in
# the logs. Certification of an EB needs a quorum of votes and an RB that announces it,
# so it can take several blocks before all the expected messages are reported.
MAX_WAIT_BLOCKS = 40
# Number of new blocks to wait for between two searches of the logs
WAIT_BLOCKS_STEP = 5

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


def _collect_vote_outcome_msgs(
    *, pool_logs: list[pl.Path], deadline: float
) -> tuple[dict[pl.Path, set[str]], list[str]]:
    """Search the pool logs for votes, EB announcements and declined votes.

    The three outcomes together say whether a pool voted, whether there was anything for
    it to vote on, and whether it reported why it didn't vote. The whole window is
    searched, with no early exit: a pool that is expected not to vote can report the
    reason first and vote afterwards, and a search that stopped at the first message
    would not see that.

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
            missing_msgs=lambda search: _VOTE_OUTCOME_MSGS_SET - search.found,
            log_errors=log_errors,
        )

        if time.monotonic() >= deadline:
            break

    return {p: s.found for p, s in searches.items()}, list(log_errors.values())


def _get_certificate_problems(
    *, found_per_pool: dict[str, set[str]], epoch: int
) -> tuple[list[str], list[str]]:
    """Judge whether the votes of one epoch added up to a certificate.

    The committee lost seats and the quorum was lowered to match, so a certificate is
    what says the seats that are left still reach it. Without an EB to vote on there is
    nothing to certify, and then the absence of certificates says nothing.

    Args:
        found_per_pool: The searched messages found in the log of each pool.
        epoch: The epoch the logs were searched in, for the reported messages.

    Returns:
        The failures, and the reasons the outcome is inconclusive. Both empty when a
        certificate was assembled.
    """
    if any(leios.MSG_CERTIFIED in f for f in found_per_pool.values()):
        return [], []

    if any(leios.MSG_ANNOUNCEMENT_ACCEPTED in f for f in found_per_pool.values()):
        error = (
            f"No pool assembled a Leios certificate in epoch {epoch}, so the votes of the "
            "seats the committee has left don't add up to the quorum."
        )
        return [error], []

    skip_reason = (
        f"No EB announcement reached any pool in epoch {epoch}, so the absence of "
        "certificates is inconclusive"
    )
    return [], [skip_reason]


def _get_voting_problems(
    *, found_per_pool: dict[str, set[str]], out_pool_names: frozenset[str], epoch: int
) -> tuple[list[str], list[str]]:
    """Judge what each pool was seen doing about the EBs of one epoch.

    A pool that holds no committee seat must have answered every EB announcement with
    `NotOnCommittee` and cast no vote, the seated pools must have voted and must not have
    declined for any of the searched reasons, and their votes must have added up to a
    certificate - the committee lost seats and the quorum was lowered to match, so a
    certificate is what says the seats that are left still reach it. A pool that didn't
    vote *and said nothing about it* is a failure only when there was an EB to vote on;
    without one the absence of votes says nothing, so it is reported as a reason to skip
    instead of as an error.

    Args:
        found_per_pool: The searched messages found in the log of each pool.
        out_pool_names: Names of the pools that the committee left out.
        epoch: The epoch the logs were searched in, for the reported messages.

    Returns:
        The failures, and the reasons the outcome is inconclusive. Both empty when every
        pool did what it was supposed to.
    """
    errors, skip_reasons = _get_certificate_problems(found_per_pool=found_per_pool, epoch=epoch)

    for pool_name, found in found_per_pool.items():
        voted = leios.MSG_VOTED in found
        if pool_name in out_pool_names:
            if voted:
                errors.append(
                    f"The pool '{pool_name}' voted in epoch {epoch}, although the Leios "
                    "committee of that epoch holds no seat for it."
                )
            # A key reason is reported only by a pool that does hold a seat, so it says
            # the ledger and the node disagree about the committee. Checked before the
            # `NotOnCommittee` branch, which otherwise passes a pool that reported both
            elif found & _NOT_VOTED_KEY_MSGS_SET:
                errors.append(
                    f"The pool '{pool_name}' declined to vote in epoch {epoch} for a reason "
                    "only a pool that holds a Leios committee seat can report, although the "
                    "committee of that epoch holds no seat for it: "
                    f"{sorted(found & _NOT_VOTED_KEY_MSGS_SET)}"
                )
            elif leios.NOT_ON_COMMITTEE_MSG in found:
                pass  # the pool answered every EB announcement the way it should have
            # Without the pool declining to vote, or at least accepting an EB
            # announcement, there may have been nothing to vote on
            elif not found & _NO_VOTE_EVIDENCE_MSGS_SET:
                skip_reasons.append(
                    f"The pool '{pool_name}' neither declined to vote nor accepted an EB "
                    f"announcement in epoch {epoch}, so the absence of votes is inconclusive"
                )
            else:
                # It saw EBs and cast no vote, but didn't name the committee as the
                # reason - which is the reason, and the only one the ledger gives it
                errors.append(
                    f"The pool '{pool_name}' didn't answer the EB announcements of epoch "
                    f"{epoch} with `NotOnCommittee`, although the Leios committee of that "
                    f"epoch holds no seat for it: {sorted(found)}"
                )
            continue

        # None of the searched reasons can apply to a pool that holds a seat its own key
        # matches, so this says the seat and the node disagree. Checked even when the pool
        # did vote, as one vote doesn't make a decline on another EB of the epoch right
        declined = sorted(found & _NOT_VOTED_MSGS_SET)
        if declined:
            errors.append(
                f"The pool '{pool_name}' declined to vote in epoch {epoch}, although it "
                f"holds a Leios committee seat: {declined}"
            )
        elif voted:
            continue
        elif leios.MSG_ANNOUNCEMENT_ACCEPTED in found:
            errors.append(
                f"The pool '{pool_name}' didn't vote in epoch {epoch}, although it holds a "
                "Leios committee seat."
            )
        else:
            skip_reasons.append(
                f"No EB announcement reached the pool '{pool_name}' in epoch {epoch}, so "
                "the absence of votes is inconclusive"
            )

    return errors, skip_reasons


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

        found_per_pool, search_problems = _collect_vote_outcome_msgs(
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

        errors, skip_reasons = _get_voting_problems(
            found_per_pool={n: found_per_pool[f] for n, f in pool_logs.items()},
            out_pool_names=frozenset(out_pools),
            epoch=search_epoch,
        )

        if errors:
            errors.extend(search_problems)
        assert not errors, "\n".join(errors)

        if skip_reasons:
            pytest.skip("; ".join([*skip_reasons, *search_problems]))
