"""Tests for `--socket-path` CLI argument."""

import logging
import os
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


POOL_ID = "pool1dlyzfcl25qwjjmpmp47dulwmp2fej8tw4qcezcnkdlsjkak5n89"
STAKE_ADDR = "stake_test1uzy5myemjnne3gr0jp7yhtznxx2lvx4qgv730jktsu46v5gaw7rmt"


PARAM_ENV_SCENARIO = pytest.mark.parametrize(
    "env_scenario",
    (
        "env_missing",
        "env_wrong",
    ),
)
PARAM_SOCKET_SCENARIO = pytest.mark.parametrize(
    "socket_scenario",
    (
        "socket_path_missing",
        "socket_path_wrong",
    ),
)


def _setup_scenarios(
    cluster_obj: clusterlib.ClusterLib, env_scenario: str, socket_scenario: str
) -> None:
    """Set `CARDANO_NODE_SOCKET_PATH` env variable and `--socket-path` arg."""
    if env_scenario == "env_missing" and os.environ.get("CARDANO_NODE_SOCKET_PATH"):
        del os.environ["CARDANO_NODE_SOCKET_PATH"]
    elif env_scenario == "env_wrong":
        os.environ["CARDANO_NODE_SOCKET_PATH"] = "/nonexistent"

    if socket_scenario == "socket_path_missing":
        cluster_obj.set_socket_path(socket_path=None)
    elif socket_scenario == "socket_path_wrong" and cluster_obj.socket_args:
        cluster_obj.socket_args[-1] = "/nonexistent"


def _assert_expected_err(env_scenario: str, socket_scenario: str, err_msg: str) -> None:
    """Check expected error message based on scenarios."""
    expected_err = ["Network.Socket.connect:"]
    if socket_scenario == "socket_path_missing" and env_scenario == "env_missing":
        expected_err = [
            "Missing: --socket-path SOCKET_PATH",
            # TODO: In 8.0.0-untested the error message is different:
            "Error while looking up environment variable: CARDANO_NODE_SOCKET_PATH",
        ]

    assert any(msg in err_msg for msg in expected_err), f"{expected_err} not in {err_msg}"


@pytest.fixture
def set_socket_path(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> tp.Generator[None, None, None]:
    """Unset `CARDANO_NODE_SOCKET_PATH` and set path for `cardano-cli ... --socket-path`."""
    if os.environ.get("CARDANO_NODE_SOCKET_PATH"):
        del os.environ["CARDANO_NODE_SOCKET_PATH"]

    socket_path = cluster_nodes.get_cardano_node_socket_path(
        instance_num=cluster_manager.cluster_instance_num
    )
    cluster.set_socket_path(socket_path=socket_path)

    yield

    os.environ["CARDANO_NODE_SOCKET_PATH"] = str(socket_path)
    cluster.set_socket_path(socket_path=cluster._init_socket_path)


@pytest.fixture
def payment_addrs(
    set_socket_path: None,  # noqa: ARG001
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


class TestSocketPath:
    """Tests for `cardano-cli ... --socket-path`."""

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_query_protocol_state(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
    ):
        """Test `query protocol-state`."""
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, socket_scenario="")

        state = cluster.g_query.get_protocol_state()
        assert "lastSlot" in state

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_query_stake_distribution(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
    ):
        """Test `query stake-distribution`."""
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, socket_scenario="")

        distrib = cluster.g_query.get_stake_distribution()
        assert next(iter(distrib)).startswith("pool")

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_query_protocol_params(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
    ):
        """Test `query protocol-parameters`."""
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, socket_scenario="")

        protocol_params = cluster.g_query.get_protocol_params()
        assert "protocolVersion" in protocol_params

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_query_pool_state(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
    ):
        """Test `query pool-state`."""
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, socket_scenario="")

        cluster.g_query.get_pool_state(stake_pool_id=POOL_ID)

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_query_stake_addr_info(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
    ):
        """Test `query stake-address-info`."""
        common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, socket_scenario="")

        cluster.g_query.get_stake_addr_info(stake_addr=STAKE_ADDR)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @PARAM_ENV_SCENARIO
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_transfer_funds(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        env_scenario: str,
    ):
        """Send funds to payment address.

        Uses `cardano-cli transaction build` command for building the transactions.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        """
        temp_template = common.get_test_id(cluster)

        _setup_scenarios(cluster_obj=cluster, env_scenario=env_scenario, socket_scenario="")

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        amount = 1_500_000
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        try:
            tx_output = cluster.g_transaction.build_tx(
                src_address=src_addr.address,
                tx_name=temp_template,
                tx_files=tx_files,
                txouts=txouts,
                fee_buffer=1_000_000,
            )
        except clusterlib.CLIError as exc:
            str_exc = str(exc)
            if (
                "Error while looking up environment variable: CARDANO_NODE_SOCKET_PATH" in str_exc
                or "Network.Socket.connect:" in str_exc
            ):
                issues.node_5199.finish_test()
            raise

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


class TestNegativeSocketPath:
    """Negative tests for `cardano-cli ... --socket-path`."""

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_SOCKET_SCENARIO
    def test_neg_query_protocol_state(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        socket_scenario: str,
    ):
        """Test `query protocol-state`.

        Expect failure.
        """
        common.get_test_id(cluster)

        _setup_scenarios(
            cluster_obj=cluster, env_scenario=env_scenario, socket_scenario=socket_scenario
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_protocol_state()
        _assert_expected_err(
            env_scenario=env_scenario, socket_scenario=socket_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_SOCKET_SCENARIO
    def test_neg_query_stake_distribution(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        socket_scenario: str,
    ):
        """Test `query stake-distribution`.

        Expect failure.
        """
        common.get_test_id(cluster)

        _setup_scenarios(
            cluster_obj=cluster, env_scenario=env_scenario, socket_scenario=socket_scenario
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_stake_distribution()
        _assert_expected_err(
            env_scenario=env_scenario, socket_scenario=socket_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_SOCKET_SCENARIO
    def test_neg_query_protocol_params(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        socket_scenario: str,
    ):
        """Test `query protocol-parameters`.

        Expect failure.
        """
        common.get_test_id(cluster)

        _setup_scenarios(
            cluster_obj=cluster, env_scenario=env_scenario, socket_scenario=socket_scenario
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_protocol_params()
        _assert_expected_err(
            env_scenario=env_scenario, socket_scenario=socket_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_SOCKET_SCENARIO
    def test_neg_query_pool_state(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        socket_scenario: str,
    ):
        """Test `query pool-state`.

        Expect failure.
        """
        common.get_test_id(cluster)

        _setup_scenarios(
            cluster_obj=cluster, env_scenario=env_scenario, socket_scenario=socket_scenario
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_pool_state(stake_pool_id=POOL_ID)
        _assert_expected_err(
            env_scenario=env_scenario, socket_scenario=socket_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @PARAM_ENV_SCENARIO
    @PARAM_SOCKET_SCENARIO
    def test_neg_query_stake_addr_info(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        env_scenario: str,
        socket_scenario: str,
    ):
        """Test `query stake-address-info`.

        Expect failure.
        """
        common.get_test_id(cluster)

        _setup_scenarios(
            cluster_obj=cluster, env_scenario=env_scenario, socket_scenario=socket_scenario
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_stake_addr_info(stake_addr=STAKE_ADDR)
        _assert_expected_err(
            env_scenario=env_scenario, socket_scenario=socket_scenario, err_msg=str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @PARAM_ENV_SCENARIO
    @PARAM_SOCKET_SCENARIO
    def test_neg_build_transfer_funds(
        self,
        set_socket_path: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        env_scenario: str,
        socket_scenario: str,
    ):
        """Send funds to payment address.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        _setup_scenarios(
            cluster_obj=cluster, env_scenario=env_scenario, socket_scenario=socket_scenario
        )

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
            env_scenario=env_scenario, socket_scenario=socket_scenario, err_msg=str(excinfo.value)
        )
