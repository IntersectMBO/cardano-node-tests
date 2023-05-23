"""Tests for `CARDANO_NODE_NETWORK_ID` environment variable."""
import logging
import os
import time
from typing import Generator
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles

LOGGER = logging.getLogger(__name__)

MISSING_ARG_ERR = "Missing: (--mainnet | --testnet-magic NATURAL)"

POOL_ID = "pool1dlyzfcl25qwjjmpmp47dulwmp2fej8tw4qcezcnkdlsjkak5n89"
STAKE_ADDR = "stake_test1uzy5myemjnne3gr0jp7yhtznxx2lvx4qgv730jktsu46v5gaw7rmt"


PARAM_ENV_SCENARIO = pytest.mark.parametrize(
    "env_scenario",
    (
        "env_missing",
        "env_wrong",
    ),
)
PARAM_ARG_SCENARIO = pytest.mark.parametrize(
    "arg_scenario",
    (
        "arg_missing",
        "arg_wrong",
    ),
)


def _setup_scenarios(
    cluster_obj: clusterlib.ClusterLib, env_scenario: str, arg_scenario: str
) -> None:
    """Set `CARDANO_NODE_NETWORK_ID` env variable and `--testnet-magic` arg."""
    if env_scenario == "env_missing" and os.environ.get("CARDANO_NODE_NETWORK_ID"):
        del os.environ["CARDANO_NODE_NETWORK_ID"]
    elif env_scenario == "env_wrong":
        os.environ["CARDANO_NODE_NETWORK_ID"] = "424242"

    if arg_scenario == "arg_missing":
        cluster_obj.magic_args = []
    elif arg_scenario == "arg_wrong":
        cluster_obj.magic_args = ["--testnet-magic", "424242"]


def _assert_expected_err(env_scenario: str, arg_scenario: str, err_msg: str) -> None:
    """Check expected error message based on scenarios."""
    expected_err = "HandshakeError (Refused NodeToClient"
    if arg_scenario == "arg_missing" and env_scenario == "env_missing":
        expected_err = MISSING_ARG_ERR

    assert expected_err in err_msg, f"{expected_err} not in {err_msg}"


@pytest.fixture
def skip_on_no_env(
    cluster: clusterlib.ClusterLib,
    set_network_id_env: None,  # noqa: ARG001
) -> None:
    """Skip test if `CARDANO_NODE_NETWORK_ID` is not available."""
    # pylint: disable=unused-argument
    try:
        cluster.g_query.get_tip()
    except clusterlib.CLIError as exc:
        if MISSING_ARG_ERR in str(exc):
            pytest.skip("Env variable `CARDANO_NODE_NETWORK_ID` is not available")
        raise


@pytest.fixture
def set_network_id_env(
    cluster: clusterlib.ClusterLib,
) -> Generator[None, None, None]:
    """Set `CARDANO_NODE_NETWORK_ID` and prevent `cardano-cli` from using `--testnet-magic`."""
    magic_args = cluster.magic_args[:]

    os.environ["CARDANO_NODE_NETWORK_ID"] = str(cluster.network_magic)
    cluster.magic_args = []
    yield
    if os.environ.get("CARDANO_NODE_NETWORK_ID"):
        del os.environ["CARDANO_NODE_NETWORK_ID"]
    cluster.magic_args = magic_args


@pytest.fixture
def payment_addrs(
    skip_on_no_env: None,  # noqa: ARG001
    set_network_id_env: None,  # noqa: ARG001
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    # pylint: disable=unused-argument
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            f"addr_network_env_ci{cluster_manager.cluster_instance_num}_0",
            f"addr_network_env_ci{cluster_manager.cluster_instance_num}_1",
            cluster_obj=cluster,
        )
        fixture_cache.value = addrs

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=100_000_000,
    )
    return addrs


@pytest.mark.smoke
@pytest.mark.testnets
class TestNetworkIdEnv:
    """Tests for `CARDANO_NODE_NETWORK_ID`."""

    @allure.link(helpers.get_vcs_link())
    def test_query_protocol_state(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
    ):
        """Test `query protocol-state`."""
        # pylint: disable=unused-argument
        common.get_test_id(cluster)
        state = cluster.g_query.get_protocol_state()
        assert "lastSlot" in state

    @allure.link(helpers.get_vcs_link())
    def test_query_stake_distribution(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
    ):
        """Test `query stake-distribution`."""
        # pylint: disable=unused-argument
        common.get_test_id(cluster)
        distrib = cluster.g_query.get_stake_distribution()
        assert list(distrib)[0].startswith("pool")

    @allure.link(helpers.get_vcs_link())
    def test_query_protocol_params(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
    ):
        """Test `query protocol-parameters`."""
        # pylint: disable=unused-argument
        common.get_test_id(cluster)
        protocol_params = cluster.g_query.get_protocol_params()
        assert "protocolVersion" in protocol_params

    @allure.link(helpers.get_vcs_link())
    def test_query_pool_state(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
    ):
        """Test `query pool-state`."""
        # pylint: disable=unused-argument
        common.get_test_id(cluster)
        cluster.g_query.get_pool_state(stake_pool_id=POOL_ID)

    @allure.link(helpers.get_vcs_link())
    def test_query_stake_addr_info(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
    ):
        """Test `query stake-address-info`."""
        # pylint: disable=unused-argument
        common.get_test_id(cluster)
        cluster.g_query.get_stake_addr_info(stake_addr=STAKE_ADDR)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_transfer_funds(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Send funds to payment address.

        Uses `cardano-cli transaction build` command for building the transactions.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        """
        # pylint: disable=unused-argument
        temp_template = common.get_test_id(cluster)

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        amount = 1_500_000
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        tx_output = cluster.g_transaction.build_tx(
            src_address=src_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=1_000_000,
        )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - amount - tx_output.fee
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"


@pytest.mark.smoke
@pytest.mark.testnets
class TestNegativeNetworkIdEnv:
    """Negative tests for `CARDANO_NODE_NETWORK_ID`."""

    @pytest.fixture
    def ignore_log_errors(self) -> Generator[None, None, None]:
        """Ignore expected handshake errors in the log files."""
        yield
        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="HandshakeError.*Refused NodeToClient",
            ignore_file_id="neg_network_id_env",
            # Ignore errors for next 20 seconds
            skip_after=time.time() + 20,
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_ARG_SCENARIO
    def test_neg_query_protocol_state(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        ignore_log_errors: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        arg_scenario: str,
    ):
        """Test `query protocol-state`.

        Expect failure.
        """
        # pylint: disable=unused-argument
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_protocol_state()

        _assert_expected_err(
            env_scenario=env_scenario, arg_scenario=arg_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_ARG_SCENARIO
    def test_neg_query_stake_distribution(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        ignore_log_errors: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        arg_scenario: str,
    ):
        """Test `query stake-distribution`.

        Expect failure.
        """
        # pylint: disable=unused-argument
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_stake_distribution()
        _assert_expected_err(
            env_scenario=env_scenario, arg_scenario=arg_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_ARG_SCENARIO
    def test_neg_query_protocol_params(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        ignore_log_errors: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        arg_scenario: str,
    ):
        """Test `query protocol-parameters`.

        Expect failure.
        """
        # pylint: disable=unused-argument
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_protocol_params()
        _assert_expected_err(
            env_scenario=env_scenario, arg_scenario=arg_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_ARG_SCENARIO
    def test_neg_query_pool_state(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        ignore_log_errors: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        arg_scenario: str,
    ):
        """Test `query pool-state`.

        Expect failure.
        """
        # pylint: disable=unused-argument
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_pool_state(stake_pool_id=POOL_ID)
        _assert_expected_err(
            env_scenario=env_scenario, arg_scenario=arg_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_ARG_SCENARIO
    def test_neg_query_stake_addr_info(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        ignore_log_errors: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        arg_scenario: str,
    ):
        """Test `query stake-address-info`.

        Expect failure.
        """
        # pylint: disable=unused-argument
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_stake_addr_info(stake_addr=STAKE_ADDR)
        _assert_expected_err(
            env_scenario=env_scenario, arg_scenario=arg_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @PARAM_ENV_SCENARIO
    @PARAM_ARG_SCENARIO
    def test_neg_build_transfer_funds(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        ignore_log_errors: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        env_scenario: str,
        arg_scenario: str,
    ):
        """Send funds to payment address.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        # pylint: disable=unused-argument
        temp_template = f"{common.get_test_id(cluster)}_{env_scenario}_{arg_scenario}"

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        amount = 1_500_000
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=src_addr.address,
                tx_name=temp_template,
                tx_files=tx_files,
                txouts=txouts,
                fee_buffer=1_000_000,
            )
        _assert_expected_err(
            env_scenario=env_scenario, arg_scenario=arg_scenario, err_msg=str(excinfo.value)
        )
