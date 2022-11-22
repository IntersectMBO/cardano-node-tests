"""Negative tests for transactions.

Tests like duplicated transaction, sending funds to wrong addresses, wrong fee, wrong ttl.
"""
import logging
import re
import string
import time
from typing import List

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import tx_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

ADDR_ALPHABET = list(f"{string.ascii_lowercase}{string.digits}")


@pytest.mark.testnets
@pytest.mark.smoke
class TestNegative:
    """Transaction tests that are expected to fail."""

    # pylint: disable=too-many-public-methods

    @pytest.fixture(scope="class")
    def skip_on_last_era(self) -> None:
        if VERSIONS.cluster_era == VERSIONS.LAST_KNOWN_ERA:
            pytest.skip(
                f"doesn't run with the latest cluster era ({VERSIONS.cluster_era_name})",
            )

    @pytest.fixture(scope="class")
    def skip_unknown_last_era(self) -> None:
        last_known_era_name = VERSIONS.MAP[VERSIONS.LAST_KNOWN_ERA]
        if not clusterlib_utils.cli_has(f"transaction build-raw --{last_known_era_name}-era"):
            pytest.skip(
                f"`transaction build-raw --{last_known_era_name}-era` command is not available"
            )

    @pytest.fixture
    def cluster_wrong_tx_era(
        self,
        skip_on_last_era: None,
        skip_unknown_last_era: None,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.ClusterLib:
        # pylint: disable=unused-argument
        # the `cluster` argument (representing the `cluster` fixture) needs to be present
        # in order to have an actual cluster instance assigned at the time this fixture
        # is executed
        return cluster_nodes.get_cluster_type().get_cluster_obj(
            tx_era=VERSIONS.MAP[VERSIONS.cluster_era + 1]
        )

    @pytest.fixture
    def pool_users(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.PoolUser]:
        """Create pool users."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            created_users = clusterlib_utils.create_pool_users(
                cluster_obj=cluster,
                name_template=f"test_negative_ci{cluster_manager.cluster_instance_num}",
                no_of_addr=3,
            )
            fixture_cache.value = created_users

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *created_users,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return created_users

    def _send_funds_to_invalid_address(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
        use_build_cmd=False,
    ):
        """Send funds from payment address to invalid address."""
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=addr, amount=1_000_000)]

        # it should NOT be possible to build a transaction using an invalid address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                cluster_obj.g_transaction.build_tx(
                    src_address=pool_users[0].payment.address,
                    tx_name="to_invalid",
                    txouts=destinations,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                cluster_obj.g_transaction.build_raw_tx(
                    src_address=pool_users[0].payment.address,
                    tx_name="to_invalid",
                    txouts=destinations,
                    tx_files=tx_files,
                    fee=0,
                )
        exc_val = str(excinfo.value)
        assert "invalid address" in exc_val or "An error occurred" in exc_val  # TODO: better match

    def _send_funds_from_invalid_address(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
        use_build_cmd=False,
    ):
        """Send funds from invalid payment address."""
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_000_000)]

        # it should NOT be possible to build a transaction using an invalid address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                cluster_obj.g_transaction.build_tx(
                    src_address=addr,
                    tx_name="from_invalid",
                    txouts=destinations,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                cluster_obj.g_transaction.build_raw_tx(
                    src_address=addr,
                    tx_name="from_invalid",
                    txouts=destinations,
                    tx_files=tx_files,
                    fee=0,
                )
        assert "invalid address" in str(excinfo.value)

    def _send_funds_invalid_change_address(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Send funds with invalid change address."""
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_000_000)]

        # it should NOT be possible to build a transaction using an invalid change address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.g_transaction.build_tx(
                src_address=pool_users[0].payment.address,
                tx_name="invalid_change",
                txouts=destinations,
                change_address=addr,
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        assert "invalid address" in str(excinfo.value)

    def _send_funds_with_invalid_utxo(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        utxo: clusterlib.UTXOData,
        temp_template: str,
        use_build_cmd=False,
    ) -> str:
        """Send funds with invalid UTxO."""
        src_addr = pool_users[0].payment
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_000_000)]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                cluster_obj.g_transaction.build_tx(
                    src_address=src_addr.address,
                    tx_name=temp_template,
                    txins=[utxo],
                    txouts=destinations,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                cluster_obj.g_transaction.send_tx(
                    src_address=src_addr.address,
                    tx_name=temp_template,
                    txins=[utxo],
                    txouts=destinations,
                    tx_files=tx_files,
                )
        return str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_past_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send a transaction with ttl in the past.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]
        ttl = cluster.g_query.get_slot_no() - 1
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            ttl=ttl,
        )

        # it should be possible to build and sign a transaction with ttl in the past
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # it should NOT be possible to submit a transaction with ttl in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(out_file_signed)
        exc_val = str(excinfo.value)
        assert "ExpiredUTxO" in exc_val or "ValidityIntervalUTxO" in exc_val

    @allure.link(helpers.get_vcs_link())
    def test_duplicated_tx(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send an identical transaction twice.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # build and sign a transaction
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # submit a transaction for the first time
        cluster.g_transaction.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        # it should NOT be possible to submit a transaction twice
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(out_file_signed)
        assert "ValueNotConservedUTxO" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_wrong_network_magic(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        worker_id: str,
    ):
        """Try to submit a TX with wrong network magic.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # build and sign a transaction
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # it should NOT be possible to submit a transaction with incorrect network magic
        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="HandshakeError",
            ignore_file_id=worker_id,
        )
        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="NodeToClientVersionData",
            ignore_file_id=worker_id,
        )
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "submit",
                    "--testnet-magic",
                    str(cluster.network_magic + 100),
                    "--tx-file",
                    str(out_file_signed),
                    f"--{cluster.protocol}-mode",
                ]
            )
        assert "HandshakeError" in str(excinfo.value)

        # wait a bit so there's some time for error messages to appear in log file
        time.sleep(1 if cluster.network_magic == configuration.NETWORK_MAGIC_LOCAL else 5)

    @allure.link(helpers.get_vcs_link())
    def test_wrong_signing_key(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send a transaction signed with wrong signing key.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        # use wrong signing key
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[1].payment.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_500_000)]

        # it should NOT be possible to submit a transaction with wrong signing key
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=pool_users[0].payment.address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
            )
        assert "MissingVKeyWitnessesUTXOW" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_wrong_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_wrong_tx_era: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send a transaction using TX era > network (cluster) era.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_500_000)]

        # it should NOT be possible to submit a transaction when TX era > network era
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_wrong_tx_era.g_transaction.send_tx(
                src_address=pool_users[0].payment.address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
            )
        err_str = str(excinfo.value)
        assert (
            "The era of the node and the tx do not match" in err_str
            or "HardForkEncoderDisabledEra" in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_send_funds_to_reward_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to send funds from payment address to stake address.

        Expect failure.
        """
        common.get_test_id(cluster)

        addr = pool_users[0].stake.address
        self._send_funds_to_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, use_build_cmd=use_build_cmd
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_send_funds_to_utxo_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to send funds from payment address to UTxO address.

        Expect failure.
        """
        common.get_test_id(cluster)

        dst_addr = pool_users[1].payment.address
        utxo_addr = cluster.g_query.get_utxo(address=dst_addr)[0].utxo_hash
        self._send_funds_to_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=utxo_addr, use_build_cmd=use_build_cmd
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    def test_send_funds_to_invalid_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to non-existent address (property-based test).

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(cluster_obj=cluster, pool_users=pool_users, addr=addr)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    def test_build_send_funds_to_invalid_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to non-existent address (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, use_build_cmd=True
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    def test_send_funds_to_invalid_length_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to address with invalid length.

        Expect failure. Property-based test.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(cluster_obj=cluster, pool_users=pool_users, addr=addr)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    def test_build_send_funds_to_invalid_length_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to address with invalid length.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure. Property-based test.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, use_build_cmd=True
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    def test_send_funds_to_invalid_chars_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to address with invalid characters.

        Expect failure. Property-based test.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(cluster_obj=cluster, pool_users=pool_users, addr=addr)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    def test_build_send_funds_to_invalid_chars_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to address with invalid characters.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure. Property-based test.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, use_build_cmd=True
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    def test_send_funds_from_invalid_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from invalid address (property-based test).

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(cluster_obj=cluster, pool_users=pool_users, addr=addr)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    def test_build_send_funds_from_invalid_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from non-existent address (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, use_build_cmd=True
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    def test_send_funds_from_invalid_length_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from address with invalid length (property-based test).

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(cluster_obj=cluster, pool_users=pool_users, addr=addr)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    def test_build_send_funds_from_invalid_length_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from address with invalid length (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, use_build_cmd=True
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    def test_send_funds_from_invalid_chars_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from address with invalid characters (property-based test).

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(cluster_obj=cluster, pool_users=pool_users, addr=addr)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    def test_build_send_funds_from_invalid_chars_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from address with invalid characters (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, use_build_cmd=True
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    def test_build_send_funds_invalid_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds using invalid change address (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_invalid_change_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    def test_build_send_funds_invalid_chars_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds using change address with invalid characters (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_invalid_change_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    def test_build_send_funds_invalid_length_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds using change address with invalid length (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_invalid_change_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_nonexistent_utxo_ix(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to use nonexistent UTxO TxIx as an input.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        utxo_copy = utxo._replace(utxo_ix=5)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster,
            pool_users=pool_users,
            utxo=utxo_copy,
            temp_template=temp_template,
            use_build_cmd=use_build_cmd,
        )
        if use_build_cmd:
            assert (
                "The UTxO is empty" in err
                # in 1.35.3 and older
                or "The following tx input(s) were not present in the UTxO" in err
            ), err
        else:
            assert "BadInputsUTxO" in err, err

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_nonexistent_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to use nonexistent UTxO hash as an input.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        new_hash = f"{utxo.utxo_hash[:-4]}fd42"
        utxo_copy = utxo._replace(utxo_hash=new_hash)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster,
            pool_users=pool_users,
            utxo=utxo_copy,
            temp_template=temp_template,
            use_build_cmd=use_build_cmd,
        )
        if use_build_cmd:
            assert (
                "The UTxO is empty" in err
                # in 1.35.3 and older
                or "The following tx input(s) were not present in the UTxO" in err
            ), err
        else:
            assert "BadInputsUTxO" in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(utxo_hash=st.text(alphabet=ADDR_ALPHABET, min_size=10, max_size=550))
    @common.hypothesis_settings(300)
    def test_invalid_length_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        utxo_hash: str,
    ):
        """Try to use invalid UTxO hash as an input (property-based test).

        Expect failure.
        """
        temp_template = f"test_invalid_length_utxo_hash_ci{cluster.cluster_id}"

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        utxo_copy = utxo._replace(utxo_hash=utxo_hash)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster, pool_users=pool_users, utxo=utxo_copy, temp_template=temp_template
        )
        assert (
            "Incorrect transaction id format" in err
            or "Failed reading" in err
            or "expecting transaction id (hexadecimal)" in err
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(utxo_hash=st.text(alphabet=ADDR_ALPHABET, min_size=10, max_size=550))
    @common.hypothesis_settings(300)
    def test_build_invalid_length_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        utxo_hash: str,
    ):
        """Try to use invalid UTxO hash as an input (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = f"test_build_invalid_length_utxo_hash_ci{cluster.cluster_id}"

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        utxo_copy = utxo._replace(utxo_hash=utxo_hash)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster,
            pool_users=pool_users,
            utxo=utxo_copy,
            temp_template=temp_template,
            use_build_cmd=True,
        )
        assert (
            "Incorrect transaction id format" in err
            or "Failed reading" in err
            or "expecting transaction id (hexadecimal)" in err
        )

    @allure.link(helpers.get_vcs_link())
    def test_missing_fee(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a transaction with a missing `--fee` parameter.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
        )
        txins, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--invalid-hereafter",
                    str(tx_raw_output.invalid_hereafter),
                    "--out-file",
                    str(tx_raw_output.out_file),
                    *helpers.prepend_flag("--tx-in", txins),
                    *helpers.prepend_flag("--tx-out", txouts),
                ]
            )
        err_str = str(excinfo.value)

        if "Transaction _ fee not supported in" in err_str:
            pytest.xfail("See node issue #4591 - Transaction _ fee not supported")

        assert (
            "fee must be specified" in err_str
            or "Implicit transaction fee not supported" in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.SHELLEY,
        reason="runs only with Shelley TX",
    )
    def test_missing_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a Shelley era TX with a missing `--ttl` (`--invalid-hereafter`) parameter.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
        )
        txins, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--fee",
                    str(tx_raw_output.fee),
                    "--out-file",
                    str(tx_raw_output.out_file),
                    *helpers.prepend_flag("--tx-in", txins),
                    *helpers.prepend_flag("--tx-out", txouts),
                    *cluster.g_transaction.tx_era_arg,
                ]
            )
        err_str = str(excinfo.value)

        if "Transaction validity upper bound not supported" in err_str:
            pytest.xfail("See node issue #4591 - Transaction validity upper bound not supported")

        assert (
            "TTL must be specified" in err_str
            or "Transaction validity upper bound must be specified" in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    def test_missing_tx_in(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a transaction with a missing `--tx-in` parameter.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
        )
        __, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--invalid-hereafter",
                    str(tx_raw_output.invalid_hereafter),
                    "--fee",
                    str(tx_raw_output.fee),
                    "--out-file",
                    str(tx_raw_output.out_file),
                    *helpers.prepend_flag("--tx-out", txouts),
                ]
            )
        assert re.search(r"Missing: *\(--tx-in TX-IN", str(excinfo.value))

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_missing_tx_in(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a transaction with a missing `--tx-in` parameter.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            for_build_command=True,
        )
        __, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build",
                    "--out-file",
                    str(tx_raw_output.out_file),
                    "--change-address",
                    str(pool_users[0].payment.address),
                    "--testnet-magic",
                    str(cluster.network_magic),
                    *helpers.prepend_flag("--tx-out", txouts),
                    *cluster.g_transaction.tx_era_arg,
                ]
            )
        assert re.search(r"Missing: *\(--tx-in TX-IN", str(excinfo.value))

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_missing_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a transaction with a missing `--change-address` parameter.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            for_build_command=True,
        )
        txins, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build",
                    "--out-file",
                    str(tx_raw_output.out_file),
                    "--testnet-magic",
                    str(cluster.network_magic),
                    *helpers.prepend_flag("--tx-in", txins),
                    *helpers.prepend_flag("--tx-out", txouts),
                    *cluster.g_transaction.tx_era_arg,
                ]
            )
        assert re.search(r"Missing:.* --change-address ADDRESS", str(excinfo.value))

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_multiple_change_addresses(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a transaction with multiple `--change-address` parameters.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            for_build_command=True,
        )
        txins, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build",
                    "--out-file",
                    str(tx_raw_output.out_file),
                    "--testnet-magic",
                    str(cluster.network_magic),
                    *helpers.prepend_flag("--tx-in", txins),
                    *helpers.prepend_flag("--tx-out", txouts),
                    *helpers.prepend_flag(
                        "--change-address",
                        [pool_users[0].payment.address, pool_users[2].payment.address],
                    ),
                    *cluster.g_transaction.tx_era_arg,
                ]
            )
        assert re.search(r"Invalid option.*--change-address", str(excinfo.value))

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("file_type", ("tx_body", "tx"))
    def test_sign_wrong_file(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        file_type: str,
    ):
        """Try to sign other file type than specified by command line option (Tx vs Tx body).

        Expect failure when cardano-cli CBOR serialization format is used (not CDDL format).

        * specify Tx file and pass Tx body file
        * specify Tx body file and pass Tx file
        """
        temp_template = f"{common.get_test_id(cluster)}_{file_type}"
        amount = 2_000_000

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # build and sign a transaction
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        if file_type == "tx":
            # call `cardano-cli transaction sign --tx-body-file tx`
            cli_args = {"tx_body_file": out_file_signed}
        else:
            # call `cardano-cli transaction sign --tx-file txbody`
            cli_args = {"tx_file": tx_raw_output.out_file}

        # when CDDL format is used, it doesn't matter what CLI option and file type
        # combination is used
        # TODO: move this tests from `TestNegative` once CDDL is the only supported format
        # of Tx body
        if cluster.use_cddl or not clusterlib_utils.cli_has("transaction build-raw --cddl-format"):
            tx_signed_again = cluster.g_transaction.sign_tx(
                **cli_args,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
            # check that the Tx can be successfully submitted
            cluster.g_transaction.submit_tx(tx_file=tx_signed_again, txins=tx_raw_output.txins)
        else:
            with pytest.raises(clusterlib.CLIError) as exc_body:
                cluster.g_transaction.sign_tx(
                    **cli_args,
                    signing_key_files=tx_files.signing_key_files,
                    tx_name=f"{temp_template}_err1",
                )
            assert "TextEnvelope error" in str(exc_body.value)
