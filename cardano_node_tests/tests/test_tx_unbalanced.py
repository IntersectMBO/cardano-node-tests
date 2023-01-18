"""Tests for unbalanced transactions."""
import logging
from typing import List

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import tx_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

MAX_LOVELACE_AMOUNT = common.MAX_UINT64


@pytest.mark.testnets
@pytest.mark.smoke
class TestUnbalanced:
    """Tests for unbalanced transactions."""

    def _build_transfer_amount_bellow_minimum(
        self,
        cluster: clusterlib.ClusterLib,
        temp_template: str,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        amount: int,
    ):
        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        err_str = ""
        try:
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
                    *cluster.g_transaction.tx_era_arg,
                    *cluster.magic_args,
                ]
            )
        except clusterlib.CLIError as err:
            err_str = str(err)

        if amount < 0:
            assert "Negative quantity" in err_str, err_str
        else:
            assert "Minimum UTxO threshold not met for tx output" in err_str, err_str

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                f"addr_not_balanced_ci{cluster_manager.cluster_instance_num}_0",
                f"addr_not_balanced_ci{cluster_manager.cluster_instance_num}_1",
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return addrs

    @pytest.fixture
    def pbt_highest_utxo(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> clusterlib.UTXOData:
        """Get UTxO with highest amount of Lovelace.

        Meant for property-based tests, so this expensive operation gets executed only once.
        """
        return cluster.g_query.get_utxo_with_highest_amount(payment_addrs[0].address)

    @allure.link(helpers.get_vcs_link())
    def test_negative_change(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Try to build a transaction with a negative change.

        Check that it is not possible to build such transaction.
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

        # use only the UTxO with the highest amount
        txins = [src_addr_highest_utxo]
        # try to transfer +1 Lovelace more than available and use a negative change (-1)
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
        exc_val = str(excinfo.value)
        assert (
            "option --tx-out: Failed reading" in exc_val
            or "TxOutAdaOnly" in exc_val
            or "AdaAssetId,-1" in exc_val
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(transfer_add=st.integers(min_value=1, max_value=MAX_LOVELACE_AMOUNT // 2))
    @hypothesis.example(transfer_add=1)
    @common.hypothesis_settings(100)
    def test_build_transfer_unavailable_funds(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        transfer_add: int,
    ):
        """Try to build a transaction with more funds than available `transaction build`.

        Check that it is not possible to build such transaction.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        # use only the UTxO with the highest amount
        txins = [pbt_highest_utxo]
        amount = min(MAX_LOVELACE_AMOUNT, pbt_highest_utxo.amount + transfer_add)
        # try to transfer whole balance
        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
            )
        exc_val = str(excinfo.value)
        assert "The net balance of the transaction is negative" in exc_val, exc_val

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(change_amount=st.integers(min_value=2_000_000, max_value=MAX_LOVELACE_AMOUNT))
    @hypothesis.example(change_amount=2_000_000)
    @hypothesis.example(change_amount=MAX_LOVELACE_AMOUNT)
    @common.hypothesis_settings(300)
    def test_wrong_balance(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        change_amount: int,
    ):
        """Build a transaction with unbalanced change (property-based test).

        * build an unbalanced transaction
        * check that it is not possible to submit such transaction
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        fee = 200_000
        transferred_amount = pbt_highest_utxo.amount - fee

        out_file_tx = f"{temp_template}_tx.body"
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.g_transaction.calculate_tx_ttl()

        # use only the UTxO with the highest amount
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

        # it should NOT be possible to submit an unbalanced transaction
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(out_file_signed)
        exc_val = str(excinfo.value)
        # TODO: see https://github.com/input-output-hk/cardano-node/issues/2555
        assert "ValueNotConservedUTxO" in exc_val or "DeserialiseFailure" in exc_val, exc_val

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(change_amount=st.integers(min_value=MAX_LOVELACE_AMOUNT + 1))
    @hypothesis.example(change_amount=2_000_000)
    @hypothesis.example(change_amount=MAX_LOVELACE_AMOUNT + 1)
    @common.hypothesis_settings(300)
    def test_out_of_bounds_amount(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        change_amount: int,
    ):
        """Try to build a transaction with output Lovelace amount that is out of bounds."""
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"
        fee = 200_000

        out_file_tx = f"{temp_template}_tx.body"
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.g_transaction.calculate_tx_ttl()

        # use only the UTxO with the highest amount
        txins = [pbt_highest_utxo]
        txouts = [
            clusterlib.TxOut(address=payment_addrs[0].address, amount=change_amount),
        ]

        try:
            cluster.g_transaction.build_raw_tx_bare(
                out_file=out_file_tx,
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
            )
        except clusterlib.CLIError as exc:
            exc_val = str(exc)
            assert "out of bounds" in exc_val or "exceeds the max bound" in exc_val, exc_val

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    # TODO: `MIN_UTXO_VALUE - 10_000` because of issue
    # https://github.com/input-output-hk/cardano-node/issues/4061
    @hypothesis.given(amount=st.integers(min_value=0, max_value=tx_common.MIN_UTXO_VALUE - 10_000))
    @hypothesis.example(amount=0)
    @hypothesis.example(amount=tx_common.MIN_UTXO_VALUE - 10_000)
    @common.hypothesis_settings(max_examples=200)
    def test_build_transfer_amount_bellow_minimum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        amount: int,
    ):
        """Try to build a transaction with amount bellow the minimum lovelace required.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
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
    def test_build_transfer_negative_amount(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        amount: int,
    ):
        """Try to build a transaction with negative Lovelace amount.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
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
    # TODO: `MIN_UTXO_VALUE - 10_000` because of issue
    # https://github.com/input-output-hk/cardano-node/issues/4061
    @hypothesis.given(amount=st.integers(min_value=0, max_value=tx_common.MIN_UTXO_VALUE - 10_000))
    @hypothesis.example(amount=0)
    @hypothesis.example(amount=tx_common.MIN_UTXO_VALUE - 10_000)
    @common.hypothesis_settings(max_examples=400)
    def test_transfer_amount_bellow_minimum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        amount: int,
    ):
        """Try to build a transaction with amount bellow the minimum lovelace required.

        Uses `cardano-cli transaction build-raw` command for building the transactions.

        Expect failure.
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
            *cluster.g_transaction.tx_era_arg,
            "--out-file",
            out_file,
        ]
        if VERSIONS.transaction_era < VERSIONS.ALLEGRA:
            build_args.extend(
                ["--invalid-hereafter", str(cluster.g_transaction.calculate_tx_ttl())]
            )

        cluster.cli(build_args)

        # create signed transaction
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=out_file,
            signing_key_files=[payment_addrs[0].skey_file],
            tx_name=f"{temp_template}_signed",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo_build:
            # submit the signed transaction
            cluster.g_transaction.submit_tx(tx_file=out_file_signed, txins=[pbt_highest_utxo])

        exc_val = str(excinfo_build.value)
        assert "OutputTooSmallUTxO" in exc_val, exc_val

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(amount=st.integers(min_value=-MAX_LOVELACE_AMOUNT, max_value=-1))
    @hypothesis.example(amount=-1)
    @hypothesis.example(amount=-MAX_LOVELACE_AMOUNT)
    @common.hypothesis_settings(max_examples=500)
    def test_transfer_negative_amount(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        amount: int,
    ):
        """Try to build a transaction with negative Lovelace amount.

        Uses `cardano-cli transaction build-raw` command for building the transactions.

        Expect failure.
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
            *cluster.g_transaction.tx_era_arg,
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
        assert "Negative quantity" in err_str_build, err_str_build
