import logging
import time
import typing as tp

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import pytest_utils
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


MAX_INT64 = (2**63) - 1
MAX_UINT64 = (2**64) - 1

ORDER5_BYRON = (
    pytest.mark.order(5) if "_fast" not in configuration.SCRIPTS_DIRNAME else pytest.mark.noop
)
LONG_BYRON = pytest.mark.long if "_fast" not in configuration.SCRIPTS_DIRNAME else pytest.mark.noop


_BLD_SKIP_REASON = ""
if VERSIONS.transaction_era != VERSIONS.cluster_era:
    _BLD_SKIP_REASON = "transaction era must be the same as node era"
BUILD_UNUSABLE = bool(_BLD_SKIP_REASON)

# Common `skipif`s
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

SKIPIF_ON_TESTNET = pytest.mark.skipif(
    cluster_nodes.get_cluster_type().type != cluster_nodes.ClusterType.LOCAL,
    reason="not supposed to run on long-running testnet",
)

SKIPIF_ON_LOCAL = pytest.mark.skipif(
    cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL,
    reason="supposed to run on long-running testnet",
)


# Common parametrization
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


# Intervals for `wait_for_epoch_interval` (negative values are counted from the end of an epoch)
if cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL:
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

    # Log test ID to cluster manager log file - getting test ID happens early
    # after the start of a test, so the log entry can be used for determining
    # time of the test start
    cm: cluster_management.ClusterManager = cluster_obj._cluster_manager  # type: ignore
    cm.log(f"c{cm.cluster_instance_num}: got ID `{test_id}` for '{curr_test.full}'")

    return test_id


def get_nodes_missing_utxos(
    cluster_obj: clusterlib.ClusterLib,
    utxos: list[clusterlib.UTXOData],
) -> set[str]:
    """Return set of nodes that don't have the given UTxOs."""
    missing_nodes: set[str] = set()

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
    utxos: list[clusterlib.UTXOData],
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
) -> tuple[set[str], set[str]]:
    """Detect if one or more nodes have forked blockchain or is out of sync."""
    forked_nodes: set[str] = set()
    unsynced_nodes: set[str] = set()

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
        all_faucets=cluster_manager.cache.addrs_data,
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
        # The local cluster needs to be respun before it is usable again
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


def _get_funded_addresses(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    create_func: tp.Callable[[], list],
    fund_idx: list[int] | None = None,
    caching_key: str = "",
    amount: int | None = None,
) -> list:
    """Create and fund addresses."""
    # Initially fund the addresses with more funds, so the funds don't need to be
    # added for each and every test.
    init_amount = 250_000_000
    # Refund the addresses if the amount is lower than this
    min_amount = 50_000_000

    if caching_key:
        fixture_cache: cluster_management.FixtureCache[list | None]
        with cluster_manager.cache_fixture(key=caching_key) as fixture_cache:
            if fixture_cache.value is None:
                addrs = create_func()
                amount = amount or init_amount
                fixture_cache.value = addrs
            else:
                addrs = fixture_cache.value
                # If amount was passed, fund the addresses only initially
                if amount:
                    return addrs

    else:
        addrs = create_func()
        amount = amount or init_amount

    # Fund source addresses
    fund_addrs = addrs if fund_idx is None else [addrs[i] for i in fund_idx]
    if fund_addrs:
        amount = amount or min_amount
        clusterlib_utils.fund_from_faucet(
            *fund_addrs,
            cluster_obj=cluster_obj,
            all_faucets=cluster_manager.cache.addrs_data,
            amount=amount,
        )

    return addrs


def get_payment_addrs(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    num: int,
    fund_idx: list[int] | None = None,
    caching_key: str = "",
    amount: int | None = None,
) -> list[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    if num < 1:
        err = f"Number of addresses must be at least 1, got: {num}"
        raise ValueError(err)

    def _create_addrs() -> list[clusterlib.AddressRecord]:
        addrs = clusterlib_utils.create_payment_addr_records(
            *[f"{name_template}_fund_addr_{i}" for i in range(1, num + 1)],
            cluster_obj=cluster_obj,
        )
        return addrs

    return _get_funded_addresses(
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        create_func=_create_addrs,
        fund_idx=fund_idx,
        caching_key=caching_key,
        amount=amount,
    )


def get_payment_addr(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    caching_key: str = "",
    amount: int | None = None,
) -> clusterlib.AddressRecord:
    """Create a single new payment address."""
    return get_payment_addrs(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        num=1,
        caching_key=caching_key,
        amount=amount,
    )[0]


def get_pool_users(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    num: int,
    fund_idx: list[int] | None = None,
    caching_key: str = "",
    amount: int | None = None,
) -> list[clusterlib.PoolUser]:
    """Create new pool users."""
    if num < 1:
        err = f"Number of pool users must be at least 1, got: {num}"
        raise ValueError(err)

    def _create_pool_users() -> list[clusterlib.PoolUser]:
        users = clusterlib_utils.create_pool_users(
            cluster_obj=cluster_obj,
            name_template=f"{name_template}_pool_user",
            no_of_addr=num,
        )
        return users

    return _get_funded_addresses(
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        create_func=_create_pool_users,
        fund_idx=fund_idx,
        caching_key=caching_key,
        amount=amount,
    )


def get_pool_user(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    caching_key: str = "",
    amount: int | None = None,
) -> clusterlib.PoolUser:
    """Create a single new pool user."""
    return get_pool_users(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        num=1,
        caching_key=caching_key,
        amount=amount,
    )[0]


def get_registered_pool_user(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    caching_key: str = "",
    amount: int | None = None,
) -> clusterlib.PoolUser:
    """Create new registered pool users."""
    pool_user = get_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        caching_key=caching_key,
        amount=amount,
    )

    if not cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address):
        # Register the stake address
        clusterlib_utils.register_stake_address(
            cluster_obj=cluster_obj,
            pool_user=pool_user,
            name_template=f"{name_template}_pool_user",
            deposit_amt=cluster_obj.g_query.get_address_deposit(),
        )

    return pool_user


def is_fee_in_interval(fee: float, expected_fee: float, frac: float = 0.1) -> bool:
    """Check that the fee is within the expected range on local testnet.

    The fee is considered to be within the expected range if it is within the expected_fee +/- frac
    range.
    """
    # We have the fees calibrated only for local testnet
    if cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.TESTNET:
        return True
    return helpers.is_in_interval(fee, expected_fee, frac=frac)
