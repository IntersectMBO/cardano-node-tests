import logging
import time
import typing as tp

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import pytest_utils
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


MAX_INT64 = (2**63) - 1
MAX_UINT64 = (2**64) - 1


_BLD_SKIP_REASON = ""
if VERSIONS.transaction_era != VERSIONS.cluster_era:
    _BLD_SKIP_REASON = "transaction era must be the same as node era"
BUILD_UNUSABLE = bool(_BLD_SKIP_REASON)

# common `skipif`s
SKIPIF_BUILD_UNUSABLE = pytest.mark.skipif(
    BUILD_UNUSABLE,
    reason=(
        f"cannot use `build` with Tx era '{VERSIONS.transaction_era_name}': {_BLD_SKIP_REASON}"
    ),
)

SKIPIF_MISMATCHED_ERAS = pytest.mark.skipif(
    VERSIONS.transaction_era != VERSIONS.cluster_era,
    reason="transaction era must be the same as node era",
)

SKIPIF_WRONG_ERA = pytest.mark.skipif(
    not (
        VERSIONS.cluster_era >= VERSIONS.DEFAULT_CLUSTER_ERA
        and VERSIONS.transaction_era == VERSIONS.cluster_era
    ),
    reason="meant to run with default era or higher, where cluster era == Tx era",
)

SKIPIF_TOKENS_UNUSABLE = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY,
    reason="native tokens are available only in Mary+ eras",
)

_PLUTUS_SKIP_REASON = ""
if VERSIONS.transaction_era < VERSIONS.ALONZO:
    _PLUTUS_SKIP_REASON = "Plutus is available only in Alonzo+ eras"
SKIPIF_PLUTUS_UNUSABLE = pytest.mark.skipif(
    bool(_PLUTUS_SKIP_REASON),
    reason=_PLUTUS_SKIP_REASON,
)

SKIPIF_PLUTUSV2_UNUSABLE = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.BABBAGE,
    reason="Plutus V2 is available only in Babbage+ eras",
)

_PLUTUSV3_SKIP_REASON = ""
if VERSIONS.transaction_era < VERSIONS.CONWAY:
    _PLUTUSV3_SKIP_REASON = "Plutus V3 is available only in Conway+ eras"
PLUTUSV3_UNUSABLE = bool(_PLUTUSV3_SKIP_REASON)
SKIPIF_PLUTUSV3_UNUSABLE = pytest.mark.skipif(
    PLUTUSV3_UNUSABLE,
    reason=_PLUTUSV3_SKIP_REASON,
)


# common parametrization
PARAM_USE_BUILD_CMD = pytest.mark.parametrize(
    "use_build_cmd",
    (
        False,
        pytest.param(True, marks=SKIPIF_BUILD_UNUSABLE),
    ),
    ids=("build_raw", "build"),
)

PARAM_PLUTUS_VERSION = pytest.mark.parametrize(
    "plutus_version",
    (
        "v1",
        pytest.param("v2", marks=SKIPIF_PLUTUSV2_UNUSABLE),
    ),
    ids=("plutus_v1", "plutus_v2"),
)

PARAM_PLUTUS3_VERSION = pytest.mark.parametrize(
    "plutus_version",
    (
        "v1",
        pytest.param("v2", marks=SKIPIF_PLUTUSV2_UNUSABLE),
        pytest.param("v3", marks=SKIPIF_PLUTUSV3_UNUSABLE),
    ),
    ids=("plutus_v1", "plutus_v2", "plutus_v3"),
)

PARAM_PLUTUS2ONWARDS_VERSION = pytest.mark.parametrize(
    "plutus_version",
    (
        "v2",
        pytest.param("v3", marks=SKIPIF_PLUTUSV3_UNUSABLE),
    ),
    ids=("plutus_v2", "plutus_v3"),
)


# intervals for `wait_for_epoch_interval` (negative values are counted from the end of an epoch)
if cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL:
    # time buffer at the end of an epoch, enough to do something that takes several transactions
    EPOCH_STOP_SEC_BUFFER = -40
    # time when all ledger state info is available for the current epoch
    EPOCH_START_SEC_LEDGER_STATE = -19
    # time buffer at the end of an epoch after getting ledger state info
    EPOCH_STOP_SEC_LEDGER_STATE = -15
else:
    # we can be more generous on testnets
    EPOCH_STOP_SEC_BUFFER = -200
    EPOCH_START_SEC_LEDGER_STATE = -300
    EPOCH_STOP_SEC_LEDGER_STATE = -200


def hypothesis_settings(max_examples: int = 100) -> tp.Any:
    # pylint: disable=import-outside-toplevel
    import hypothesis

    return hypothesis.settings(
        max_examples=max_examples,
        deadline=None,
        suppress_health_check=(
            hypothesis.HealthCheck.too_slow,
            hypothesis.HealthCheck.function_scoped_fixture,
        ),
    )


def unique_time_str() -> str:
    """Return unique string based on current timestamp.

    Useful for property-based tests as it isn't possible to use `random` module in hypothesis tests.
    """
    return str(time.time()).replace(".", "")[-8:]


def get_test_id(cluster_obj: clusterlib.ClusterLib) -> str:
    """Return unique test ID - function name + assigned cluster instance + random string.

    Log the test ID into cluster manager log file.
    """
    curr_test = pytest_utils.get_current_test()
    rand_str = clusterlib.get_rand_str(3)
    test_id = (
        f"{curr_test.test_function}{curr_test.test_params}_ci{cluster_obj.cluster_id}_{rand_str}"
    )

    # log test ID to cluster manager log file - getting test ID happens early
    # after the start of a test, so the log entry can be used for determining
    # time of the test start
    cm: cluster_management.ClusterManager = cluster_obj._cluster_manager  # type: ignore
    cm.log(f"c{cm.cluster_instance_num}: got ID `{test_id}` for '{curr_test.full}'")

    return test_id


def get_nodes_missing_utxos(
    cluster_obj: clusterlib.ClusterLib,
    utxos: tp.List[clusterlib.UTXOData],
) -> tp.Set[str]:
    """Return set of nodes that don't have the given UTxOs."""
    missing_nodes: tp.Set[str] = set()

    known_nodes = cluster_nodes.get_cluster_type().NODES
    # Skip the check if there is only one node
    if len(known_nodes) <= 1:
        return missing_nodes

    instance_num = cluster_nodes.get_instance_num()

    # Check if all nodes know about the UTxO
    try:
        for node in known_nodes:
            # Set 'CARDANO_NODE_SOCKET_PATH' to point to socket of the selected node
            cluster_nodes.set_cluster_env(
                instance_num=instance_num, socket_file_name=f"{node}.socket"
            )

            if not cluster_obj.g_query.get_utxo(utxo=utxos):
                missing_nodes.add(node)
    finally:
        # Restore 'CARDANO_NODE_SOCKET_PATH' to original value
        cluster_nodes.set_cluster_env(instance_num=instance_num)

    return missing_nodes


def check_missing_utxos(
    cluster_obj: clusterlib.ClusterLib,
    utxos: tp.List[clusterlib.UTXOData],
) -> None:
    """Fail if any node is missing the given UTxOs."""
    missing_nodes = get_nodes_missing_utxos(cluster_obj=cluster_obj, utxos=utxos)

    if missing_nodes:
        msg = f"Following nodes are missing the given UTxOs: {sorted(missing_nodes)}"
        raise AssertionError(msg)


def detect_fork(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
) -> tp.Tuple[tp.Set[str], tp.Set[str]]:
    """Detect if one or more nodes have forked blockchain or is out of sync."""
    forked_nodes: tp.Set[str] = set()
    unsynced_nodes: tp.Set[str] = set()

    known_nodes = cluster_nodes.get_cluster_type().NODES
    if len(known_nodes) <= 1:
        LOGGER.warning("WARNING: Not enough nodes available to detect forks, skipping the check.")
        return forked_nodes, unsynced_nodes

    instance_num = cluster_nodes.get_instance_num()

    # Create a UTxO
    payment_rec = cluster_obj.g_address.gen_payment_addr_and_keys(
        name=temp_template,
    )
    tx_raw_output = clusterlib_utils.fund_from_faucet(
        payment_rec,
        cluster_obj=cluster_obj,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=2_000_000,
    )
    assert tx_raw_output
    utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_raw_output)

    # Check if all nodes know about the UTxO
    try:
        for node in known_nodes:
            # Set 'CARDANO_NODE_SOCKET_PATH' to point to socket of the selected node
            cluster_nodes.set_cluster_env(
                instance_num=instance_num, socket_file_name=f"{node}.socket"
            )

            for __ in range(5):
                if float(cluster_obj.g_query.get_tip()["syncProgress"]) == 100:
                    break
                time.sleep(1)
            else:
                unsynced_nodes.add(node)
                continue

            if not cluster_obj.g_query.get_utxo(utxo=utxos):
                forked_nodes.add(node)
    finally:
        # Restore 'CARDANO_NODE_SOCKET_PATH' to original value
        cluster_nodes.set_cluster_env(instance_num=instance_num)

    # Forked nodes are the ones that differ from the majority of nodes
    if forked_nodes and len(forked_nodes) > (len(known_nodes) // 2):
        forked_nodes = known_nodes - forked_nodes

    return forked_nodes, unsynced_nodes


def fail_on_fork(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
) -> None:
    """Fail if one or more nodes have forked blockchain or is out of sync."""
    forked_nodes, unsynced_nodes = detect_fork(
        cluster_manager=cluster_manager, cluster_obj=cluster_obj, temp_template=temp_template
    )

    err_msg = []

    if forked_nodes:
        err_msg.append(f"Following nodes appear to have forked blockchain: {sorted(forked_nodes)}")
    if unsynced_nodes:
        err_msg.append(f"Following nodes appear to be out of sync: {sorted(unsynced_nodes)}")

    if err_msg:
        # the local cluster needs to be respun before it is usable again
        cluster_manager.set_needs_respin()
        raise AssertionError("\n".join(err_msg))


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


def get_conway_address_deposit(cluster_obj: clusterlib.ClusterLib) -> int:
    """Get stake address deposit amount - is required in Conway+."""
    stake_deposit_amt = -1
    if VERSIONS.transaction_era >= VERSIONS.CONWAY:
        stake_deposit_amt = cluster_obj.g_query.get_address_deposit()

    return stake_deposit_amt
