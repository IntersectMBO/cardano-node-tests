"""Tests for rotation and expiration of the stake pool BLS (Leios voting) keys.

A pool registers its BLS key through the stake pool registration certificate, and the
ledger stores the key together with the epoch the registration took effect in
(``bksRegisteredIn``). That pair is what the Leios voting committee is drawn from, and
the key is offered to the committee only while::

    currentEpoch < bksRegisteredIn + maxKeyAge

``maxKeyAge`` is not a protocol parameter. The ledger derives it from the KES setup in
the Shelley genesis, so that voting key rotation rides along with the KES rotation pools
already run::

    maxKeyAge = ceil(maxKESEvolutions * slotsPerKESPeriod / epochLength) + 2

Rotating is the same operation as registering - submit a fresh pool registration
certificate carrying the new key. The ledger treats a registration of an already
registered pool as an update, so it lands in ``futurePoolParams`` and takes effect at the
next epoch boundary, which is where ``bksRegisteredIn`` is re-stamped. The committee
itself is seated once per epoch from the stake snapshot that rotates into the *set*
position, so a rotated key needs one more boundary before the committee holds it - two
epoch boundaries in total from the rotation transaction.

An aged-out key does not free the seat: the pool keeps its committee weight, cannot vote
with it, and the seat is not reallocated. That is what `query stake-snapshot` reports as
a seat with a ``key`` and ``"voting": false``. A pool that never registered a key holds
the same kind of seat, reported with a ``key`` of ``null`` - CIP-0164 keeps the
``bls_key`` field of the registration certificate optional for backwards compatibility.

The two epoch boundaries are a design choice rather than a necessity. CIP-0164 notes
that a voting key carries no nonce grinding concern and could therefore activate a full
epoch earlier than a VRF key, and settles on the VRF schedule anyway so that an operator
who rotates both keys does a single hot key swap. The tests check the boundary the ledger
implements, so the committee still holding the old key in the epoch after the rotation is
asserted rather than merely tolerated - moving to the earlier activation has to fail
here, not pass quietly.

CIP-0164 still lists the rotation mechanism as an open acceptance criterion (Appendix A
requirement 2), so what these tests pin down is the mechanism the ledger implements
today: re-registration through the pool certificate, with the key time-to-live derived
from the KES setup. The CIP requires rotation and a corresponding time-to-live, but
neither the certificate route nor the formula is settled there.

The two tests that rotate the key of a *cluster* pool need that pool to be
re-registrable, which a pool whose parameters come from the genesis is not - see
`reregister_cluster_pool`. They xfail on such an instance, so today they run for real
only on a testnet variant that starts before Dijkstra and hard-forks into it.

See CIP-0164 and the Leios testnet guide for the operator side of this.
"""

import dataclasses
import json
import logging
import math
import pathlib as pl
import shutil
import time

import allure
import pytest
import pytest_subtests
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import bls
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import kes
from cardano_node_tests.tests import leios
from cardano_node_tests.tests import markers
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import faucet
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.leios,
    pytest.mark.skipif(
        min(VERSIONS.cluster_era, VERSIONS.transaction_era) < VERSIONS.DIJKSTRA_FIRST,
        reason="BLS keys and the Leios voting committee are available only in Dijkstra+ eras",
    ),
]

# Number of epoch boundaries between the transaction that registers a BLS key and the
# epoch in which the Leios committee holds it. The first boundary applies the pool
# update, the second seats the committee from the snapshot that saw the update. That is
# the VRF key schedule, which CIP-0164 aligns voting keys with.
BLS_ACTIVATION_EPOCHS = 2

# Number of pool owner addresses created for a test pool
POOL_OWNERS_NUM = 2
# Funds for the pool owners, all of it given to the first address, which is the one that
# pays: the pool deposit, and the fees of the registration and of every rotation
POOL_OWNERS_FUNDS = 900_000_000 * POOL_OWNERS_NUM

# The KES setup of the `short_bls_keyage_start_cluster` fixture. The BLS key lifetime is
# derived from it, so shortening the KES lifetime to `KES_LIFETIME_EPOCHS` epochs brings
# `maxKeyAge` down to a number of epochs a test can actually wait through.
MAX_KES_EVOLUTIONS = 10
KES_LIFETIME_EPOCHS = 5
SHORT_MAX_KEY_AGE = KES_LIFETIME_EPOCHS + 2

# Max wall-clock time the expiration test is willing to spend waiting for epochs. The
# test has to reach epoch `SHORT_MAX_KEY_AGE`, so a testnet variant with long epochs is
# skipped instead of running for hours.
MAX_EXPIRATION_WAIT_SEC = 60 * 60


@pytest.fixture(scope="module")
def short_bls_keyage_start_cluster() -> pl.Path:
    """Return startup scripts with a KES setup that gives BLS keys a short lifetime.

    The ledger derives the BLS key lifetime from ``maxKESEvolutions`` and
    ``slotsPerKESPeriod``, so `slotsPerKESPeriod` is picked to make the KES lifetime
    exactly `KES_LIFETIME_EPOCHS` epochs. That makes ``maxKeyAge``
    `SHORT_MAX_KEY_AGE` epochs on any testnet variant, regardless of its epoch length.
    """
    shared_tmp = temptools.get_pytest_shared_tmp()

    # Need to lock because this same fixture can run on several workers in parallel
    with locking.FileLockIfXdist(f"{shared_tmp}/startup_files_short_bls_keyage.lock"):
        destdir = shared_tmp / "startup_files_short_bls_keyage"
        destdir.mkdir(exist_ok=True)

        # Return the existing scripts dir if it was already generated by another worker
        destdir_ls = list(destdir.glob("start-cluster*"))
        if destdir_ls:
            return destdir_ls[0].parent

        startup_files = cluster_nodes.get_cluster_type().cluster_scripts.copy_scripts_files(
            destdir=destdir
        )
        with open(startup_files.genesis_spec, encoding="utf-8") as fp_in:
            genesis_spec = json.load(fp_in)

        # The KES lifetime is `maxKESEvolutions * slotsPerKESPeriod` slots. Integer
        # division can only make the lifetime shorter than the requested number of
        # epochs, never longer, so the `ceil` in the `maxKeyAge` formula still yields
        # `KES_LIFETIME_EPOCHS`.
        genesis_spec["maxKESEvolutions"] = MAX_KES_EVOLUTIONS
        genesis_spec["slotsPerKESPeriod"] = int(
            genesis_spec["epochLength"] * KES_LIFETIME_EPOCHS / MAX_KES_EVOLUTIONS
        )

        with open(startup_files.genesis_spec, "w", encoding="utf-8") as fp_out:
            json.dump(genesis_spec, fp_out)

        return startup_files.start_script.parent


def get_startup_epoch_length_sec(*, scriptsdir: pl.Path) -> float:
    """Return the epoch length, in seconds, of a cluster started from the given scripts.

    Read before a cluster instance exists, so that a test which cannot fit into its
    wall-clock budget is skipped without first spinning one up.

    Args:
        scriptsdir: A path to the startup scripts dir.

    Returns:
        float: The length of an epoch, in seconds.
    """
    with open(scriptsdir / "genesis.spec.json", encoding="utf-8") as in_fp:
        genesis_spec = json.load(in_fp)

    return float(genesis_spec["epochLength"]) * float(genesis_spec["slotLength"])


@pytest.fixture
def cluster_short_bls_keyage(
    cluster_manager: cluster_management.ClusterManager,
    short_bls_keyage_start_cluster: pl.Path,
) -> clusterlib.ClusterLib:
    """Return a cluster instance where the BLS keys of the cluster pools expire soon.

    Spinning the instance up means starting a dedicated cluster from custom genesis, so
    the wall-clock budget is checked first, off the startup scripts.
    """
    epoch_length_sec = get_startup_epoch_length_sec(scriptsdir=short_bls_keyage_start_cluster)
    wait_sec = SHORT_MAX_KEY_AGE * epoch_length_sec
    if wait_sec > MAX_EXPIRATION_WAIT_SEC:
        pytest.skip(
            f"Reaching epoch {SHORT_MAX_KEY_AGE}, in which the BLS keys expire, takes "
            f"{wait_sec:.0f} sec on the '{configuration.TESTNET_VARIANT}' testnet variant"
        )

    cluster_obj = cluster_manager.get(
        lock_resources=[cluster_management.Resources.CLUSTER],
        prio=True,
        cleanup=True,
        scriptsdir=short_bls_keyage_start_cluster,
    )
    return cluster_obj


@pytest.fixture
def cluster_leios_singleton(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Lock the whole cluster instance and skip unless Leios EBs can be observed on it.

    The whole instance is locked because the test re-registers one of the cluster pools
    and restarts its node several times. It is respun afterwards rather than handed back,
    as the pool ends up with a different BLS key than the one it was set up with - so
    everything that can rule the test out is checked before the instance is marked.
    """
    cluster_obj = cluster_manager.get(lock_resources=[cluster_management.Resources.CLUSTER])
    leios.skip_if_no_ebs(cluster_obj=cluster_obj)

    # A search window has to fit into a single epoch, so that a vote cast in the next
    # epoch cannot be mistaken for one cast in the searched epoch
    epoch_tail_sec = leios.MAX_SEARCH_SEC + leios.EPOCH_MARGIN_SEC
    if cluster_obj.epoch_length_sec <= epoch_tail_sec:
        pytest.skip(
            f"An epoch takes only {cluster_obj.epoch_length_sec:.0f} sec on the "
            f"'{configuration.TESTNET_VARIANT}' testnet variant, which is not enough for "
            f"the {epoch_tail_sec} sec search window"
        )

    cluster_manager.set_needs_respin()
    return cluster_obj


def get_future_bls_key(*, cluster_obj: clusterlib.ClusterLib, pool_id: str) -> dict:
    """Return the BLS key of a pool update that is waiting for the next epoch boundary.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pool_id: An ID of the stake pool (Bech32-encoded or hex-encoded).

    Returns:
        dict: The ``blsKey`` record of ``futurePoolParams``, or an empty dict when there
            is no pending update, or it carries no BLS key.

    Note:
        A pending update is reported as stake pool *parameters*, which carry the bare key
        under ``blsKey``, while the active key is reported as pool *state* and carries the
        key under ``spsBlsKey.bksKey`` together with its registration epoch. The epoch is
        stamped only when the update takes effect, so the shallower nesting here is not
        an oversight.
    """
    future_params = cluster_obj.g_query.get_pool_state(stake_pool_id=pool_id).future_pool_params
    return future_params.get("blsKey") or {}


def get_committee_seat(*, cluster_obj: clusterlib.ClusterLib, pool_id: str) -> dict:
    """Return the Leios committee seat of a pool in the current epoch.

    The committee is reported as a whole regardless of the queried pool, so a single
    pool ID is enough to get it.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pool_id: An ID of the stake pool (Bech32-encoded or hex-encoded).

    Returns:
        dict: The seat of the pool, or an empty dict when the pool holds no seat.
    """
    pool_id_dec = helpers.decode_bech32(pool_id) if pool_id.startswith("pool") else pool_id
    snapshot = cluster_obj.g_query.get_stake_snapshot(stake_pool_ids=[pool_id_dec])
    committee: list[dict] = snapshot.get("leiosCommittee") or []
    return next((s for s in committee if s["poolId"] == pool_id_dec), {})


def get_max_key_age(*, cluster_obj: clusterlib.ClusterLib) -> int:
    """Return the BLS key lifetime in epochs, as the ledger derives it from genesis.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.

    Returns:
        int: The number of epochs a registered BLS key is honoured for.
    """
    genesis = cluster_obj.genesis
    kes_lifetime = int(genesis["maxKESEvolutions"]) * int(genesis["slotsPerKESPeriod"])
    return math.ceil(kes_lifetime / int(genesis["epochLength"])) + 2


def write_bls_key_bundle(*, key_files: list[pl.Path], out_file: pl.Path) -> pl.Path:
    """Write several BLS signing keys into one file, as a JSON array of key envelopes.

    `cardano-node run --shelley-bls-key` takes either a single text envelope or an array of
    them, and casts a vote for every committee seat any of the keys holds. A pair of the
    old and the new key is what keeps a pool voting across the epoch boundary where its
    registered key changes, as only the currently registered key matches a seat.

    The multi-key file is a convenience the node offers, not something CIP-0164 asks
    for - the CIP only says the operator has to have the newly active key on the block
    producing machine for the epoch it becomes effective in.

    Args:
        key_files: Paths to the BLS signing key files to bundle, in order.
        out_file: A path to write the bundle to.

    Returns:
        pl.Path: The path of the written bundle.
    """
    bundle = [clusterlib_utils.load_envelope(envelope_file=f) for f in key_files]
    with open(out_file, "w", encoding="utf-8") as out_fp:
        json.dump(bundle, out_fp, indent=4)
    return out_file


def install_node_bls_key(
    *,
    cluster_obj: clusterlib.ClusterLib,
    key_file: pl.Path,
    key_pair: clusterlib.KeyPair,
    vkey_file: pl.Path | None = None,
) -> None:
    """Put a BLS signing key file in place of a pool's own and restart the nodes.

    The node reads its BLS key only on startup, so a key swap needs a restart. All the
    nodes are restarted, so that the connections between them are established again, and
    the chain is given time to advance afterwards - a node that is still coming up logs
    no Leios activity, and a log search started too early would search that gap.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        key_file: A path to the signing key file, or a key bundle, to put in place.
        key_pair: The BLS key pair of the pool, pointing into the cluster state dir.
        vkey_file: A path to the matching verification key file, when the swap is meant
            to be the pool's new key for good (optional).
    """
    shutil.copy(key_file, key_pair.skey_file)
    if vkey_file is not None:
        shutil.copy(vkey_file, key_pair.vkey_file)
    cluster_nodes.restart_all_nodes(delay=5)
    cluster_obj.wait_for_new_block(new_blocks=2)


def search_voting_activity(
    *,
    cluster_obj: clusterlib.ClusterLib,
    logfile: pl.Path,
    within_epoch: bool = True,
    full_window: bool = False,
) -> tuple[set[str], list[str]]:
    """Search a pool log for a cast vote, an EB announcement and a declined vote.

    The three outcomes together say whether the pool voted, whether there was anything
    to vote on in the first place, and whether the pool declined because none of the
    keys its node holds is seated.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        logfile: A path to the log file of the block producing node.
        within_epoch: End the window before the current epoch does, so that a vote cast
            in the next epoch cannot land in the searched part of the log. Needed only
            while the committee still changes at the next boundary.
        full_window: Keep searching for the whole window even after a vote was found.
            Needed when the absence of votes is what is being checked, as the evidence
            that there was something to vote on can show up after the first message.

    Returns:
        The messages that were found, and the problems that got in the way of the search.
    """
    window_sec = float(leios.MAX_SEARCH_SEC)
    if within_epoch:
        window_sec = min(window_sec, cluster_obj.time_to_epoch_end() - leios.EPOCH_MARGIN_SEC)
        # Without a floor, a window that the epoch has no room left for would search
        # nothing and be reported as "no EB to vote on", which hides the real reason
        if window_sec < leios.MIN_SEARCH_SEC:
            pytest.skip(
                f"Only {window_sec:.0f} sec are left in epoch "
                f"{cluster_obj.g_query.get_epoch()} for the log search, which needs at "
                f"least {leios.MIN_SEARCH_SEC} sec to be conclusive"
            )
    return leios.wait_for_msgs(
        logfile=logfile,
        regexes=[
            leios.MSG_VOTED,
            leios.MSG_ANNOUNCEMENT_ACCEPTED,
            *leios.NOT_VOTED_KEY_MSGS,
        ],
        deadline=time.monotonic() + window_sec,
        stop_on=() if full_window else (leios.MSG_VOTED,),
    )


def check_voted(
    *,
    found: set[str],
    problems: list[str],
    errors: list[str],
    skip_reasons: list[str],
    description: str,
) -> None:
    """Record whether a pool was seen voting, in place.

    A pool that didn't vote is a failure only when there was an EB to vote on. Without
    one, the absence of votes says nothing, so it is reported as a reason to skip
    instead of as an error.

    Args:
        found: The messages a `search_voting_activity` call found.
        problems: The problems the same call reported.
        errors: Collects the failures, appended to in place.
        skip_reasons: Collects the inconclusive outcomes, appended to in place.
        description: Names the checked situation, for the reported message.
    """
    if leios.MSG_VOTED in found:
        return

    if leios.MSG_ANNOUNCEMENT_ACCEPTED in found:
        errors.append(f"The pool didn't vote {description}.")
        errors.extend(problems)
        return

    skip_reasons.append(
        f"No EB announcement reached the pool {description}, so the absence of votes "
        "is inconclusive"
    )
    skip_reasons.extend(problems)


def create_test_pool(
    *,
    cluster_obj: clusterlib.ClusterLib,
    cluster_manager: cluster_management.ClusterManager,
    temp_template: str,
    request: FixtureRequest,
) -> clusterlib.PoolCreationOutput:
    """Register a new stake pool with a BLS key and schedule its deregistration.

    The pool is not backed by a running node, so it never forges or votes. It is a
    member of the Leios committee all the same, because the committee is seated from the
    registered pools and the local testnet committee size is far above the number of
    pools.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        cluster_manager: An instance of `cluster_management.ClusterManager`.
        temp_template: A test identifier used for naming the created files.
        request: A pytest fixture request, used to register the deregistration.

    Returns:
        clusterlib.PoolCreationOutput: The pool creation output.
    """
    pool_owners = addrs_common.get_pool_users(
        name_template=temp_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        num=POOL_OWNERS_NUM,
        fund_idx=[0],
        amount=POOL_OWNERS_FUNDS,
    )

    pool_data = clusterlib.PoolData(
        pool_name=f"pool_{temp_template}",
        pool_pledge=1_000,
        pool_cost=cluster_obj.g_query.get_protocol_params().get("minPoolCost", 0),
        pool_margin=0.01,
    )

    pool_creation_out = cluster_obj.g_stake_pool.create_stake_pool(
        pool_data=pool_data,
        pool_owners=pool_owners,
        tx_name=f"{temp_template}_reg",
    )

    # The finalizer runs after the test, when the working dir is no longer the one the
    # test ran in, so the dir is captured here for the deregistration artifacts
    temp_dir = pl.Path.cwd()

    def _deregister() -> None:
        with helpers.change_cwd(temp_dir):
            cluster_obj.g_stake_pool.deregister_stake_pool(
                pool_owners=pool_owners,
                cold_key_pair=pool_creation_out.cold_key_pair,
                epoch=cluster_obj.g_query.get_epoch() + 2,
                pool_name=pool_data.pool_name,
                tx_name=f"{temp_template}_dereg",
            )

    request.addfinalizer(_deregister)

    return pool_creation_out


def get_pool_bls_key_pair(
    *, pool_creation_out: clusterlib.PoolCreationOutput
) -> clusterlib.KeyPair:
    """Return the BLS key pair a created pool was registered with.

    Args:
        pool_creation_out: The output of a pool registration.

    Returns:
        clusterlib.KeyPair: The BLS key pair of the pool.
    """
    assert pool_creation_out.bls_key_pair is not None, "The pool was created without a BLS key pair"
    return pool_creation_out.bls_key_pair


def register_pool_with_bls_key(
    *,
    cluster_obj: clusterlib.ClusterLib,
    cluster_manager: cluster_management.ClusterManager,
    temp_template: str,
    request: FixtureRequest,
) -> tuple[clusterlib.PoolCreationOutput, int, str]:
    """Register a new stake pool with a BLS key, inside a single epoch.

    The registration and the checks that read back the epoch it was stamped with must
    not be split by an epoch boundary, so the registration is started early enough in
    the epoch for the whole transaction to fit into it.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        cluster_manager: An instance of `cluster_management.ClusterManager`.
        temp_template: A test identifier used for naming the created files.
        request: A pytest fixture request, used to register the deregistration.

    Returns:
        The pool creation output, the epoch the registration landed in, and the hex
        encoded BLS verification key the pool registered.
    """
    clusterlib_utils.wait_for_epoch_interval(
        cluster_obj=cluster_obj, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
    )
    reg_epoch = cluster_obj.g_query.get_epoch()

    pool_creation_out = create_test_pool(
        cluster_obj=cluster_obj,
        cluster_manager=cluster_manager,
        temp_template=temp_template,
        request=request,
    )

    assert cluster_obj.g_query.get_epoch() == reg_epoch, (
        "The pool registration took longer than expected and would affect other checks"
    )

    bls_key_pair = get_pool_bls_key_pair(pool_creation_out=pool_creation_out)

    return pool_creation_out, reg_epoch, bls.get_vkey_hex(vkey_file=bls_key_pair.vkey_file)


def check_seat_voting(
    *,
    cluster_obj: clusterlib.ClusterLib,
    pool_name: str,
    pool_id: str,
    epoch: int,
    errors: list[str],
    expected_vkey: str | None = None,
    context: str = "",
) -> None:
    """Record whether a pool holds a committee seat it can vote with, in place.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pool_name: A name of the stake pool, for the reported message.
        pool_id: An ID of the stake pool (Bech32-encoded or hex-encoded).
        epoch: The epoch the seat is checked in, for the reported message.
        errors: Collects the failures, appended to in place.
        expected_vkey: The hex encoded BLS key the seat is expected to hold (optional).
        context: Added to the reported message, to say why the pool should be voting.
    """
    seat = get_committee_seat(cluster_obj=cluster_obj, pool_id=pool_id)

    if not seat:
        errors.append(
            f"The pool '{pool_name}' holds no Leios committee seat in epoch {epoch}{context}"
        )
        return

    seat_vkey = bls.get_bls_pub_key(bls_key_state=seat.get("key"))
    if expected_vkey is not None and seat_vkey != expected_vkey:
        errors.append(
            f"The Leios committee of epoch {epoch} doesn't hold the expected BLS key of "
            f"'{pool_name}': {seat}"
        )

    if seat.get("voting") is not True:
        errors.append(f"The pool '{pool_name}' is not voting in epoch {epoch}{context}: {seat}")


def check_seat_keyless(
    *,
    cluster_obj: clusterlib.ClusterLib,
    pool_name: str,
    pool_id: str,
    epoch: int,
    errors: list[str],
) -> None:
    """Record whether a pool kept its seat with an aged-out key, in place.

    An expired key doesn't free the seat, and the key is still reported - what is gone is
    the ability to vote with the weight the seat carries.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pool_name: A name of the stake pool, for the reported message.
        pool_id: An ID of the stake pool (Bech32-encoded or hex-encoded).
        epoch: The epoch the seat is checked in, for the reported message.
        errors: Collects the failures, appended to in place.
    """
    seat = get_committee_seat(cluster_obj=cluster_obj, pool_id=pool_id)

    if not seat:
        errors.append(
            f"The pool '{pool_name}' lost its Leios committee seat in epoch {epoch}, instead "
            "of keeping it without a usable key"
        )
        return

    if not seat.get("key"):
        errors.append(
            f"The seat of the pool '{pool_name}' reports no BLS key in epoch {epoch}: {seat}"
        )

    if seat.get("voting") is not False:
        errors.append(
            f"The pool '{pool_name}' is still voting in epoch {epoch}, although its BLS key "
            f"expired: {seat}"
        )


def reregister_cluster_pool(
    *,
    cluster_obj: clusterlib.ClusterLib,
    pool_rec: dict,
    pool_name: str,
    pool_id: str,
    bls_skey_file: pl.Path,
    tx_name: str,
) -> None:
    """Re-register a cluster pool with a new BLS key, keeping everything else as it is.

    Xfails on a cluster instance whose pools came from the genesis: the ledger records no
    occurrence of their VRF key hash, so the Dijkstra `POOL` rule rejects the update with
    `VRFKeyHashAlreadyRegistered` even though the VRF key does not change. Once the ledger
    populates the map on genesis injection, the rejection stops and the tests run.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pool_rec: The addresses and keys of the pool, from the cluster manager cache.
        pool_name: A name of the pool, e.g. ``node-pool1``.
        pool_id: An ID of the stake pool (Bech32-encoded or hex-encoded).
        bls_skey_file: A path to the BLS signing key file to register.
        tx_name: A name of the transaction.
    """
    pool_data = clusterlib_utils.load_registered_pool_data(
        cluster_obj=cluster_obj, pool_name=f"rotated_{pool_name}", pool_id=pool_id
    )

    try:
        cluster_obj.g_stake_pool.register_stake_pool(
            pool_data=pool_data,
            pool_owners=[clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])],
            vrf_vkey_file=pool_rec["vrf_key_pair"].vkey_file,
            cold_key_pair=pool_rec["cold_key_pair"],
            tx_name=tx_name,
            reward_account_vkey_file=pool_rec["reward"].vkey_file,
            bls_signing_key_file=bls_skey_file,
            deposit=0,  # no additional deposit, the pool is already registered
        )
    except clusterlib.CLIError as excinfo:
        if "VRFKeyHashAlreadyRegistered" not in str(excinfo):
            raise
        issues.ledger_6102.finish_test()


def rotate_bls_key(
    *,
    cluster_obj: clusterlib.ClusterLib,
    pool_creation_out: clusterlib.PoolCreationOutput,
    bls_skey_file: pl.Path | None,
    tx_name: str,
) -> None:
    """Re-register a pool with a different BLS signing key, leaving everything else as is.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pool_creation_out: The output of the original pool registration.
        bls_skey_file: A path to the BLS signing key file to register, or `None` to
            submit a certificate that carries no BLS key at all.
        tx_name: A name of the transaction, also used for naming the certificate.
    """
    # The certificate file is named after the pool, so a rotation needs a name of its
    # own to avoid overwriting the certificate of the original registration
    pool_data = dataclasses.replace(
        pool_creation_out.pool_data, pool_name=f"{pool_creation_out.pool_data.pool_name}_{tx_name}"
    )

    cluster_obj.g_stake_pool.register_stake_pool(
        pool_data=pool_data,
        pool_owners=pool_creation_out.pool_owners,
        vrf_vkey_file=pool_creation_out.vrf_key_pair.vkey_file,
        cold_key_pair=pool_creation_out.cold_key_pair,
        tx_name=tx_name,
        bls_signing_key_file=bls_skey_file,
        deposit=0,  # no additional deposit, the pool is already registered
    )


def report_subtest(
    *,
    subtests: pytest_subtests.SubTests,
    name: str,
    errors: list[str],
    skip_reasons: list[str],
) -> None:
    """Report one phase of a test as a subtest, so that the others still report.

    Args:
        subtests: The `pytest-subtests` fixture.
        name: Names the phase in the subtest report.
        errors: The failures the phase collected, empty when it passed.
        skip_reasons: Why the phase was inconclusive, empty when it was not.
    """
    with subtests.test(node_key=name):
        assert not errors, "\n".join(errors)
        if skip_reasons:
            pytest.skip("; ".join(skip_reasons))


class TestBlsKeyRotation:
    """Tests for rotating the BLS key of a registered stake pool."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_rotate_bls_key(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        request: FixtureRequest,
    ):
        """Rotate the BLS key of a stake pool by re-registering the pool.

        * Register a new stake pool with a BLS key and check that the ledger stamped the
          key with the epoch the registration landed in
        * Re-register the pool in the next epoch with a freshly generated BLS key
        * Check that the new key is pending in `futurePoolParams` and that the active
          key didn't change yet
        * Check that after the epoch boundary the new key is the registered one, and
          that its registration epoch was re-stamped with the epoch the update took
          effect in
        * Check that the Leios committee still holds the old key in that epoch - the
          committee was seated from a snapshot that predates the update
        * Check that after one more epoch boundary the committee seat holds the new key
          and is voting
        * Check that the rotation didn't change any other pool parameter
        """
        cluster_obj = cluster
        temp_template = common.get_test_id(cluster_obj)

        pool_creation_out, reg_epoch, orig_vkey = register_pool_with_bls_key(
            cluster_obj=cluster_obj,
            cluster_manager=cluster_manager,
            temp_template=temp_template,
            request=request,
        )
        pool_id = pool_creation_out.stake_pool_id

        # A registration of a new pool takes effect right away, and the key is stamped
        # with the epoch the registration landed in
        bls_key = bls.get_registered_bls_key(cluster_obj=cluster_obj, pool_id=pool_id)
        assert bls.get_bls_pub_key(bls_key_state=bls_key) == orig_vkey, (
            f"Unexpected registered BLS key: {bls_key}"
        )
        assert bls_key["bksRegisteredIn"] == reg_epoch, (
            f"The BLS key was registered in epoch {bls_key['bksRegisteredIn']} instead of "
            f"{reg_epoch}"
        )

        # Rotate in the epoch that follows the registration, so that the committee of the
        # epoch after the rotation was seated from a snapshot that already knows the pool
        cluster_obj.wait_for_epoch(epoch_no=reg_epoch + 1, padding_seconds=5)
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster_obj, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        rotate_epoch = cluster_obj.g_query.get_epoch()

        new_bls_key_pair = cluster_obj.g_node.gen_bls_key_pair(node_name=f"{temp_template}_new")
        new_vkey = bls.get_vkey_hex(vkey_file=new_bls_key_pair.vkey_file)
        assert new_vkey != orig_vkey, "The newly generated BLS key is the same as the original one"

        rotate_bls_key(
            cluster_obj=cluster_obj,
            pool_creation_out=pool_creation_out,
            bls_skey_file=new_bls_key_pair.skey_file,
            tx_name=f"{temp_template}_rotate",
        )

        assert cluster_obj.g_query.get_epoch() == rotate_epoch, (
            "The rotation took longer than expected and would affect other checks"
        )

        # The re-registration is an update, so it waits for the next epoch boundary
        assert (
            get_future_bls_key(cluster_obj=cluster_obj, pool_id=pool_id).get("blsPubKey")
            == new_vkey
        ), "The new BLS key is not pending in `futurePoolParams`"
        assert (
            bls.get_bls_pub_key(
                bls_key_state=bls.get_registered_bls_key(cluster_obj=cluster_obj, pool_id=pool_id)
            )
            == orig_vkey
        ), "The registered BLS key changed before the epoch boundary"

        # The update takes effect on the epoch boundary, which is also where the
        # registration epoch of the key is re-stamped
        # Overshooting lands in the epoch that already holds the rotated key, where the
        # check below would fail as a wrong key rather than as a missed window
        this_epoch = cluster_obj.wait_for_epoch(
            epoch_no=rotate_epoch + 1, padding_seconds=5, future_is_ok=False
        )
        bls_key = bls.get_registered_bls_key(cluster_obj=cluster_obj, pool_id=pool_id)
        assert bls.get_bls_pub_key(bls_key_state=bls_key) == new_vkey, (
            f"The BLS key was not rotated: {bls_key}"
        )
        assert bls_key["bksRegisteredIn"] == rotate_epoch + 1, (
            f"The rotated BLS key is stamped with epoch {bls_key['bksRegisteredIn']} instead of "
            f"{rotate_epoch + 1}"
        )

        # The committee of this epoch was seated before the update, so it still holds
        # the old key
        seat = get_committee_seat(cluster_obj=cluster_obj, pool_id=pool_id)
        assert seat, f"The pool holds no Leios committee seat in epoch {this_epoch}"
        assert bls.get_bls_pub_key(bls_key_state=seat.get("key")) == orig_vkey, (
            f"The Leios committee of epoch {this_epoch} doesn't hold the original BLS key: {seat}"
        )
        assert seat.get("voting") is True, (
            f"The pool is not voting with the original BLS key in epoch {this_epoch}: {seat}"
        )

        # One more boundary and the committee is seated from the snapshot that saw the
        # update
        this_epoch = cluster_obj.wait_for_epoch(
            epoch_no=rotate_epoch + BLS_ACTIVATION_EPOCHS, padding_seconds=5
        )
        seat = get_committee_seat(cluster_obj=cluster_obj, pool_id=pool_id)
        assert seat, f"The pool holds no Leios committee seat in epoch {this_epoch}"
        assert bls.get_bls_pub_key(bls_key_state=seat.get("key")) == new_vkey, (
            f"The Leios committee of epoch {this_epoch} doesn't hold the rotated BLS key: {seat}"
        )
        assert (seat.get("key") or {}).get("bksRegisteredIn") == rotate_epoch + 1, (
            f"The committee seat holds an unexpected registration epoch: {seat}"
        )
        assert seat.get("voting") is True, (
            f"The pool is not voting with the rotated BLS key in epoch {this_epoch}: {seat}"
        )

        # The rotation resubmits every pool parameter, so a mistake there would show up
        # as a changed pool
        pool_params = cluster_obj.g_query.get_pool_state(stake_pool_id=pool_id).pool_params
        assert not clusterlib_utils.check_pool_data(
            pool_params=pool_params, pool_creation_data=pool_creation_out.pool_data
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_reregister_same_bls_key(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        request: FixtureRequest,
    ):
        """Renew a BLS key by re-registering the pool with the very same key.

        Re-registration is what restarts the key lifetime clock, and it does so by
        re-stamping the registration epoch. That works with the key the pool already
        holds, so an operator can renew without handling a new key.

        * Register a new stake pool with a BLS key
        * Re-register the pool in the next epoch with the same BLS key
        * Check that after the epoch boundary the registered key is unchanged, but its
          registration epoch was re-stamped with the epoch the update took effect in
        """
        cluster_obj = cluster
        temp_template = common.get_test_id(cluster_obj)

        pool_creation_out, reg_epoch, orig_vkey = register_pool_with_bls_key(
            cluster_obj=cluster_obj,
            cluster_manager=cluster_manager,
            temp_template=temp_template,
            request=request,
        )
        pool_id = pool_creation_out.stake_pool_id

        bls_key = bls.get_registered_bls_key(cluster_obj=cluster_obj, pool_id=pool_id)
        assert bls_key, "The newly registered pool has no BLS key"
        assert bls_key["bksRegisteredIn"] == reg_epoch, (
            f"The BLS key was registered in epoch {bls_key['bksRegisteredIn']} instead of "
            f"{reg_epoch}"
        )

        # Renew in a later epoch, so that the re-stamped registration epoch differs from
        # the original one
        cluster_obj.wait_for_epoch(epoch_no=reg_epoch + 1, padding_seconds=5)
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster_obj, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        renew_epoch = cluster_obj.g_query.get_epoch()

        rotate_bls_key(
            cluster_obj=cluster_obj,
            pool_creation_out=pool_creation_out,
            bls_skey_file=get_pool_bls_key_pair(pool_creation_out=pool_creation_out).skey_file,
            tx_name=f"{temp_template}_renew",
        )

        assert cluster_obj.g_query.get_epoch() == renew_epoch, (
            "The renewal took longer than expected and would affect other checks"
        )

        cluster_obj.wait_for_epoch(epoch_no=renew_epoch + 1, padding_seconds=5)
        bls_key = bls.get_registered_bls_key(cluster_obj=cluster_obj, pool_id=pool_id)
        assert bls.get_bls_pub_key(bls_key_state=bls_key) == orig_vkey, (
            f"The registered BLS key changed: {bls_key}"
        )
        assert bls_key["bksRegisteredIn"] == renew_epoch + 1, (
            f"The renewed BLS key is stamped with epoch {bls_key['bksRegisteredIn']} instead of "
            f"{renew_epoch + 1}"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_drop_and_restore_bls_key(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_conway_cmd: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        request: FixtureRequest,
    ):
        """Drop the BLS key of a pool with a Conway-era certificate, then rotate one back in.

        A Conway-era pool registration certificate carries no BLS key, and the Dijkstra
        ledger accepts it as a pool update - it just leaves the pool without a voting
        key. The pool keeps its committee seat, so this is the "seated but keyless" case
        an operator ends up in, reached on purpose. Rotating a key back in is the way out
        of it.

        `test_pools.TestCompatibility.test_pool_registration_conway_cert` covers the
        same certificate as a *first* registration, where the pool simply never had a
        key. What is checked here is the update path that takes a key away, and what the
        committee makes of the pool afterwards.

        * Register a new stake pool with a BLS key
        * Re-register the pool in the same epoch with a Conway-era certificate, which
          carries no BLS key
        * Check that after the epoch boundary the pool has no registered BLS key
        * Check that the pool still holds a Leios committee seat, with no key and not
          voting
        * Re-register the pool with a freshly generated BLS key
        * Check that after the epoch boundary the pool holds the new key again
        """
        cluster_obj = cluster
        temp_template = common.get_test_id(cluster_obj)

        # The registration and the certificate that drops the key have to land in the
        # same epoch, so that the drop is applied on the very next boundary
        pool_creation_out, reg_epoch, __ = register_pool_with_bls_key(
            cluster_obj=cluster_obj,
            cluster_manager=cluster_manager,
            temp_template=temp_template,
            request=request,
        )
        pool_id = pool_creation_out.stake_pool_id

        assert bls.get_registered_bls_key(cluster_obj=cluster_obj, pool_id=pool_id), (
            "The newly registered pool has no BLS key"
        )

        # A Conway-era certificate has no `--bls-signing-key-file` argument at all, so
        # the cert is generated with the `conway` command era and submitted as usual
        pool_reg_cert_file = cluster_conway_cmd.g_stake_pool.gen_pool_registration_cert(
            pool_data=dataclasses.replace(
                pool_creation_out.pool_data,
                pool_name=f"{pool_creation_out.pool_data.pool_name}_nobls",
            ),
            vrf_vkey_file=pool_creation_out.vrf_key_pair.vkey_file,
            cold_vkey_file=pool_creation_out.cold_key_pair.vkey_file,
            owner_stake_vkey_files=[p.stake.vkey_file for p in pool_creation_out.pool_owners],
        )
        cert_cbor = clusterlib_utils.load_envelope_cbor(envelope_file=pool_reg_cert_file)
        assert len(cert_cbor) == common.POOL_REG_CERT_CONWAY_ITEMS, (
            f"Unexpected pool registration certificate: {cert_cbor}"
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[pool_reg_cert_file],
            signing_key_files=[
                *[p.payment.skey_file for p in pool_creation_out.pool_owners],
                *[p.stake.skey_file for p in pool_creation_out.pool_owners],
                pool_creation_out.cold_key_pair.skey_file,
            ],
        )
        cluster_obj.g_transaction.send_tx(
            src_address=pool_creation_out.pool_owners[0].payment.address,
            tx_name=f"{temp_template}_drop_bls",
            tx_files=tx_files,
            deposit=0,  # no additional deposit, the pool is already registered
        )

        assert cluster_obj.g_query.get_epoch() == reg_epoch, (
            "The pool setup took longer than expected and would affect other checks"
        )

        # The update takes effect on the epoch boundary
        drop_epoch = cluster_obj.wait_for_epoch(epoch_no=reg_epoch + 1, padding_seconds=5)
        assert not bls.get_registered_bls_key(cluster_obj=cluster_obj, pool_id=pool_id), (
            "The pool still has a registered BLS key"
        )

        # A pool with no key is still seated, it just cannot vote with its weight
        this_epoch = cluster_obj.wait_for_epoch(
            epoch_no=reg_epoch + BLS_ACTIVATION_EPOCHS, padding_seconds=5
        )
        seat = get_committee_seat(cluster_obj=cluster_obj, pool_id=pool_id)
        assert seat, f"The pool holds no Leios committee seat in epoch {this_epoch}"
        assert seat.get("key") is None, (
            f"The keyless pool got a key on the Leios committee of epoch {this_epoch}: {seat}"
        )
        assert seat.get("voting") is False, (
            f"The keyless pool is voting in epoch {this_epoch}: {seat}"
        )

        # Rotating a key in is the way back out of the keyless state
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster_obj, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        restore_epoch = cluster_obj.g_query.get_epoch()
        assert restore_epoch > drop_epoch, "The check of the keyless pool took longer than expected"

        new_bls_key_pair = cluster_obj.g_node.gen_bls_key_pair(node_name=f"{temp_template}_new")
        new_vkey = bls.get_vkey_hex(vkey_file=new_bls_key_pair.vkey_file)

        rotate_bls_key(
            cluster_obj=cluster_obj,
            pool_creation_out=pool_creation_out,
            bls_skey_file=new_bls_key_pair.skey_file,
            tx_name=f"{temp_template}_restore",
        )

        assert cluster_obj.g_query.get_epoch() == restore_epoch, (
            "The restore took longer than expected and would affect other checks"
        )

        cluster_obj.wait_for_epoch(epoch_no=restore_epoch + 1, padding_seconds=5)
        bls_key = bls.get_registered_bls_key(cluster_obj=cluster_obj, pool_id=pool_id)
        assert bls.get_bls_pub_key(bls_key_state=bls_key) == new_vkey, (
            f"The BLS key was not restored: {bls_key}"
        )
        assert bls_key["bksRegisteredIn"] == restore_epoch + 1, (
            f"The restored BLS key is stamped with epoch {bls_key['bksRegisteredIn']} instead of "
            f"{restore_epoch + 1}"
        )


class TestBlsKeyExpiration:
    """Tests for the expiration of a registered BLS key."""

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
    def test_expired_bls_key(
        self,
        cluster_short_bls_keyage: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
    ):
        """Test that a BLS key expires, and that rotating it in time prevents that.

        * Start a local cluster instance whose KES setup gives the BLS keys a lifetime of
          only `SHORT_MAX_KEY_AGE` epochs, so the keys the cluster pools registered on
          startup expire during the test
        * Refresh the operational certificates of all the pools, so that the short KES
          lifetime doesn't stop them from forging before the BLS keys expire
        * Rotate the BLS key of one pool, early enough for the rotated key to be seated
          before the original keys expire
        * Check that in the last epoch before the expiration every pool is voting, and
          that the rotated pool is voting with its new key
        * Check that in the expiration epoch the pools that didn't rotate are still
          seated, with their key reported and not voting, while the rotated pool keeps
          voting
        """
        cluster = cluster_short_bls_keyage
        temp_template = common.get_test_id(cluster)

        max_key_age = get_max_key_age(cluster_obj=cluster)
        assert max_key_age == SHORT_MAX_KEY_AGE, (
            f"The cluster instance gives BLS keys a lifetime of {max_key_age} epochs, "
            f"expected {SHORT_MAX_KEY_AGE}"
        )

        rotated_pool_name = cluster_management.Resources.POOL1
        rotated_pool_rec = cluster_manager.cache.addrs_data[rotated_pool_name]
        rotated_pool_id = delegation.get_pool_id(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            pool_name=rotated_pool_name,
        )
        expired_pool_ids = {
            p: delegation.get_pool_id(
                cluster_obj=cluster,
                addrs_data=cluster_manager.cache.addrs_data,
                pool_name=p,
            )
            for p in cluster_management.Resources.ALL_POOLS
            if p != rotated_pool_name
        }

        # The keys the pools registered on cluster startup all expire at the same epoch
        orig_bls_key = bls.get_registered_bls_key(cluster_obj=cluster, pool_id=rotated_pool_id)
        assert orig_bls_key, f"The pool '{rotated_pool_name}' has no registered BLS key"
        expire_epoch = orig_bls_key["bksRegisteredIn"] + max_key_age

        # The rotated key has to be seated before `expire_epoch`, and the rotation needs
        # `BLS_ACTIVATION_EPOCHS` epoch boundaries to get there
        rotate_epoch = expire_epoch - BLS_ACTIVATION_EPOCHS - 1
        # The operational certificates are refreshed before the rotation, while the KES
        # keys of the pools are still valid
        refresh_epoch = rotate_epoch - 1

        # Both waits below are no-ops once the instance is past their epoch, and the
        # test would then burn an opcert refresh and an epoch wait before noticing
        this_epoch = cluster.g_query.get_epoch()
        if this_epoch > refresh_epoch:
            pytest.skip(
                f"The cluster instance is already in epoch {this_epoch}, past the epoch "
                f"{refresh_epoch} in which the operational certificates have to be refreshed "
                f"for the BLS keys to expire on schedule in epoch {expire_epoch}"
            )

        # The node restart drops the connections to the other nodes
        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="MuxBearerClosed",
            ignore_file_id=cluster_manager.worker_id,
        )

        cluster.wait_for_epoch(epoch_no=refresh_epoch, padding_seconds=5)
        kes.refresh_opcerts(
            cluster_obj=cluster,
            cluster_manager=cluster_manager,
            node_names=[p.replace("node-", "") for p in cluster_management.Resources.ALL_POOLS],
            name_template=temp_template,
        )
        # The restarted nodes are queried right after, so give them a chance to come up
        cluster.wait_for_new_block(new_blocks=2)

        # Rotate the BLS key of one pool, so that its seat outlives the expiration of
        # the keys the other pools registered on startup
        cluster.wait_for_epoch(epoch_no=rotate_epoch, padding_seconds=5)
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        assert cluster.g_query.get_epoch() == rotate_epoch, (
            f"Missed the window for rotating the BLS key in epoch {rotate_epoch}"
        )

        new_bls_key_pair = cluster.g_node.gen_bls_key_pair(node_name=f"{temp_template}_new")
        new_vkey = bls.get_vkey_hex(vkey_file=new_bls_key_pair.vkey_file)

        pool_owner = clusterlib.PoolUser(
            payment=rotated_pool_rec["payment"], stake=rotated_pool_rec["stake"]
        )
        # The pool owner pays for the rotation transaction
        faucet.fund_from_faucet(
            pool_owner.payment,
            cluster_obj=cluster,
            all_faucets=cluster_manager.cache.addrs_data,
            amount=900_000_000,
            tx_name=f"{temp_template}_fund_owner",
            force=True,
        )

        reregister_cluster_pool(
            cluster_obj=cluster,
            pool_rec=rotated_pool_rec,
            pool_name=rotated_pool_name,
            pool_id=rotated_pool_id,
            bls_skey_file=new_bls_key_pair.skey_file,
            tx_name=f"{temp_template}_rotate",
        )

        assert cluster.g_query.get_epoch() == rotate_epoch, (
            "The rotation took longer than expected and would affect other checks"
        )

        # The last epoch in which the keys registered on startup are still honoured
        # Overshooting lands in the expiration epoch, where the pools that didn't rotate
        # are guaranteed not to be voting
        this_epoch = cluster.wait_for_epoch(
            epoch_no=expire_epoch - 1, padding_seconds=5, future_is_ok=False
        )
        errors: list[str] = []

        check_seat_voting(
            cluster_obj=cluster,
            pool_name=rotated_pool_name,
            pool_id=rotated_pool_id,
            epoch=this_epoch,
            errors=errors,
            expected_vkey=new_vkey,
        )
        for pool_name, pool_id in expired_pool_ids.items():
            check_seat_voting(
                cluster_obj=cluster,
                pool_name=pool_name,
                pool_id=pool_id,
                epoch=this_epoch,
                errors=errors,
                context=f", which is before its BLS key expires in epoch {expire_epoch}",
            )

        assert not errors, "\n".join(errors)

        # The keys registered on startup are past their lifetime in this epoch
        this_epoch = cluster.wait_for_epoch(epoch_no=expire_epoch, padding_seconds=5)

        check_seat_voting(
            cluster_obj=cluster,
            pool_name=rotated_pool_name,
            pool_id=rotated_pool_id,
            epoch=this_epoch,
            errors=errors,
            expected_vkey=new_vkey,
            context=f", although its BLS key was rotated in epoch {rotate_epoch}",
        )
        for pool_name, pool_id in expired_pool_ids.items():
            check_seat_keyless(
                cluster_obj=cluster,
                pool_name=pool_name,
                pool_id=pool_id,
                epoch=this_epoch,
                errors=errors,
            )

        assert not errors, "\n".join(errors)


class TestBlsKeyRotationVoting:
    """End to end test of the BLS key rotation procedure on a block producing pool."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(bool(leios.SKIP_REASON), reason=leios.SKIP_REASON)
    @markers.SKIPIF_ON_TESTNET
    # Scheduled at the end of the testrun, after `test_leios_blocks`, so that the
    # cluster instance is already past `leios.VOTING_START_EPOCH` and so that the epochs
    # in which this test keeps a pool from voting come last
    @pytest.mark.order(-9)
    @pytest.mark.xdist_split(markers.XdSplits.heavy)
    @pytest.mark.long
    def test_rotation_pair_keeps_voting(
        self,
        cluster_leios_singleton: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        subtests: pytest_subtests.SubTests,
    ):
        """Rotate the BLS key of a block producing pool and check that it keeps voting.

        The ledger swaps the seated key on an epoch boundary, while the node votes with
        whatever `--shelley-bls-key` it was started with. Handing the node both keys as a
        JSON array - the rotation pair - is what bridges that boundary: the node casts a
        vote for every seat any of its keys holds, so it votes with the old key in the
        epoch that still has the old key seated, and with the new one from the next epoch
        on.

        * Wait for the epoch in which the voting committee becomes active
        * Rotate the BLS key of the pool by re-registering it, and restart the node with
          both the old and the new key in one file
        * Check that in the epoch after the rotation the committee still holds the old
          key, and that the pool votes
        * Check that in the epoch after that the committee holds the new key, and that
          the pool still votes
        * Restart the node with the old key alone and check that it declines to vote,
          because the key it holds no longer matches its committee seat
        * Restart the node with the new key alone - the cut over an operator does once
          the rotation is seated - and check that the pool votes again
        """
        cluster = cluster_leios_singleton
        temp_template = common.get_test_id(cluster)

        pool_name = cluster_management.Resources.POOL_FOR_OFFLINE
        node_name = pool_name.replace("node-", "")
        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_id = delegation.get_pool_id(
            cluster_obj=cluster,
            addrs_data=cluster_manager.cache.addrs_data,
            pool_name=pool_name,
        )

        node_bls_key_pair = pool_rec.get("bls_key_pair")
        assert node_bls_key_pair is not None, (
            f"The pool '{pool_name}' was set up without a BLS key pair"
        )

        pool_log = cluster_nodes.get_cluster_env().state_dir / f"{node_name}.stdout"
        assert pool_log.exists(), f"The pool log file '{pool_log}' doesn't exist"

        # The key the node currently runs with. Keep a copy - it goes into the rotation
        # pair, and the node is started with it alone later on.
        orig_skey_file = pl.Path(
            shutil.copy(node_bls_key_pair.skey_file, f"{temp_template}_orig_bls.skey")
        )
        orig_vkey = bls.get_vkey_hex(vkey_file=node_bls_key_pair.vkey_file)
        assert (
            bls.get_bls_pub_key(
                bls_key_state=bls.get_registered_bls_key(cluster_obj=cluster, pool_id=pool_id)
            )
            == orig_vkey
        ), f"The key registered by the pool '{pool_name}' is not the one its node runs with"

        # No pool can vote before the committee is seated for the first time
        cluster.wait_for_epoch(epoch_no=leios.VOTING_START_EPOCH, padding_seconds=5)

        # Restarting the nodes drops the connections between them
        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="MuxBearerClosed",
            ignore_file_id=cluster_manager.worker_id,
        )

        # No `respin_on_failure` here: the cluster fixture already marked the instance
        # for respin, because the pool ends up rotated either way.

        # The rotation and the check that it is still pending must not be split by an
        # epoch boundary
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        rotate_epoch = cluster.g_query.get_epoch()

        new_bls_key_pair = cluster.g_node.gen_bls_key_pair(node_name=f"{temp_template}_new")
        new_vkey = bls.get_vkey_hex(vkey_file=new_bls_key_pair.vkey_file)
        assert new_vkey != orig_vkey, "The generated BLS key is the same as the original one"

        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        # The pool owner pays for the rotation transaction
        faucet.fund_from_faucet(
            pool_owner.payment,
            cluster_obj=cluster,
            all_faucets=cluster_manager.cache.addrs_data,
            amount=900_000_000,
            tx_name=f"{temp_template}_fund_owner",
            force=True,
        )

        reregister_cluster_pool(
            cluster_obj=cluster,
            pool_rec=pool_rec,
            pool_name=pool_name,
            pool_id=pool_id,
            bls_skey_file=new_bls_key_pair.skey_file,
            tx_name=f"{temp_template}_rotate",
        )

        assert cluster.g_query.get_epoch() == rotate_epoch, (
            "The rotation took longer than expected and would affect other checks"
        )
        assert (
            get_future_bls_key(cluster_obj=cluster, pool_id=pool_id).get("blsPubKey") == new_vkey
        ), "The new BLS key is not pending in `futurePoolParams`"

        # Hand the node both keys, so it can vote with whichever one the committee
        # holds. Without the pair it would go dark in one of the next two epochs.
        bundle_file = write_bls_key_bundle(
            key_files=[orig_skey_file, new_bls_key_pair.skey_file],
            out_file=pl.Path(f"{temp_template}_bls_pair.skey"),
        )
        install_node_bls_key(cluster_obj=cluster, key_file=bundle_file, key_pair=node_bls_key_pair)

        pair_errors: list[str] = []
        pair_skip_reasons: list[str] = []

        # The committee of this epoch was seated before the rotation, so the old key
        # is the one that can vote.
        # Overshooting lands in the epoch that already holds the rotated key, where
        # the check below would fail as a wrong key rather than as a missed window.
        this_epoch = cluster.wait_for_epoch(
            epoch_no=rotate_epoch + 1, padding_seconds=5, future_is_ok=False
        )
        seat = get_committee_seat(cluster_obj=cluster, pool_id=pool_id)
        assert bls.get_bls_pub_key(bls_key_state=seat.get("key")) == orig_vkey, (
            f"The Leios committee of epoch {this_epoch} doesn't hold the original BLS key "
            f"of '{pool_name}': {seat}"
        )
        found, problems = search_voting_activity(cluster_obj=cluster, logfile=pool_log)
        check_voted(
            found=found,
            problems=problems,
            errors=pair_errors,
            skip_reasons=pair_skip_reasons,
            description=f"with the original key of the rotation pair in epoch {this_epoch}",
        )

        # One more boundary and the rotated key is the seated one
        this_epoch = cluster.wait_for_epoch(
            epoch_no=rotate_epoch + BLS_ACTIVATION_EPOCHS, padding_seconds=5
        )
        seat = get_committee_seat(cluster_obj=cluster, pool_id=pool_id)
        assert bls.get_bls_pub_key(bls_key_state=seat.get("key")) == new_vkey, (
            f"The Leios committee of epoch {this_epoch} doesn't hold the rotated BLS key "
            f"of '{pool_name}': {seat}"
        )
        found, problems = search_voting_activity(cluster_obj=cluster, logfile=pool_log)
        check_voted(
            found=found,
            problems=problems,
            errors=pair_errors,
            skip_reasons=pair_skip_reasons,
            description=f"with the rotated key of the rotation pair in epoch {this_epoch}",
        )

        # The rotated key stays the seated one from here on, so the two checks below
        # don't depend on an epoch boundary. Both node restarts are done before any
        # of their results is asserted on, so that a failed check cannot leave the
        # node running with a key that is not the registered one.
        install_node_bls_key(
            cluster_obj=cluster, key_file=orig_skey_file, key_pair=node_bls_key_pair
        )
        stale_found, stale_problems = search_voting_activity(
            cluster_obj=cluster, logfile=pool_log, within_epoch=False, full_window=True
        )

        # The cut over an operator does once the rotation is seated: drop the old key
        install_node_bls_key(
            cluster_obj=cluster,
            key_file=new_bls_key_pair.skey_file,
            key_pair=node_bls_key_pair,
            vkey_file=new_bls_key_pair.vkey_file,
        )
        cutover_found, cutover_problems = search_voting_activity(
            cluster_obj=cluster, logfile=pool_log, within_epoch=False
        )

        # Every phase collected its evidence above, so each one reports on its own -
        # an inconclusive rotation pair must not hide the outcome of the two cut overs
        report_subtest(
            subtests=subtests,
            name="rotation pair",
            errors=pair_errors,
            skip_reasons=pair_skip_reasons,
        )

        stale_errors: list[str] = []
        stale_skip_reasons: list[str] = []
        if leios.MSG_VOTED in stale_found:
            stale_errors.append(
                f"The pool '{pool_name}' voted while its node held only the old BLS key, "
                "which no longer matches its committee seat."
            )
        elif not stale_found & frozenset(leios.NOT_VOTED_KEY_MSGS):
            # Without the pool declining to vote there may have been nothing to vote on,
            # and then the absence of votes says nothing
            stale_skip_reasons.append(
                f"The pool '{pool_name}' didn't report declining to vote with a key that "
                "holds no committee seat, so the absence of votes is inconclusive"
            )
            stale_skip_reasons.extend(stale_problems)
        report_subtest(
            subtests=subtests,
            name="old key alone",
            errors=stale_errors,
            skip_reasons=stale_skip_reasons,
        )

        cutover_errors: list[str] = []
        cutover_skip_reasons: list[str] = []
        check_voted(
            found=cutover_found,
            problems=cutover_problems,
            errors=cutover_errors,
            skip_reasons=cutover_skip_reasons,
            description="after the cut over to the rotated key alone",
        )
        report_subtest(
            subtests=subtests,
            name="new key alone",
            errors=cutover_errors,
            skip_reasons=cutover_skip_reasons,
        )
