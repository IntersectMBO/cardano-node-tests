"""Tests for `CARDANO_NODE_NETWORK_ID` environment variable."""

import logging
import os
import time
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
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
        cluster_obj.magic_args = ["--cardano-mode"]
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
    try:
        cluster.g_query.get_tip()
    except clusterlib.CLIError as exc:
        if MISSING_ARG_ERR in str(exc):
            pytest.skip("Env variable `CARDANO_NODE_NETWORK_ID` is not available")
        raise


@pytest.fixture
def set_network_id_env(
    cluster: clusterlib.ClusterLib,
) -> tp.Generator[None]:
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
) -> list[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    addrs = common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=2,
        fund_idx=[0],
        caching_key=helpers.get_current_line_str(),
    )
    return addrs


class TestNetworkIdEnv:
    """Tests for `CARDANO_NODE_NETWORK_ID`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_query_protocol_state(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
    ):
        """Query protocol state using CARDANO_NODE_NETWORK_ID environment variable.

        Test that cardano-cli can query protocol state when network is specified via
        CARDANO_NODE_NETWORK_ID env variable instead of --testnet-magic argument.

        * set CARDANO_NODE_NETWORK_ID environment variable to network magic
        * remove --testnet-magic argument from cardano-cli commands
        * execute `cardano-cli query protocol-state` command
        * check that protocol state contains lastSlot field
        * verify query succeeds without --testnet-magic argument
        """
        common.get_test_id(cluster)
        state = cluster.g_query.get_protocol_state()
        assert "lastSlot" in state

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_query_stake_distribution(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
    ):
        """Query stake distribution using CARDANO_NODE_NETWORK_ID environment variable.

        Test that cardano-cli can query stake distribution when network is specified via
        CARDANO_NODE_NETWORK_ID env variable instead of --testnet-magic argument.

        * set CARDANO_NODE_NETWORK_ID environment variable to network magic
        * remove --testnet-magic argument from cardano-cli commands
        * execute `cardano-cli query stake-distribution` command
        * check that distribution contains pool IDs starting with "pool"
        * verify query succeeds without --testnet-magic argument
        """
        common.get_test_id(cluster)
        distrib = cluster.g_query.get_stake_distribution()
        assert next(iter(distrib)).startswith("pool")

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_query_protocol_params(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
    ):
        """Query protocol parameters using CARDANO_NODE_NETWORK_ID environment variable.

        Test that cardano-cli can query protocol parameters when network is specified via
        CARDANO_NODE_NETWORK_ID env variable instead of --testnet-magic argument.

        * set CARDANO_NODE_NETWORK_ID environment variable to network magic
        * remove --testnet-magic argument from cardano-cli commands
        * execute `cardano-cli query protocol-parameters` command
        * check that parameters contain protocolVersion field
        * verify query succeeds without --testnet-magic argument
        """
        common.get_test_id(cluster)
        protocol_params = cluster.g_query.get_protocol_params()
        assert "protocolVersion" in protocol_params

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_query_pool_state(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
    ):
        """Query pool state using CARDANO_NODE_NETWORK_ID environment variable.

        Test that cardano-cli can query pool state when network is specified via
        CARDANO_NODE_NETWORK_ID env variable instead of --testnet-magic argument.

        * set CARDANO_NODE_NETWORK_ID environment variable to network magic
        * remove --testnet-magic argument from cardano-cli commands
        * execute `cardano-cli query pool-state` command for specific pool ID
        * verify query succeeds without --testnet-magic argument
        """
        common.get_test_id(cluster)
        cluster.g_query.get_pool_state(stake_pool_id=POOL_ID)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_query_stake_addr_info(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
    ):
        """Query stake address info using CARDANO_NODE_NETWORK_ID environment variable.

        Test that cardano-cli can query stake address info when network is specified via
        CARDANO_NODE_NETWORK_ID env variable instead of --testnet-magic argument.

        * set CARDANO_NODE_NETWORK_ID environment variable to network magic
        * remove --testnet-magic argument from cardano-cli commands
        * execute `cardano-cli query stake-address-info` command for specific stake address
        * verify query succeeds without --testnet-magic argument
        """
        common.get_test_id(cluster)
        cluster.g_query.get_stake_addr_info(stake_addr=STAKE_ADDR)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_transfer_funds(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Send funds to payment address.

        Uses `cardano-cli transaction build` command for building the transactions.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        """
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


class TestNegativeNetworkIdEnv:
    """Negative tests for `CARDANO_NODE_NETWORK_ID`."""

    def _ignore_log_errors(self) -> None:
        """Ignore expected handshake errors in the log files."""
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
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_neg_query_protocol_state(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        arg_scenario: str,
    ):
        """Query protocol state with incorrect network configuration.

        Test parametrized scenarios where CARDANO_NODE_NETWORK_ID env variable and/or
        --testnet-magic argument are missing or contain wrong values.

        * set CARDANO_NODE_NETWORK_ID to wrong value or remove it (env_scenario parameter)
        * set --testnet-magic to wrong value or remove it (arg_scenario parameter)
        * execute `cardano-cli query protocol-state` command
        * check that command fails with expected error message
        * verify error is HandshakeError when network magic mismatch
        * verify error is "Missing: (--mainnet | --testnet-magic NATURAL)" when both missing

        Expect failure.
        """
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        self._ignore_log_errors()

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_protocol_state()

        _assert_expected_err(
            env_scenario=env_scenario, arg_scenario=arg_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_ARG_SCENARIO
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_neg_query_stake_distribution(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        arg_scenario: str,
    ):
        """Query stake distribution with incorrect network configuration.

        Test parametrized scenarios where CARDANO_NODE_NETWORK_ID env variable and/or
        --testnet-magic argument are missing or contain wrong values.

        * set CARDANO_NODE_NETWORK_ID to wrong value or remove it (env_scenario parameter)
        * set --testnet-magic to wrong value or remove it (arg_scenario parameter)
        * execute `cardano-cli query stake-distribution` command
        * check that command fails with expected error message
        * verify error is HandshakeError when network magic mismatch
        * verify error is "Missing: (--mainnet | --testnet-magic NATURAL)" when both missing

        Expect failure.
        """
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        self._ignore_log_errors()

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_stake_distribution()
        _assert_expected_err(
            env_scenario=env_scenario, arg_scenario=arg_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_ARG_SCENARIO
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_neg_query_protocol_params(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        arg_scenario: str,
    ):
        """Query protocol parameters with incorrect network configuration.

        Test parametrized scenarios where CARDANO_NODE_NETWORK_ID env variable and/or
        --testnet-magic argument are missing or contain wrong values.

        * set CARDANO_NODE_NETWORK_ID to wrong value or remove it (env_scenario parameter)
        * set --testnet-magic to wrong value or remove it (arg_scenario parameter)
        * execute `cardano-cli query protocol-parameters` command
        * check that command fails with expected error message
        * verify error is HandshakeError when network magic mismatch
        * verify error is "Missing: (--mainnet | --testnet-magic NATURAL)" when both missing

        Expect failure.
        """
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        self._ignore_log_errors()

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_protocol_params()
        _assert_expected_err(
            env_scenario=env_scenario, arg_scenario=arg_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_ARG_SCENARIO
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_neg_query_pool_state(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        arg_scenario: str,
    ):
        """Query pool state with incorrect network configuration.

        Test parametrized scenarios where CARDANO_NODE_NETWORK_ID env variable and/or
        --testnet-magic argument are missing or contain wrong values.

        * set CARDANO_NODE_NETWORK_ID to wrong value or remove it (env_scenario parameter)
        * set --testnet-magic to wrong value or remove it (arg_scenario parameter)
        * execute `cardano-cli query pool-state` command for specific pool ID
        * check that command fails with expected error message
        * verify error is HandshakeError when network magic mismatch
        * verify error is "Missing: (--mainnet | --testnet-magic NATURAL)" when both missing

        Expect failure.
        """
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        self._ignore_log_errors()

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_pool_state(stake_pool_id=POOL_ID)
        _assert_expected_err(
            env_scenario=env_scenario, arg_scenario=arg_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_ARG_SCENARIO
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_neg_query_stake_addr_info(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        arg_scenario: str,
    ):
        """Query stake address info with incorrect network configuration.

        Test parametrized scenarios where CARDANO_NODE_NETWORK_ID env variable and/or
        --testnet-magic argument are missing or contain wrong values.

        * set CARDANO_NODE_NETWORK_ID to wrong value or remove it (env_scenario parameter)
        * set --testnet-magic to wrong value or remove it (arg_scenario parameter)
        * execute `cardano-cli query stake-address-info` command for specific stake address
        * check that command fails with expected error message
        * verify error is HandshakeError when network magic mismatch
        * verify error is "Missing: (--mainnet | --testnet-magic NATURAL)" when both missing

        Expect failure.
        """
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        self._ignore_log_errors()

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_stake_addr_info(stake_addr=STAKE_ADDR)
        _assert_expected_err(
            env_scenario=env_scenario, arg_scenario=arg_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @PARAM_ENV_SCENARIO
    @PARAM_ARG_SCENARIO
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_neg_build_transfer_funds(
        self,
        skip_on_no_env: None,  # noqa: ARG002
        set_network_id_env: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        env_scenario: str,
        arg_scenario: str,
    ):
        """Build transfer transaction with incorrect network configuration.

        Test parametrized scenarios where CARDANO_NODE_NETWORK_ID env variable and/or
        --testnet-magic argument are missing or contain wrong values.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create 2 payment addresses and fund first address
        * set CARDANO_NODE_NETWORK_ID to wrong value or remove it (env_scenario parameter)
        * set --testnet-magic to wrong value or remove it (arg_scenario parameter)
        * attempt to build transaction sending 1.5 ADA from source to destination address
        * check that transaction build fails with expected error message
        * verify error is HandshakeError when network magic mismatch
        * verify error is "Missing: (--mainnet | --testnet-magic NATURAL)" when both missing

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, arg_scenario=arg_scenario)

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        amount = 1_500_000
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        self._ignore_log_errors()

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
