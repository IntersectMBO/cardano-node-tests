"""Tests for basic transactions."""

import dataclasses
import itertools
import logging
import re

import allure
import pytest
from _pytest.fixtures import SubRequest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import tx_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_api
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


class TestBasicTransactions:
    """Test basic transactions - transferring funds, transaction IDs."""

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
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @pytest.fixture
    def byron_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create 2 new Byron payment addresses."""
        fixture_cache: cluster_management.FixtureCache[list[clusterlib.AddressRecord] | None]
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value is None:
                new_byron_addrs = [
                    clusterlib_utils.gen_byron_addr(
                        cluster_obj=cluster,
                        name_template=f"addr_payment_ci{cluster_manager.cluster_instance_num}_{i}",
                    )
                    for i in range(2)
                ]
                fixture_cache.value = new_byron_addrs
            else:
                new_byron_addrs = fixture_cache.value

        # Fund source addresses
        clusterlib_utils.fund_from_faucet(
            *new_byron_addrs,
            cluster_obj=cluster,
            all_faucets=cluster_manager.cache.addrs_data,
            amount=10_000_000,
        )
        return new_byron_addrs

    @pytest.fixture
    def payment_addrs_disposable(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=f"{common.get_test_id(cluster)}_disposable",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
        )
        return addrs

    @pytest.fixture
    def payment_addrs_no_change(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create 2 new payment addresses for `test_build_no_change`."""
        addrs = common.get_payment_addrs(
            name_template=f"{common.get_test_id(cluster)}_no_change",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
            fund_idx=[0],
        )
        return addrs

    @pytest.fixture
    def cluster_default_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        request: SubRequest,
    ) -> clusterlib.ClusterLib:
        is_era_explicit = request.param
        cluster_default = cluster

        if is_era_explicit:
            default_tx_era = VERSIONS.MAP[VERSIONS.DEFAULT_TX_ERA]
            if cluster.command_era != default_tx_era:
                cluster_default = cluster_nodes.get_cluster_type().get_cluster_obj(
                    command_era=default_tx_era
                )
        elif cluster.command_era:
            cluster_default = cluster_nodes.get_cluster_type().get_cluster_obj(command_era="")

        return cluster_default

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_BUILD_METHOD
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 5_000_000))
    @pytest.mark.parametrize(
        "dst_addr_type", ("shelley", "byron"), ids=("dst_shelley", "dst_byron")
    )
    @pytest.mark.parametrize(
        "src_addr_type", ("shelley", "byron"), ids=("src_shelley", "src_byron")
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_transfer_funds(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        byron_addrs: list[clusterlib.AddressRecord],
        src_addr_type: str,
        dst_addr_type: str,
        amount: int,
        submit_method: str,
        build_method: str,
    ):
        """Send funds to payment address.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        src_addr = byron_addrs[0] if src_addr_type == "byron" else payment_addrs[0]
        dst_addr = byron_addrs[1] if dst_addr_type == "byron" else payment_addrs[1]

        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=src_addr.address,
            submit_method=submit_method,
            build_method=build_method,
            txouts=txouts,
            tx_files=tx_files,
            # TODO: cardano-node issue #4752
            witness_override=2 if src_addr_type == "byron" else None,
            byron_witness_count=1 if src_addr_type == "byron" else 0,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            assert (
                cluster.g_query.get_address_balance(src_addr.address)
                == dbsync_utils.get_utxo(address=src_addr.address).amount_sum
            ), f"Unexpected balance for source address `{src_addr.address}` in db-sync"

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_byron_fee_too_small(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        byron_addrs: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Test cardano-node issue #4752.

        Use `cardano-cli transaction build` command for building a transaction that needs to be
        signed by Byron skey. Check if calculated fee is too small and if Tx submit fails.
        """
        temp_template = common.get_test_id(cluster)
        amount = 1_500_000

        src_addr = byron_addrs[0]

        txouts = [clusterlib.TxOut(address=payment_addrs[1].address, amount=amount)]
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

        try:
            submit_utils.submit_tx(
                submit_method=submit_method,
                cluster_obj=cluster,
                tx_file=tx_signed,
                txins=tx_output.txins,
            )
        except (clusterlib.CLIError, submit_api.SubmitApiError) as exc:
            if "FeeTooSmallUTxO" not in str(exc):
                raise
            issues.node_4752.finish_test()

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_build_no_change(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs_no_change: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Send funds to payment address and balance the outputs so that there is no change.

        Uses `cardano-cli transaction build` command for building the transactions.

        * try to build a Tx that transfers all available funds, and extract fee amount
          from the error message
        * transfer all available funds minus fee from source address to destination address
        * check that no change UTxO was created
        * (optional) check transactions in db-sync
        """
        # The test checks the following issues:
        #  - https://github.com/IntersectMBO/cardano-node/issues/3041

        temp_template = common.get_test_id(cluster)

        src_addr = payment_addrs_no_change[0]
        src_address = src_addr.address
        dst_address = payment_addrs_no_change[1].address

        src_init_balance = cluster.g_query.get_address_balance(src_address)

        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        fee = 150_000  # Initial fee value
        for i in range(5):
            txouts = [clusterlib.TxOut(address=dst_address, amount=src_init_balance - fee)]

            try:
                tx_output = cluster.g_transaction.build_tx(
                    src_address=src_address,
                    tx_name=f"{temp_template}_{i}",
                    tx_files=tx_files,
                    txouts=txouts,
                    change_address=src_address,
                )
            except clusterlib.CLIError as exc:
                str_exc = str(exc)

                if "does not meet the minimum UTxO threshold" in str_exc:
                    issues.api_829.finish_test()
                if "negative" not in str_exc:
                    raise

                fee_match_old = re.search(r"negative: Lovelace \(-([0-9]*)\) lovelace", str_exc)
                # cardano-node 8.10.0+
                fee_match_new = re.search(r"negative: -([0-9]*) Lovelace", str_exc)
                fee_match = fee_match_new or fee_match_old
                assert fee_match, f"The expected error message was not found: {str_exc}"
                fee = fee + int(fee_match.group(1))
            else:
                break

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=tx_signed,
            txins=tx_output.txins,
        )

        clusterlib_utils.check_txins_spent(cluster_obj=cluster, txins=tx_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert not clusterlib.filter_utxos(utxos=out_utxos, address=src_address), (
            f"Unexpected change UTxO created on source address `{src_address}`"
        )
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount
            == src_init_balance - fee
        ), f"Incorrect balance for destination address `{dst_address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_transfer_all_funds(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs_disposable: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Send ALL funds from one payment address to another.

        * send all available funds from 1 source address to 1 destination address
        * check expected balance for destination addresses
        * check that balance for source address is 0 Lovelace
        * check output of the `transaction view` command
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs_disposable[1].address
        dst_address = payment_addrs_disposable[0].address

        # Amount value -1 means all available funds
        txouts = [clusterlib.TxOut(address=dst_address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs_disposable[1].skey_file])

        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=out_file_signed,
            txins=tx_raw_output.txins,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert not clusterlib.filter_utxos(utxos=out_utxos, address=src_address), (
            f"Incorrect balance for source address `{src_address}`"
        )
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for destination address `{dst_address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        # Check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_transfer_some_build_estimate(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Transfer some funds from one payment address to another.

        Use the `transaction build-estimate` command.

        * transfer some available funds from 1 source address to 1 destination address
        * check expected balance for source addresses
        * check expected balance for destination addresses
        * check output of the `transaction view` command
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_output = cluster.g_transaction.build_estimate_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        cluster.g_transaction.submit_tx(
            tx_file=out_file_signed,
            txins=tx_raw_output.txins,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        )
        assert clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount, (
            f"Incorrect balance for destination address `{dst_address}`"
        )

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        # Check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_transfer_all_build_estimate(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs_disposable: list[clusterlib.AddressRecord],
    ):
        """Transfer all funds from one payment address to another.

        Use the `transaction build-estimate` command.

        * transfer all available funds from 1 source address to 1 destination address
        * check expected balance for destination addresses
        * check that balance for source address is 0 Lovelace
        * check output of the `transaction view` command
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs_disposable[1].address
        dst_address = payment_addrs_disposable[0].address

        txouts = [clusterlib.TxOut(address=dst_address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs_disposable[1].skey_file])

        try:
            tx_raw_output = cluster.g_transaction.build_estimate_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
            )
        except clusterlib.CLIError as exc:
            if "balance of the transaction is negative" not in str(exc):
                raise
            issues.cli_1199.finish_test()

        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        cluster.g_transaction.submit_tx(
            tx_file=out_file_signed,
            txins=tx_raw_output.txins,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert not clusterlib.filter_utxos(utxos=out_utxos, address=src_address), (
            f"Incorrect balance for source address `{src_address}`"
        )
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for destination address `{dst_address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        # Check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_funds_to_valid_address(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Send funds to a valid payment address.

        The destination address is a valid address that was generated sometime
        in the past. The test verifies it is possible to use a valid address
        even though it was not generated while running a specific cardano
        network.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        * check min UTxO value
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = "addr_test1vpst87uzwafqkxumyf446zr2jsyn44cfpu9fe8yqanyuh6glj2hkl"

        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=src_address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            txouts=txouts,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount, (
            f"Incorrect balance for destination address `{dst_address}`"
        )

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        # Check min UTxO value
        min_value = cluster.g_transaction.calculate_min_req_utxo(txouts=txouts)
        assert min_value.value in tx_common.MIN_UTXO_VALUE

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_get_txid(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Get transaction ID (txid) from transaction body.

        Transaction ID is a hash of transaction body and doesn't change for a signed TX.

        * send funds from 1 source address to 1 destination address
        * get txid from transaction body
        * get txid from signed transaction
        * check that txid from transaction body matches the txid from signed transaction
        * check that txid has expected length
        * check that the txid is listed in UTxO hashes for both source and destination addresses
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        txouts = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=src_address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            txouts=txouts,
            tx_files=tx_files,
        )

        txid_body = cluster.g_transaction.get_txid(tx_body_file=tx_output.out_file)
        txid_signed = cluster.g_transaction.get_txid(
            tx_file=tx_output.out_file.with_suffix(".signed")
        )
        assert txid_body == txid_signed
        assert len(txid_body) == 64

        utxo_src = cluster.g_query.get_utxo(txin=f"{txid_body}#1")[0]  # change UTxO
        assert txid_body in utxo_src.utxo_hash

        utxo_dst = cluster.g_query.get_utxo(txin=f"{txid_body}#0")[0]
        assert txid_body in utxo_dst.utxo_hash

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_extra_signing_keys(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Send a transaction with extra signing key.

        Check that it is possible to use unneeded signing key in addition to the necessary
        signing keys for signing the transaction.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        # Use extra signing key
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file, payment_addrs[1].skey_file]
        )
        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]

        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, txouts=txouts, tx_files=tx_files
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # It should be possible to submit a transaction with extra signing key
        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=tx_signed,
            txins=tx_raw_output.txins,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount, (
            f"Incorrect balance for destination address `{dst_address}`"
        )

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_duplicate_signing_keys(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Send a transaction with duplicate signing key.

        Check that it is possible to specify the same signing key twice.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        # Use extra signing key
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file, payment_addrs[0].skey_file]
        )
        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]

        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, txouts=txouts, tx_files=tx_files
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # It should be possible to submit a transaction with duplicate signing key
        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=tx_signed,
            txins=tx_raw_output.txins,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount, (
            f"Incorrect balance for destination address `{dst_address}`"
        )

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.parametrize("file_type", ("tx_body", "tx"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_sign_wrong_file(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        file_type: str,
        submit_method: str,
    ):
        """Sign other file type than the one specified by command line option (Tx vs Tx body).

        * specify Tx file and pass Tx body file
        * specify Tx body file and pass Tx file
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # Build and sign a transaction
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        if file_type == "tx":
            # Call `cardano-cli transaction sign --tx-body-file tx`
            cli_args = {"tx_body_file": out_file_signed}
        else:
            # Call `cardano-cli transaction sign --tx-file txbody`
            cli_args = {"tx_file": tx_raw_output.out_file}

        tx_signed_again = cluster.g_transaction.sign_tx(
            **cli_args,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # Check that the Tx can be successfully submitted
        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=tx_signed_again,
            txins=tx_raw_output.txins,
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_no_txout(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        submit_method: str,
    ):
        """Send transaction with just fee, no UTxO is produced.

        * submit a transaction where all funds available on source address is used for fee
        * check that no UTxOs are created by the transaction
        * check that there are no funds left on source address
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        src_record = common.get_payment_addr(
            name_template=temp_template,
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            amount=2_000_000,
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[src_record.skey_file])
        fee = cluster.g_query.get_address_balance(src_record.address)
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_record.address, tx_name=temp_template, tx_files=tx_files, fee=fee
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=tx_signed,
            txins=tx_raw_output.txins,
        )

        assert not tx_raw_output.txouts, "Transaction has unexpected txouts"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_missing_tx_out(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Build a transaction with a missing `--tx-out` parameter."""
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=payment_addrs[0],
            dst_record=payment_addrs[1],
        )
        txins, __ = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        cli_args = [
            "transaction",
            "build-raw",
            "--invalid-hereafter",
            str(tx_raw_output.invalid_hereafter),
            "--fee",
            str(tx_raw_output.fee),
            "--out-file",
            str(tx_raw_output.out_file),
            *helpers.prepend_flag("--tx-in", txins),
        ]

        cluster.cli(cli_args)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era == VERSIONS.SHELLEY and VERSIONS.node < version.parse("8.7.0"),
        reason="doesn't run with Shelley TX on node < 8.7.0",
    )
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_missing_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Submit a transaction with a missing `--ttl` (`--invalid-hereafter`) parameter."""
        temp_template = common.get_test_id(cluster)
        src_address = payment_addrs[0].address

        tx_raw_template = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=payment_addrs[0],
            dst_record=payment_addrs[0],
        )
        txins, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_template.txins, txouts=tx_raw_template.txouts
        )
        tx_raw_output = dataclasses.replace(tx_raw_template, invalid_hereafter=None)

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
            ]
        )

        tx_signed_file = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=temp_template,
            signing_key_files=[payment_addrs[0].skey_file],
        )

        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=tx_signed_file,
            txins=tx_raw_output.txins,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for source address `{src_address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_multiple_same_txins(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Try to build a transaction with multiple identical txins."""
        temp_template = common.get_test_id(cluster)
        src_address = payment_addrs[0].address

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=payment_addrs[0],
            dst_record=payment_addrs[0],
        )
        txins, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        cluster.cli(
            [
                "transaction",
                "build-raw",
                "--fee",
                str(tx_raw_output.fee),
                "--out-file",
                str(tx_raw_output.out_file),
                "--invalid-hereafter",
                str(tx_raw_output.invalid_hereafter),
                "--tx-in",
                str(txins[0]),
                *helpers.prepend_flag("--tx-in", txins),
                *helpers.prepend_flag("--tx-out", txouts),
            ]
        )

        tx_signed_file = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=temp_template,
            signing_key_files=[payment_addrs[0].skey_file],
        )

        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=tx_signed_file,
            txins=tx_raw_output.txins,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for source address `{src_address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_multiple_same_txins(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Build a transaction with multiple identical txins.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=payment_addrs[0],
            dst_record=payment_addrs[1],
            for_build_command=True,
        )
        txins, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        out = cluster.cli(
            [
                "transaction",
                "build",
                "--out-file",
                str(tx_raw_output.out_file),
                "--testnet-magic",
                str(cluster.network_magic),
                "--change-address",
                str(payment_addrs[0].address),
                "--tx-in",
                str(txins[0]),
                *helpers.prepend_flag("--tx-in", txins),
                *helpers.prepend_flag("--tx-out", txouts),
            ]
        )
        assert tx_raw_output.out_file.exists()

        stdout_dec = out.stdout.strip().decode("utf-8") if out.stdout else ""
        assert " Lovelace" in stdout_dec, "Fee information not found in stdout"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALONZO,
        reason="doesn't run with TX era < Alonzo",
    )
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_utxo_with_datum_hash(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Create a UTxO with datum hash in a regular address and spend it.

        * create a UTxO with a datum hash at the payment address
        * check that the UTxO was created with the respective datum hash
        * spend the UTxO (not providing the datum hash)
        * check that the UTxO was spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        payment_addr_1 = payment_addrs[0].address
        payment_addr_2 = payment_addrs[1].address

        # Step 1: create utxo with a datum hash
        datum_hash = cluster.g_transaction.get_hash_script_data(
            script_data_value="42",
        )

        txouts_1 = [clusterlib.TxOut(address=payment_addr_2, amount=amount, datum_hash=datum_hash)]
        tx_files_1 = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_output_1 = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_step_1",
            src_address=payment_addr_1,
            submit_method=submit_method,
            txouts=txouts_1,
            tx_files=tx_files_1,
        )

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output_1.out_file)
        datum_hash_utxo = cluster.g_query.get_utxo(txin=f"{txid}#0")
        assert datum_hash_utxo[0].datum_hash, "No datum hash"

        # Step 2: spend the created utxo
        txouts_2 = [clusterlib.TxOut(address=payment_addr_1, amount=-1)]
        tx_files_2 = clusterlib.TxFiles(signing_key_files=[payment_addrs[1].skey_file])

        tx_raw_output_2 = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_step_2",
            src_address=payment_addr_2,
            submit_method=submit_method,
            txins=datum_hash_utxo,
            txouts=txouts_2,
            tx_files=tx_files_2,
        )

        # Check that the UTxO was spent
        assert not cluster.g_query.get_utxo(txin=f"{txid}#0"), "UTxO not spent"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_2)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_far_future_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Send a transaction with ttl far in the future."""
        temp_template = common.get_test_id(cluster)

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=2_000_000)]

        ttl = cluster.g_query.get_slot_no() + 10_000_000
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            invalid_hereafter=ttl,
        )

        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
            invalid_hereafter=ttl,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # It should be possible to submit a transaction with ttl far in the future
        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=out_file_signed,
            txins=tx_raw_output.txins,
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_WRONG_ERA
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize(
        "cluster_default_tx_era",
        (True, False),
        ids=("explicit_tx_era", "implicit_tx_era"),
        indirect=True,
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_default_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_default_tx_era: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test default Tx era.

        * check that default Tx era is implicit
        * check that default Tx era can be specified explicitly
        """
        temp_template = common.get_test_id(cluster)

        cluster = cluster_default_tx_era

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        txouts = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=src_address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            txouts=txouts,
            tx_files=tx_files,
        )

        # Check `transaction view` command, this will check if the tx era is the expected
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output)


class TestMultiInOut:
    """Test transactions with multiple txins and/or txouts."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create 201 new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=201,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
            amount=90_000_000_000,
        )
        return addrs

    def _from_to_transactions(
        self,
        cluster_obj: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        tx_name: str,
        from_num: int,
        to_num: int,
        amount: int,
        submit_method: str,
        use_build_cmd=False,
    ):
        """Test 1 tx from `from_num` payment addresses to `to_num` payment addresses."""
        src_address = payment_addrs[0].address
        # Addr1..addr<from_num+1>
        from_addr_recs = payment_addrs[1 : from_num + 1]
        # Addr<from_num+1>..addr<from_num+to_num+1>
        dst_addresses = [
            payment_addrs[i].address for i in range(from_num + 1, from_num + to_num + 1)
        ]

        # Fund "from" addresses
        # Using `src_address` to fund the "from" addresses. In `build_and_submit_tx`, all remaining
        # change is returned to `src_address`, so it should always have enough funds.
        # The "from" addresses has zero balance after each test.
        fund_amount = int(amount * len(dst_addresses) / len(from_addr_recs))
        # Min UTxO on testnets is 1.x ADA
        fund_amount = max(fund_amount, 1500000)
        fund_dst = [
            clusterlib.TxOut(address=d.address, amount=fund_amount) for d in from_addr_recs[:-1]
        ]
        # Add more funds to the last "from" address so it can cover TX fee
        last_from_addr_rec = from_addr_recs[-1]
        fund_dst.append(
            clusterlib.TxOut(address=last_from_addr_rec.address, amount=fund_amount + 5_000_000)
        )
        fund_tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster_obj,
            name_template=f"{tx_name}_add_funds",
            src_address=src_address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            txouts=fund_dst,
            tx_files=fund_tx_files,
        )

        # Create TX data
        _txins = [cluster_obj.g_query.get_utxo(address=r.address) for r in from_addr_recs]
        # Flatten the list of lists that is _txins
        txins = list(itertools.chain.from_iterable(_txins))
        txouts = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]
        tx_files = clusterlib.TxFiles(signing_key_files=[r.skey_file for r in from_addr_recs])

        # Send TX
        tx_raw_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster_obj,
            name_template=tx_name,
            src_address=src_address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            txins=txins,
            txouts=txouts,
            tx_files=tx_files,
        )

        # Check balances
        out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[
            0
        ].amount == clusterlib.calculate_utxos_balance(
            tx_raw_output.txins
        ) - tx_raw_output.fee - amount * len(dst_addresses), (
            f"Incorrect balance for source address `{src_address}`"
        )

        for addr in dst_addresses:
            assert clusterlib.filter_utxos(utxos=out_utxos, address=addr)[0].amount == amount, (
                f"Incorrect balance for destination address `{addr}`"
            )

        common.check_missing_utxos(cluster_obj=cluster_obj, utxos=out_utxos)

        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_10_transactions(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Send 10 transactions to payment address.

        * send funds from 1 source address to 1 destination address in 10 separate transactions
        * check expected balances for both source and destination addresses
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        no_of_transactions = 10
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.g_query.get_address_balance(src_address)
        dst_init_balance = cluster.g_query.get_address_balance(dst_address)

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]

        tx_outputs = []
        for i in range(no_of_transactions):
            tx_raw_output = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_{i}",
                src_address=src_address,
                submit_method=submit_method,
                txouts=txouts,
                tx_files=tx_files,
            )
            tx_outputs.append(tx_raw_output)

            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        assert (
            cluster.g_query.get_address_balance(src_address)
            == src_init_balance - sum(r.fee for r in tx_outputs) - amount * no_of_transactions
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.g_query.get_address_balance(dst_address)
            == dst_init_balance + amount * no_of_transactions
        ), f"Incorrect balance for destination address `{dst_address}`"

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 10_000_000))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_transaction_to_10_addrs_from_1_addr(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        amount: int,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test 1 transaction from 1 payment address to 10 payment addresses.

        * send funds from 1 source address to 10 destination addresses
        * check expected balances for both source and destination addresses
        * (optional) check transactions in db-sync
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=common.get_test_id(cluster),
            from_num=1,
            to_num=10,
            amount=amount,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 10_000_000))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_transaction_to_1_addr_from_10_addrs(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        amount: int,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test 1 transaction from 10 payment addresses to 1 payment address.

        * send funds from 10 source addresses to 1 destination address
        * check expected balances for both source and destination addresses
        * (optional) check transactions in db-sync
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=common.get_test_id(cluster),
            from_num=10,
            to_num=1,
            amount=amount,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 10_000_000))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_transaction_to_10_addrs_from_10_addrs(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        amount: int,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test 1 transaction from 10 payment addresses to 10 payment addresses.

        * send funds from 10 source addresses to 10 destination addresses
        * check expected balances for both source and destination addresses
        * (optional) check transactions in db-sync
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=common.get_test_id(cluster),
            from_num=10,
            to_num=10,
            amount=amount,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 5_000_000))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_transaction_to_100_addrs_from_50_addrs(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        amount: int,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test 1 transaction from 50 payment addresses to 100 payment addresses.

        * send funds from 50 source addresses to 100 destination addresses
        * check expected balances for both source and destination addresses
        * (optional) check transactions in db-sync
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=common.get_test_id(cluster),
            from_num=50,
            to_num=100,
            amount=amount,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
        )


class TestIncrementalSigning:
    """Test incremental signing of transactions."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=10,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )

        return addrs

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.parametrize("tx_is", ("witnessed", "signed"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_incremental_signing(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        tx_is: str,
        submit_method: str,
    ):
        """Test sending funds using Tx that is signed incrementally.

        Test with Tx body created by both `transaction build` and `transaction build-raw`.
        Test with Tx created by both `transaction sign` and `transaction assemble`.

        * create a transaction
        * sign the transaction incrementally with part of the signing keys
        * sign the transaction incrementally with the rest of the signing keys, except of
          the required one
        * sign the transaction multiple times with the same skey to see that it doesn't affect
          the Tx fee
        * check that the transaction cannot be submitted
        * sign the transaction with the required signing key
        * check that the transaction can be submitted
        * check expected balances for both source and destination addresses
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        payment_skey_files = [p.skey_file for p in payment_addrs]

        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(
            signing_key_files=payment_skey_files,
        )

        if use_build_cmd:
            tx_output = cluster.g_transaction.build_tx(
                src_address=src_addr.address,
                tx_name=temp_template,
                tx_files=tx_files,
                txouts=txouts,
                fee_buffer=1_000_000,
                witness_override=len(payment_skey_files),
            )
        else:
            fee = cluster.g_transaction.calculate_tx_fee(
                src_address=src_addr.address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
                witness_count_add=len(payment_skey_files),
            )
            tx_output = cluster.g_transaction.build_raw_tx(
                src_address=src_addr.address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
                fee=fee,
            )

        # Sign or witness Tx body with part of the skeys and thus create Tx file that will be used
        # for incremental signing
        if tx_is == "signed":
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=payment_skey_files[5:],
                tx_name=f"{temp_template}_from0",
            )
        else:
            # Sign Tx body using witness files
            witness_files = [
                cluster.g_transaction.witness_tx(
                    tx_body_file=tx_output.out_file,
                    witness_name=f"{temp_template}_from_skey{idx}",
                    signing_key_files=[skey],
                )
                for idx, skey in enumerate(payment_skey_files[5:])
            ]
            tx_signed = cluster.g_transaction.assemble_tx(
                tx_body_file=tx_output.out_file,
                witness_files=witness_files,
                tx_name=f"{temp_template}_from0",
            )

        # Incrementally sign the already signed Tx with rest of the skeys, excluding the
        # required skey
        for idx, skey in enumerate(payment_skey_files[1:5], start=1):
            # Sign multiple times with the same skey to see that it doesn't affect Tx fee
            for r in range(5):
                tx_signed = cluster.g_transaction.sign_tx(
                    tx_file=tx_signed,
                    signing_key_files=[skey],
                    tx_name=f"{temp_template}_from{idx}_r{r}",
                )

        # It is not possible to submit Tx with missing required skey
        assert tx_signed is not None  # for pyrefly
        with pytest.raises((clusterlib.CLIError, submit_api.SubmitApiError)) as excinfo:
            submit_utils.submit_tx(
                submit_method=submit_method,
                cluster_obj=cluster,
                tx_file=tx_signed,
                txins=tx_output.txins,
            )
        err_str = str(excinfo.value)
        assert "MissingVKeyWitnessesUTXOW" in err_str, err_str

        # Incrementally sign the already signed Tx with the required skey
        tx_signed = cluster.g_transaction.sign_tx(
            tx_file=tx_signed,
            signing_key_files=[payment_skey_files[0]],
            tx_name=f"{temp_template}_from_final",
        )

        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=tx_signed,
            txins=tx_output.txins,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
