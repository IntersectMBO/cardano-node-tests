"""Tests for basic transactions."""
import itertools
import logging
import re
import shutil
from typing import List

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.fixtures import SubRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import tx_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_api
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


param_submit_method = pytest.mark.parametrize(
    "submit_method",
    (
        "submit_cli",
        pytest.param(
            "submit_api",
            marks=pytest.mark.skipif(
                not shutil.which("cardano-submit-api"),
                reason="`cardano-submit-api` is not available",
            ),
        ),
    ),
)


@pytest.mark.testnets
@pytest.mark.smoke
class TestBasicTransactions:
    """Test basic transactions - transferring funds, transaction IDs."""

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
                f"addr_basic_ci{cluster_manager.cluster_instance_num}_0",
                f"addr_basic_ci{cluster_manager.cluster_instance_num}_1",
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *addrs,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )
        return addrs

    @pytest.fixture
    def byron_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new Byron payment addresses."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            byron_addrs = [
                clusterlib_utils.gen_byron_addr(
                    cluster_obj=cluster,
                    name_template=f"addr_payment_ci{cluster_manager.cluster_instance_num}_{i}",
                )
                for i in range(2)
            ]
            fixture_cache.value = byron_addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *byron_addrs,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )
        return byron_addrs

    @pytest.fixture
    def payment_addrs_disposable(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        temp_template = common.get_test_id(cluster)

        addrs = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_addr_0",
            f"{temp_template}_addr_1",
            cluster_obj=cluster,
        )

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *addrs,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )
        return addrs

    @pytest.fixture
    def payment_addrs_no_change(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses for `test_build_no_change`."""
        test_id = common.get_test_id(cluster)
        addrs = clusterlib_utils.create_payment_addr_records(
            f"{test_id}_addr_no_change_0",
            f"{test_id}_addr_no_change_1",
            cluster_obj=cluster,
        )

        # fund source address
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
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
            if cluster.tx_era != default_tx_era:
                cluster_default = cluster_nodes.get_cluster_type().get_cluster_obj(
                    tx_era=default_tx_era
                )
        elif cluster.tx_era:
            cluster_default = cluster_nodes.get_cluster_type().get_cluster_obj(tx_era="")

        return cluster_default

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 10_000_000))
    @pytest.mark.parametrize(
        "dst_addr_type", ("shelley", "byron"), ids=("dst_shelley", "dst_byron")
    )
    @pytest.mark.parametrize(
        "src_addr_type", ("shelley", "byron"), ids=("src_shelley", "src_byron")
    )
    @pytest.mark.dbsync
    def test_transfer_funds(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        byron_addrs: List[clusterlib.AddressRecord],
        src_addr_type: str,
        dst_addr_type: str,
        amount: int,
    ):
        """Send funds to payment address.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{src_addr_type}_{dst_addr_type}_{amount}"

        src_addr = byron_addrs[0] if src_addr_type == "byron" else payment_addrs[0]
        dst_addr = byron_addrs[1] if dst_addr_type == "byron" else payment_addrs[1]

        destinations = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=src_addr.address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            witness_count_add=1 if src_addr_type == "byron" else 0,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            assert (
                cluster.g_query.get_address_balance(src_addr.address)
                == dbsync_utils.get_utxo(address=src_addr.address).amount_sum
            ), f"Unexpected balance for source address `{src_addr.address}` in db-sync"

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.parametrize(
        "dst_addr_type", ("shelley", "byron"), ids=("dst_shelley", "dst_byron")
    )
    @pytest.mark.parametrize(
        "src_addr_type", ("shelley", "byron"), ids=("src_shelley", "src_byron")
    )
    @pytest.mark.dbsync
    def test_build_transfer_funds(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        byron_addrs: List[clusterlib.AddressRecord],
        src_addr_type: str,
        dst_addr_type: str,
    ):
        """Send funds to payment address.

        Uses `cardano-cli transaction build` command for building the transactions.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{src_addr_type}_{dst_addr_type}"
        amount = 1_500_000

        src_addr = byron_addrs[0] if src_addr_type == "byron" else payment_addrs[0]
        dst_addr = byron_addrs[1] if dst_addr_type == "byron" else payment_addrs[1]

        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        tx_output = cluster.g_transaction.build_tx(
            src_address=src_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=1_000_000,
            # TODO: cardano-node issue #4752
            witness_override=2 if src_addr_type == "byron" else None,
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

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_byron_fee_too_small(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        byron_addrs: List[clusterlib.AddressRecord],
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
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)
        except clusterlib.CLIError as exc:
            if "FeeTooSmallUTxO" not in str(exc):
                raise
            pytest.xfail("FeeTooSmallUTxO: see node issue #4752")

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.dbsync
    def test_build_no_change(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs_no_change: List[clusterlib.AddressRecord],
    ):
        """Send funds to payment address and balance the outputs so that there is no change.

        Uses `cardano-cli transaction build` command for building the transactions.

        Tests bug https://github.com/input-output-hk/cardano-node/issues/3041

        * try to build a Tx that sends all available funds, and extract fee amount
          from the error message
        * send all available funds minus fee from source address to destination address
        * check that no change UTxO was created
        """
        temp_template = common.get_test_id(cluster)

        src_addr = payment_addrs_no_change[0]
        src_address = src_addr.address
        dst_address = payment_addrs_no_change[1].address

        src_init_balance = cluster.g_query.get_address_balance(src_address)

        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        txouts_init = [clusterlib.TxOut(address=dst_address, amount=src_init_balance)]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                tx_files=tx_files,
                txouts=txouts_init,
            )
        fee_match = re.search(r"negative: Lovelace \(-([0-9]*)\) lovelace", str(excinfo.value))
        assert fee_match

        fee = int(fee_match.group(1))
        amount = src_init_balance - fee
        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]

        tx_output = cluster.g_transaction.build_tx(
            src_address=src_address,
            tx_name=temp_template,
            tx_files=tx_files,
            txouts=txouts,
            change_address=src_address,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        clusterlib_utils.check_txins_spent(cluster_obj=cluster, txins=tx_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert not clusterlib.filter_utxos(
            utxos=out_utxos, address=src_address
        ), f"Incorrect balance for source address `{src_address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @param_submit_method
    @pytest.mark.dbsync
    def test_transfer_all_funds(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs_disposable: List[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Send ALL funds from one payment address to another.

        * send all available funds from 1 source address to 1 destination address
        * check expected balance for destination addresses
        * check that balance for source address is 0 Lovelace
        * check output of the `transaction view` command
        """
        temp_template = f"{common.get_test_id(cluster)}_{submit_method}"

        if submit_method == "submit_api" and not submit_api.is_running():
            pytest.skip("The cardano-submit-api REST service is not running.")

        src_address = payment_addrs_disposable[1].address
        dst_address = payment_addrs_disposable[0].address

        # amount value -1 means all available funds
        destinations = [clusterlib.TxOut(address=dst_address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs_disposable[1].skey_file])

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

        if submit_method == "submit_cli":
            cluster.g_transaction.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)
        else:
            submit_api.submit_tx(tx_file=out_file_signed)
            cluster.wait_for_new_block(2)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert not clusterlib.filter_utxos(
            utxos=out_utxos, address=src_address
        ), f"Incorrect balance for source address `{src_address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for destination address `{dst_address}`"

        # check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_funds_to_valid_address(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Send funds to a valid payment address.

        The destination address is a valid address that was generated sometime
        in the past. The test verifies it is possible to use a valid address
        even though it was not generated while running a specific cardano
        network.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        * check min UTxO value
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = "addr_test1vpst87uzwafqkxumyf446zr2jsyn44cfpu9fe8yqanyuh6glj2hkl"

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_output = cluster.g_transaction.send_funds(
            src_address=src_address,
            destinations=destinations,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        # check min UTxO value
        min_value = cluster.g_transaction.calculate_min_req_utxo(txouts=destinations)
        assert min_value.value == tx_common.MIN_UTXO_VALUE

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_get_txid(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Get transaction ID (txid) from transaction body.

        Transaction ID is a hash of transaction body and doesn't change for a signed TX.

        * send funds from 1 source address to 1 destination address
        * get txid from transaction body
        * get txid from signed transaction
        * check that txid from transaction body matches the txid from signed transaction
        * check that txid has expected length
        * check that the txid is listed in UTxO hashes for both source and destination addresses
        """
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        tx_raw_output = cluster.g_transaction.send_funds(
            src_address=src_address,
            destinations=destinations,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        txid_body = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
        txid_signed = cluster.g_transaction.get_txid(
            tx_file=tx_raw_output.out_file.with_suffix(".signed")
        )
        assert txid_body == txid_signed
        assert len(txid_body) == 64

        utxo_src = cluster.g_query.get_utxo(txin=f"{txid_body}#1")[0]  # change UTxO
        assert txid_body in utxo_src.utxo_hash

        utxo_dst = cluster.g_query.get_utxo(txin=f"{txid_body}#0")[0]
        assert txid_body in utxo_dst.utxo_hash

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_extra_signing_keys(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Send a transaction with extra signing key.

        Check that it is possible to use unneeded signing key in addition to the necessary
        signing keys for signing the transaction.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        # use extra signing key
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file, payment_addrs[1].skey_file]
        )
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # it should be possible to submit a transaction with extra signing key
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=src_address, tx_name=temp_template, txouts=destinations, tx_files=tx_files
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_duplicate_signing_keys(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Send a transaction with duplicate signing key.

        Check that it is possible to specify the same signing key twice.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        # use extra signing key
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file, payment_addrs[0].skey_file]
        )
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # it should be possible to submit a transaction with duplicate signing key
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=src_address, tx_name=temp_template, txouts=destinations, tx_files=tx_files
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("file_type", ("tx_body", "tx"))
    @pytest.mark.dbsync
    def test_sign_wrong_file(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        file_type: str,
    ):
        """Sign other file type than the one specified by command line option (Tx vs Tx body).

        * specify Tx file and pass Tx body file
        * specify Tx body file and pass Tx file
        """
        temp_template = f"{common.get_test_id(cluster)}_{file_type}"
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
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

        tx_signed_again = cluster.g_transaction.sign_tx(
            **cli_args,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        # check that the Tx can be successfully submitted
        cluster.g_transaction.submit_tx(tx_file=tx_signed_again, txins=tx_raw_output.txins)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_no_txout(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
    ):
        """Send transaction with just fee, no UTxO is produced.

        * submit a transaction where all funds available on source address is used for fee
        * check that no UTxOs are created by the transaction
        * check that there are no funds left on source address
        """
        temp_template = common.get_test_id(cluster)

        src_record = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_0", cluster_obj=cluster
        )[0]
        clusterlib_utils.fund_from_faucet(
            src_record,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=2_000_000,
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[src_record.skey_file])
        fee = cluster.g_query.get_address_balance(src_record.address)
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=src_record.address, tx_name=temp_template, tx_files=tx_files, fee=fee
        )

        assert not tx_raw_output.txouts, "Transaction has unexpected txouts"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    def test_missing_tx_out(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
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
        VERSIONS.transaction_era == VERSIONS.SHELLEY,
        reason="doesn't run with Shelley TX",
    )
    @pytest.mark.dbsync
    def test_missing_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
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
        tx_raw_output = tx_raw_template._replace(invalid_hereafter=None)

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

        tx_signed_file = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=temp_template,
            signing_key_files=[payment_addrs[0].skey_file],
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_file, txins=tx_raw_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for source address `{src_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_multiple_same_txins(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
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
                *cluster.g_transaction.tx_era_arg,
            ]
        )

        tx_signed_file = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=temp_template,
            signing_key_files=[payment_addrs[0].skey_file],
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_file, txins=tx_raw_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee
        ), f"Incorrect balance for source address `{src_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_multiple_same_txins(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
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

        cluster.cli(
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
                *cluster.g_transaction.tx_era_arg,
            ]
        )
        assert tx_raw_output.out_file.exists()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALONZO,
        reason="doesn't run with TX era < Alonzo",
    )
    @pytest.mark.dbsync
    def test_utxo_with_datum_hash(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Create a UTxO with datum hash in a regular address and spend it.

        * create a UTxO with a datum hash at the payment address
        * check that the UTxO was created with the respective datum hash
        * spend the UTxO (not providing the datum hash)
        * check that the UTxO was spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        payment_addr_1 = payment_addrs[0].address
        payment_addr_2 = payment_addrs[1].address

        # step 1: create utxo with a datum hash
        datum_hash = cluster.g_transaction.get_hash_script_data(
            script_data_value="42",
        )

        destinations_1 = [
            clusterlib.TxOut(address=payment_addr_2, amount=amount, datum_hash=datum_hash)
        ]
        tx_files_1 = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_output_1 = cluster.g_transaction.send_tx(
            src_address=payment_addr_1,
            tx_name=f"{temp_template}_step_1",
            txouts=destinations_1,
            tx_files=tx_files_1,
        )

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output_1.out_file)
        datum_hash_utxo = cluster.g_query.get_utxo(txin=f"{txid}#0")
        assert datum_hash_utxo[0].datum_hash, "No datum hash"

        # step 2: spend the created utxo
        destinations_2 = [clusterlib.TxOut(address=payment_addr_1, amount=-1)]
        tx_files_2 = clusterlib.TxFiles(signing_key_files=[payment_addrs[1].skey_file])

        tx_raw_output_2 = cluster.g_transaction.send_tx(
            src_address=payment_addr_2,
            tx_name=f"{temp_template}_step_2",
            txins=datum_hash_utxo,
            txouts=destinations_2,
            tx_files=tx_files_2,
        )

        # check that the UTxO was spent
        assert not cluster.g_query.get_utxo(txin=f"{txid}#0"), "UTxO not spent"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_far_future_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Send a transaction with ttl far in the future."""
        temp_template = common.get_test_id(cluster)

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        destinations = [clusterlib.TxOut(address=dst_addr.address, amount=2_000_000)]

        ttl = cluster.g_query.get_slot_no() + 10_000_000
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_addr.address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            invalid_hereafter=ttl,
        )

        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_addr.address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
            invalid_hereafter=ttl,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # it should be possible to submit a transaction with ttl far in the future
        cluster.g_transaction.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_WRONG_ERA
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize(
        "cluster_default_tx_era",
        (True, False),
        ids=("explicit_tx_era", "implicit_tx_era"),
        indirect=True,
    )
    def test_default_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_default_tx_era: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
        request: FixtureRequest,
    ):
        """Test default Tx era.

        * check that default Tx era is implicit
        * check that default Tx era can be specified explicitly
        """
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"

        cluster = cluster_default_tx_era

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        if use_build_cmd:
            tx_output = cluster.g_transaction.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                tx_files=tx_files,
                txouts=destinations,
                fee_buffer=1_000_000,
            )
            tx_signed_fund = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed_fund, txins=tx_output.txins)
        else:
            tx_output = cluster.g_transaction.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=temp_template,
                tx_files=tx_files,
            )

        # check `transaction view` command, this will check if the tx era is the expected
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output)


@pytest.mark.testnets
@pytest.mark.smoke
class TestMultiInOut:
    """Test transactions with multiple txins and/or txouts."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 201 new payment addresses."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[
                    f"multi_in_out_addr_ci{cluster_manager.cluster_instance_num}_{i}"
                    for i in range(201)
                ],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=90_000_000_000,
        )

        return addrs

    def _from_to_transactions(
        self,
        cluster_obj: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        tx_name: str,
        from_num: int,
        to_num: int,
        amount: int,
        use_build_cmd=False,
    ):
        """Test 1 tx from `from_num` payment addresses to `to_num` payment addresses."""
        src_address = payment_addrs[0].address
        # addr1..addr<from_num+1>
        from_addr_recs = payment_addrs[1 : from_num + 1]
        # addr<from_num+1>..addr<from_num+to_num+1>
        dst_addresses = [
            payment_addrs[i].address for i in range(from_num + 1, from_num + to_num + 1)
        ]

        # fund "from" addresses
        # Using `src_address` to fund the "from" addresses. In `send_tx`, all remaining change is
        # returned to `src_address`, so it should always have enough funds. The "from" addresses has
        # zero balance after each test.
        fund_amount = int(amount * len(dst_addresses) / len(from_addr_recs))
        # min UTxO on testnets is 1.x ADA
        fund_amount = fund_amount if fund_amount >= 1_500_000 else 1_500_000
        fund_dst = [
            clusterlib.TxOut(address=d.address, amount=fund_amount) for d in from_addr_recs[:-1]
        ]
        # add more funds to the last "from" address so it can cover TX fee
        last_from_addr_rec = from_addr_recs[-1]
        fund_dst.append(
            clusterlib.TxOut(address=last_from_addr_rec.address, amount=fund_amount + 5_000_000)
        )
        fund_tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        if use_build_cmd:
            tx_output_fund = cluster_obj.g_transaction.build_tx(
                src_address=src_address,
                tx_name=f"{tx_name}_add_funds",
                tx_files=fund_tx_files,
                txouts=fund_dst,
                fee_buffer=1_000_000,
            )
            tx_signed_fund = cluster_obj.g_transaction.sign_tx(
                tx_body_file=tx_output_fund.out_file,
                signing_key_files=fund_tx_files.signing_key_files,
                tx_name=f"{tx_name}_add_funds",
            )
            cluster_obj.g_transaction.submit_tx(tx_file=tx_signed_fund, txins=tx_output_fund.txins)
        else:
            cluster_obj.g_transaction.send_funds(
                src_address=src_address,
                destinations=fund_dst,
                tx_name=f"{tx_name}_add_funds",
                tx_files=fund_tx_files,
            )

        # create TX data
        _txins = [cluster_obj.g_query.get_utxo(address=r.address) for r in from_addr_recs]
        # flatten the list of lists that is _txins
        txins = list(itertools.chain.from_iterable(_txins))
        txouts = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]
        tx_files = clusterlib.TxFiles(signing_key_files=[r.skey_file for r in from_addr_recs])

        # send TX
        if use_build_cmd:
            tx_raw_output = cluster_obj.g_transaction.build_tx(
                src_address=from_addr_recs[0].address,
                tx_name=tx_name,
                txins=txins,
                txouts=txouts,
                change_address=src_address,
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
            tx_signed = cluster_obj.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=tx_name,
            )
            cluster_obj.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
        else:
            tx_raw_output = cluster_obj.g_transaction.send_tx(
                src_address=src_address,  # change is returned to `src_address`
                tx_name=tx_name,
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
            )

        # check balances
        out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[
            0
        ].amount == clusterlib.calculate_utxos_balance(
            tx_raw_output.txins
        ) - tx_raw_output.fee - amount * len(
            dst_addresses
        ), f"Incorrect balance for source address `{src_address}`"

        for addr in dst_addresses:
            assert (
                clusterlib.filter_utxos(utxos=out_utxos, address=addr)[0].amount == amount
            ), f"Incorrect balance for destination address `{addr}`"

        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_10_transactions(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send 10 transactions to payment address.

        * send funds from 1 source address to 1 destination address in 10 separate transactions
        * check expected balances for both source and destination addresses
        """
        temp_template = common.get_test_id(cluster)
        no_of_transactions = 10

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.g_query.get_address_balance(src_address)
        dst_init_balance = cluster.g_query.get_address_balance(dst_address)

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.g_transaction.calculate_tx_ttl()

        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            dst_addresses=[dst_address],
            tx_files=tx_files,
            ttl=ttl,
        )
        amount = 2_000_000
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        for i in range(no_of_transactions):
            tx_raw_output = cluster.g_transaction.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=f"{temp_template}_{i}",
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
            )
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        assert (
            cluster.g_query.get_address_balance(src_address)
            == src_init_balance - fee * no_of_transactions - amount * no_of_transactions
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.g_query.get_address_balance(dst_address)
            == dst_init_balance + amount * no_of_transactions
        ), f"Incorrect balance for destination address `{dst_address}`"

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 10_000_000))
    @pytest.mark.dbsync
    def test_transaction_to_10_addrs_from_1_addr(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
        use_build_cmd: bool,
    ):
        """Test 1 transaction from 1 payment address to 10 payment addresses.

        * send funds from 1 source address to 10 destination addresses
        * check expected balances for both source and destination addresses
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=f"{common.get_test_id(cluster)}_{amount}_{use_build_cmd}",
            from_num=1,
            to_num=10,
            amount=amount,
            use_build_cmd=use_build_cmd,
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 10_000_000))
    @pytest.mark.dbsync
    def test_transaction_to_1_addr_from_10_addrs(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
        use_build_cmd: bool,
    ):
        """Test 1 transaction from 10 payment addresses to 1 payment address.

        * send funds from 10 source addresses to 1 destination address
        * check expected balances for both source and destination addresses
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=f"{common.get_test_id(cluster)}_{amount}_{use_build_cmd}",
            from_num=10,
            to_num=1,
            amount=amount,
            use_build_cmd=use_build_cmd,
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 10_000_000))
    @pytest.mark.dbsync
    def test_transaction_to_10_addrs_from_10_addrs(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
        use_build_cmd: bool,
    ):
        """Test 1 transaction from 10 payment addresses to 10 payment addresses.

        * send funds from 10 source addresses to 10 destination addresses
        * check expected balances for both source and destination addresses
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=f"{common.get_test_id(cluster)}_{amount}_{use_build_cmd}",
            from_num=10,
            to_num=10,
            amount=amount,
            use_build_cmd=use_build_cmd,
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 5_000_000))
    @pytest.mark.dbsync
    def test_transaction_to_100_addrs_from_50_addrs(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
        use_build_cmd: bool,
    ):
        """Test 1 transaction from 50 payment addresses to 100 payment addresses.

        * send funds from 50 source addresses to 100 destination addresses
        * check expected balances for both source and destination addresses
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=f"{common.get_test_id(cluster)}_{amount}_{use_build_cmd}",
            from_num=50,
            to_num=100,
            amount=amount,
            use_build_cmd=use_build_cmd,
        )


@pytest.mark.testnets
@pytest.mark.smoke
class TestIncrementalSigning:
    """Test incremental signing of transactions."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[
                    f"multi_addr_inc_signing_ci{cluster_manager.cluster_instance_num}_{i}"
                    for i in range(10)
                ],
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

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("tx_is", ("witnessed", "signed"))
    @pytest.mark.dbsync
    def test_incremental_signing(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
        tx_is: str,
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
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}_{tx_is}"
        amount = 2_000_000

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        payment_skey_files = [p.skey_file for p in payment_addrs]

        destinations = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(
            signing_key_files=payment_skey_files,
        )

        if use_build_cmd:
            tx_output = cluster.g_transaction.build_tx(
                src_address=src_addr.address,
                tx_name=temp_template,
                tx_files=tx_files,
                txouts=destinations,
                fee_buffer=1_000_000,
                witness_override=len(payment_skey_files),
            )
        else:
            fee = cluster.g_transaction.calculate_tx_fee(
                src_address=src_addr.address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
                witness_count_add=len(payment_skey_files),
            )
            tx_output = cluster.g_transaction.build_raw_tx(
                src_address=src_addr.address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
                fee=fee,
            )

        # sign or witness Tx body with part of the skeys and thus create Tx file that will be used
        # for incremental signing
        if tx_is == "signed":
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=payment_skey_files[5:],
                tx_name=f"{temp_template}_from0",
            )
        else:
            # sign Tx body using witness files
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

        # incrementally sign the already signed Tx with rest of the skeys, excluding the
        # required skey
        for idx, skey in enumerate(payment_skey_files[1:5], start=1):
            # sign multiple times with the same skey to see that it doesn't affect Tx fee
            for r in range(5):
                tx_signed = cluster.g_transaction.sign_tx(
                    tx_file=tx_signed,
                    signing_key_files=[skey],
                    tx_name=f"{temp_template}_from{idx}_r{r}",
                )

        # it is not possible to submit Tx with missing required skey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)
        err_str = str(excinfo.value)
        assert "(MissingVKeyWitnessesUTXOW" in err_str, err_str

        # incrementally sign the already signed Tx with the required skey
        tx_signed = cluster.g_transaction.sign_tx(
            tx_file=tx_signed,
            signing_key_files=[payment_skey_files[0]],
            tx_name=f"{temp_template}_from_final",
        )

        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
