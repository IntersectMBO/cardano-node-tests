"""Tests for unbalanced transactions."""

import logging

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import tx_common
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

MAX_LOVELACE_AMOUNT = common.MAX_UINT64


class TestUnbalanced:
    """Tests for unbalanced transactions."""

    def _build_transfer_amount_bellow_minimum(
        self,
        cluster: clusterlib.ClusterLib,
        temp_template: str,
        payment_addrs: list[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        amount: int,
    ):
        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build",
                    "--tx-in",
                    f"{pbt_highest_utxo.utxo_hash}#{pbt_highest_utxo.utxo_ix}",
                    "--change-address",
                    src_address,
                    "--tx-out",
                    f"{dst_address}+{amount}",
                    "--out-file",
                    f"{temp_template}_tx.body",
                    *cluster.magic_args,
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            if amount < 0:
                assert (
                    "Value must be positive in UTxO" in exc_value  # In cli 10.1.1.0+
                    or "Illegal Value in TxOut" in exc_value  # In node 9.2.0+
                    or "Negative quantity" in exc_value
                ), exc_value
            else:
                assert "Minimum UTxO threshold not met for tx output" in exc_value, exc_value

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @pytest.fixture
    def pbt_highest_utxo(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ) -> clusterlib.UTXOData:
        """Get UTxO with highest amount of Lovelace.

        Meant for property-based tests, so this expensive operation gets executed only once.
        """
        return cluster.g_query.get_utxo_with_highest_amount(payment_addrs[0].address)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_negative_change(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Try to build a transaction with a negative change amount.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.

        * calculate transaction fee
        * get UTxO with highest amount from source address
        * attempt to create transaction outputs that exceed inputs by 1 Lovelace
        * include a change output with -1 Lovelace
        * check that transaction building fails with appropriate error
        """
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.g_transaction.calculate_tx_ttl()

        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            dst_addresses=[dst_address],
            tx_files=tx_files,
            ttl=ttl,
        )

        src_addr_highest_utxo = cluster.g_query.get_utxo_with_highest_amount(src_address)

        # Use only the UTxO with the highest amount
        txins = [src_addr_highest_utxo]
        # Try to transfer +1 Lovelace more than available and use a negative change (-1)
        txouts = [
            clusterlib.TxOut(address=dst_address, amount=src_addr_highest_utxo.amount - fee + 1),
            clusterlib.TxOut(address=src_address, amount=-1),
        ]
        assert txins[0].amount - txouts[0].amount - fee == txouts[-1].amount

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_raw_tx_bare(
                out_file=f"{helpers.get_timestamped_rand_str()}_tx.body",
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "option --tx-out: Failed reading" in exc_value
                or "TxOutAdaOnly" in exc_value
                or "AdaAssetId,-1" in exc_value
                or "Negative quantity" in exc_value  # cardano-node >= 8.7.0
                or "Illegal Value in TxOut" in exc_value  # cardano-node >= 9.2.0
            )

        if "CallStack" in exc_value:
            issues.cli_904.finish_test()

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(transfer_add=st.integers(min_value=1, max_value=MAX_LOVELACE_AMOUNT // 2))
    @hypothesis.example(transfer_add=1)
    @common.hypothesis_settings(100)
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_transfer_unavailable_funds(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        transfer_add: int,
    ):
        """Try to build transaction transferring more funds than available (property-based test).

        Expect failure.

        Uses `cardano-cli transaction build` command.

        * use UTxO with highest amount as sole input
        * attempt to transfer amount exceeding UTxO balance by parametrized value (1 to MAX/2)
        * check that transaction building fails with negative balance error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        # Use only the UTxO with the highest amount
        txins = [pbt_highest_utxo]
        amount = min(MAX_LOVELACE_AMOUNT, pbt_highest_utxo.amount + transfer_add)
        # Try to transfer whole balance
        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "The net balance of the transaction is negative" in exc_value
                or "Illegal Value in TxOut" in exc_value  # In node 9.2.0+
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(change_amount=st.integers(min_value=2_000_000, max_value=MAX_LOVELACE_AMOUNT))
    @hypothesis.example(change_amount=2_000_000)
    @hypothesis.example(change_amount=MAX_LOVELACE_AMOUNT)
    @common.hypothesis_settings(300)
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_wrong_balance(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        change_amount: int,
    ):
        """Build transaction with unbalanced change amount (property-based test).

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.

        * use UTxO with highest amount as sole input
        * transfer full balance minus fee to destination address
        * add incorrect change output with parametrized amount (2 ADA to MAX)
        * build and sign the unbalanced transaction successfully
        * attempt to submit the unbalanced transaction
        * check that submission fails with ValueNotConservedUTxO error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        fee = 200_000
        transferred_amount = pbt_highest_utxo.amount - fee

        out_file_tx = f"{temp_template}_tx.body"
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.g_transaction.calculate_tx_ttl()

        # Use only the UTxO with the highest amount
        txins = [pbt_highest_utxo]
        txouts = [
            clusterlib.TxOut(address=dst_address, amount=transferred_amount),
            # Add the value from test's parameter to unbalance the transaction. Since the correct
            # change amount here is 0, the value from test's parameter can be used directly.
            clusterlib.TxOut(address=src_address, amount=change_amount),
        ]

        cluster.g_transaction.build_raw_tx_bare(
            out_file=out_file_tx,
            txins=txins,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=out_file_tx,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # It should NOT be possible to submit an unbalanced transaction
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(out_file_signed)
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            # TODO: see https://github.com/IntersectMBO/cardano-node/issues/2555
            assert "ValueNotConservedUTxO" in exc_value or "DeserialiseFailure" in exc_value, (
                exc_value
            )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(amount=st.integers(min_value=MAX_LOVELACE_AMOUNT + 1))
    @hypothesis.example(amount=MAX_LOVELACE_AMOUNT + 1)
    @common.hypothesis_settings(300)
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_out_of_bounds_amount(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        amount: int,
    ):
        """Try to build transaction with Lovelace amount exceeding maximum (property-based test).

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.

        * use UTxO with highest amount as sole input
        * attempt to create transaction output with amount > MAX_UINT64
        * check that transaction building fails with out of bounds error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"
        fee = 200_000

        out_file_tx = f"{temp_template}_tx.body"
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.g_transaction.calculate_tx_ttl()

        # Use only the UTxO with the highest amount
        txins = [pbt_highest_utxo]
        txouts = [
            clusterlib.TxOut(address=payment_addrs[0].address, amount=amount),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_raw_tx_bare(
                out_file=out_file_tx,
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "out of bounds" in exc_value or "exceeds the max bound" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    # See https://github.com/IntersectMBO/cardano-node/issues/4061
    @hypothesis.given(
        amount=st.integers(min_value=0, max_value=tx_common.MIN_UTXO_VALUE[1] - 1_000)
    )
    @hypothesis.example(amount=0)
    @hypothesis.example(amount=tx_common.MIN_UTXO_VALUE[1] - 1_000)
    @common.hypothesis_settings(max_examples=200)
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_transfer_amount_bellow_minimum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        amount: int,
    ):
        """Try to build transaction with amount below minimum UTxO threshold (property-based test).

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * use UTxO with highest amount as sole input
        * attempt to create transaction output with amount below minimum UTxO (0 to ~1 ADA)
        * check that transaction building fails with minimum UTxO threshold error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        self._build_transfer_amount_bellow_minimum(
            cluster=cluster,
            temp_template=temp_template,
            payment_addrs=payment_addrs,
            pbt_highest_utxo=pbt_highest_utxo,
            amount=amount,
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(amount=st.integers(min_value=-MAX_LOVELACE_AMOUNT, max_value=-1))
    @hypothesis.example(amount=-MAX_LOVELACE_AMOUNT)
    @hypothesis.example(amount=-1)
    @common.hypothesis_settings(max_examples=300)
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_transfer_negative_amount(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        amount: int,
    ):
        """Try to build transaction with negative Lovelace amount (property-based test).

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * use UTxO with highest amount as sole input
        * attempt to create transaction output with negative amount (-MAX to -1)
        * check that transaction building fails with negative quantity error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        self._build_transfer_amount_bellow_minimum(
            cluster=cluster,
            temp_template=temp_template,
            payment_addrs=payment_addrs,
            pbt_highest_utxo=pbt_highest_utxo,
            amount=amount,
        )

    @allure.link(helpers.get_vcs_link())
    # See https://github.com/IntersectMBO/cardano-node/issues/4061
    @hypothesis.given(
        amount=st.integers(min_value=0, max_value=tx_common.MIN_UTXO_VALUE[1] - 1_000)
    )
    @hypothesis.example(amount=0)
    @hypothesis.example(amount=tx_common.MIN_UTXO_VALUE[1] - 1_000)
    @common.hypothesis_settings(max_examples=400)
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_transfer_amount_bellow_minimum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        amount: int,
    ):
        """Try to build and submit transaction with amount below minimum UTxO (property-based test).

        Expect failure.

        Uses `cardano-cli transaction build-raw` command for building the transactions.

        * use UTxO with highest amount as sole input
        * build transaction with output amount below minimum UTxO (0 to ~1 ADA)
        * sign the transaction successfully (no validation at build-raw stage)
        * attempt to submit the transaction
        * check that submission fails with OutputTooSmallUTxO error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        fee = 200_000

        out_file = f"{temp_template}.body"
        build_args = [
            "transaction",
            "build-raw",
            "--fee",
            f"{fee}",
            "--tx-in",
            f"{pbt_highest_utxo.utxo_hash}#{pbt_highest_utxo.utxo_ix}",
            "--tx-out",
            f"{dst_address}+{amount}",
            "--tx-out",
            f"{src_address}+{pbt_highest_utxo.amount - amount - fee}",
            "--out-file",
            out_file,
        ]
        if VERSIONS.transaction_era < VERSIONS.ALLEGRA:
            build_args.extend(
                ["--invalid-hereafter", str(cluster.g_transaction.calculate_tx_ttl())]
            )

        cluster.cli(build_args)

        # Create signed transaction
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=out_file,
            signing_key_files=[payment_addrs[0].skey_file],
            tx_name=f"{temp_template}_signed",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo_build:
            # Submit the signed transaction
            cluster.g_transaction.submit_tx(tx_file=out_file_signed, txins=[pbt_highest_utxo])

        exc_value = str(excinfo_build.value)
        with common.allow_unstable_error_messages():
            assert "OutputTooSmallUTxO" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(amount=st.integers(min_value=-MAX_LOVELACE_AMOUNT, max_value=-1))
    @hypothesis.example(amount=-1)
    @hypothesis.example(amount=-MAX_LOVELACE_AMOUNT)
    @common.hypothesis_settings(max_examples=500)
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_transfer_negative_amount(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        amount: int,
    ):
        """Try to build transaction with negative Lovelace amount (property-based test).

        Expect failure.

        Uses `cardano-cli transaction build-raw` command for building the transactions.

        * use UTxO with highest amount as sole input
        * attempt to build transaction with negative output amount (-MAX to -1)
        * check that transaction building fails with negative quantity error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        fee = 200_000

        out_file = f"{temp_template}.body"
        build_args = [
            "transaction",
            "build-raw",
            "--fee",
            f"{fee}",
            "--tx-in",
            f"{pbt_highest_utxo.utxo_hash}#{pbt_highest_utxo.utxo_ix}",
            "--tx-out",
            f"{dst_address}+{amount}",
            "--tx-out",
            f"{src_address}+2000000",
            "--out-file",
            out_file,
        ]
        if VERSIONS.transaction_era < VERSIONS.ALLEGRA:
            build_args.extend(
                ["--invalid-hereafter", str(cluster.g_transaction.calculate_tx_ttl())]
            )

        with pytest.raises(clusterlib.CLIError) as excinfo_build:
            cluster.cli(build_args)
        err_str_build = str(excinfo_build.value)
        assert (
            "Value must be positive in UTxO" in err_str_build  # In cli 10.1.1.0+
            or "Illegal Value in TxOut" in err_str_build  # In node 9.2.0+
            or "Negative quantity" in err_str_build
        ), err_str_build
