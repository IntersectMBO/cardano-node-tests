import contextlib
import logging
import pathlib as pl
import string
import time
import typing as tp

from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import artifacts
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import pytest_utils

LOGGER = logging.getLogger(__name__)

DATA_DIR = pl.Path(__file__).parent / "data"

COST_PROPOSAL_FILE = DATA_DIR / "cost_models_list_332_350_v2_v3.json"

# Layout of a stake pool registration certificate, which is a CBOR array. The Dijkstra
# certificate has one item more than the Conway one - the BLS key with its proof of possession.
POOL_REG_CERT_CONWAY_ITEMS = 10
POOL_REG_CERT_DIJKSTRA_ITEMS = 11
POOL_REG_CERT_BLS_IX = 3

MAX_INT64 = (2**63) - 1
MAX_UINT64 = (2**64) - 1

ADDR_ALPHABET = string.ascii_lowercase + string.digits

# Publicly-hosted metadata/anchor URLs, used by both db-sync and cardano-cli
# whenever a test needs a publicly accessible URL (for pool metadata, DRep
# metadata or governance action anchors) rather than a cluster-local one. Each
# URL below points to the equivalent file under ``data/`` served from the public
# repo. Keep the raw-content paths short: some consumers cap the URL at 128 bytes.

# db-sync's off-chain fetcher rejects URLs whose host is one of localhost,
# 127.0.0.1, ::1, 10.*, or 192.168.* (see ``parseOffChainUrl`` in
# ``cardano-db-sync/src/Cardano/DbSync/OffChain/Http.hs``), so the cluster-local
# ``http://localhost:...`` URLs always fail and this public equivalent is needed.
PUBLIC_POOL_METADATA_URL = (
    "https://raw.githubusercontent.com/IntersectMBO/cardano-node-tests/master"
    "/cardano_node_tests/tests/data/pool_metadata.json"
)
PUBLIC_DREP_METADATA_URL = (
    "https://raw.githubusercontent.com/IntersectMBO/cardano-node-tests/master"
    "/cardano_node_tests/tests/data/drep_metadata_url.json"
)
PUBLIC_DREP_METADATA_IPFS = (
    "https://peach-accused-sloth-87.mypinata.cloud/ipfs"
    "/bafkreigphrowsgabffhrhhlnf3fufoxiomzm56qyynlxtbgbbpq2xpqwxa"
)
PUBLIC_ACTION_ANCHOR_URL = (
    "https://raw.githubusercontent.com/IntersectMBO/cardano-node-tests/master"
    "/cardano_node_tests/tests/data/ga_anchor.json"
)
# Off-chain voting anchor test vectors (valid-but-non-CIP and invalid-JSON). Short file names
# keep the raw-content URL under the 128-byte on-chain anchor limit. Like the URLs above, these
# resolve only once the data files land on the master branch.
PUBLIC_ACTION_ANCHOR_NONCONFORMANT_URL = (
    "https://raw.githubusercontent.com/IntersectMBO/cardano-node-tests/master"
    "/cardano_node_tests/tests/data/ga_anchor_nonconf.json"
)
PUBLIC_ACTION_ANCHOR_INVALID_URL = (
    "https://raw.githubusercontent.com/IntersectMBO/cardano-node-tests/master"
    "/cardano_node_tests/tests/data/ga_anchor_invalid.json"
)


# Intervals for `wait_for_epoch_interval` (negative values are counted from the end of an epoch)
if cluster_nodes.get_cluster_type().is_local:
    # Time buffer at the end of an epoch, enough to do something that takes several transactions
    EPOCH_STOP_SEC_BUFFER = -40
    # Time when all ledger state info is available for the current epoch
    EPOCH_START_SEC_LEDGER_STATE = -19
    # Time buffer at the end of an epoch after getting ledger state info
    EPOCH_STOP_SEC_LEDGER_STATE = -15
else:
    # We can be more generous on testnets
    EPOCH_STOP_SEC_BUFFER = -200
    EPOCH_START_SEC_LEDGER_STATE = -300
    EPOCH_STOP_SEC_LEDGER_STATE = -200


def hypothesis_settings(max_examples: int = 100) -> tp.Any:
    import hypothesis  # noqa: PLC0415

    return hypothesis.settings(
        max_examples=max_examples,
        deadline=None,
        suppress_health_check=(
            hypothesis.HealthCheck.too_slow,
            hypothesis.HealthCheck.function_scoped_fixture,
            hypothesis.HealthCheck.filter_too_much,
        ),
    )


def unique_time_str() -> str:
    """Return unique string based on current timestamp.

    Useful for property-based tests as it isn't possible to use `random` module in hypothesis tests.
    """
    return str(time.time()).replace(".", "")[-8:]


def get_test_id(
    cluster_or_manager: clusterlib.ClusterLib | cluster_management.ClusterManager,
) -> str:
    """Return unique test ID - function name + assigned cluster instance + random string.

    Log the test ID into cluster manager log file.
    """
    if isinstance(cluster_or_manager, clusterlib.ClusterLib):
        cid_part = f"_ci{cluster_or_manager.cluster_id}"
        cm: cluster_management.ClusterManager = cluster_or_manager._cluster_manager  # type: ignore
    else:
        cid_part = ""
        cm = cluster_or_manager

    cinstance = str(cm._cluster_instance_num) if cm._cluster_instance_num != -1 else ""

    curr_test = pytest_utils.get_current_test()
    rand_str = clusterlib.get_rand_str(6)
    test_id = f"{curr_test.test_function}{curr_test.test_params}{cid_part}_{rand_str}"

    # Log test ID to cluster manager log file - getting test ID happens early
    # after the start of a test, so the log entry can be used for determining
    # time of the test start
    cm.log(f"c{cinstance}: got ID `{test_id}` for '{curr_test.full}'")

    return test_id


def get_fixture_cluster_obj(
    *, request: FixtureRequest, command_era: str = ""
) -> clusterlib.ClusterLib:
    """Create a `ClusterLib` instance for a test fixture and save its CLI coverage.

    Intended for test code only - it must be called from a pytest fixture, as it uses the
    fixture request for registering the teardown that saves the CLI coverage. Don't use it
    in framework code outside of fixtures.

    Use when a test needs its own `ClusterLib` instance, e.g. an instance that uses
    a different command era than the one provided by the `cluster` fixture. CLI coverage of
    instances created by `ClusterManager` is saved by the manager itself.

    Args:
        request: A pytest fixture request, used for registering the teardown and for
            accessing the pytest config.
        command_era: An era name to be used for CLI commands.

    Returns:
        A new `ClusterLib` instance.
    """
    cluster_obj = cluster_nodes.get_cluster_type().get_cluster_obj(command_era=command_era)
    request.addfinalizer(
        lambda: artifacts.save_cli_coverage(cluster_obj=cluster_obj, pytest_config=request.config)
    )
    return cluster_obj


def match_blocker(func: tp.Callable) -> tp.Any:
    """Fail or Xfail the test if CLI error is raised."""
    try:
        ret = func()
    except clusterlib.CLIError as exc:
        str_exc = str(exc)

        if (
            " transaction build " in str_exc
            and "fromConsensusQueryResult: internal query mismatch" in str_exc
            and "--certificate-file" in str_exc
        ):
            issues.cli_268.finish_test()
        raise

    return ret


def is_fee_in_interval(fee: float, expected_fee: float, frac: float = 0.1) -> bool:
    """Check that the fee is within the expected range on local testnet.

    The fee is considered to be within the expected range if it is within the expected_fee +/- frac
    range.
    """
    # We have the fees calibrated only for local testnet
    if cluster_nodes.get_cluster_type().is_testnet:
        return True
    return helpers.is_in_interval(fee, expected_fee, frac=frac)


@contextlib.contextmanager
def allow_unstable_error_messages() -> tp.Iterator[None]:
    """Catch AssertionError and either log it or raise it.

    Used in tests where error messages can vary between node/CLI versions.
    """
    if not configuration.ALLOW_UNSTABLE_ERROR_MESSAGES:
        yield
        return

    try:
        yield
    except AssertionError:
        LOGGER.exception("AssertionError suppressed")
