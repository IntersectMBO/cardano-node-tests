"""Tests for general transactions.

* transferring funds (from 1 address to many, many to 1, many to many)
* not balanced transactions
* other negative tests like duplicated transaction, sending funds to wrong addresses,
  wrong fee, wrong ttl
* transactions with metadata
* transactions with many UTxOs
"""
import functools
import itertools
import json
import logging
import random
import re
import string
import time
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple

import allure
import cbor2
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"

MAX_LOVELACE_AMOUNT = 2**64
MIN_LOVELACE_AMOUNT = 999_978

ADDR_ALPHABET = list(f"{string.ascii_lowercase}{string.digits}")


def _get_raw_tx_values(
    cluster_obj: clusterlib.ClusterLib,
    tx_name: str,
    src_record: clusterlib.AddressRecord,
    dst_record: clusterlib.AddressRecord,
    for_build_command: bool = False,
) -> clusterlib.TxRawOutput:
    """Get values for building raw TX using `clusterlib.build_raw_tx_bare`."""
    src_address = src_record.address
    dst_address = dst_record.address

    tx_files = clusterlib.TxFiles(signing_key_files=[src_record.skey_file])
    ttl = cluster_obj.calculate_tx_ttl()

    if for_build_command:
        fee = 0
        min_change = 1_500_000
    else:
        fee = cluster_obj.calculate_tx_fee(
            src_address=src_address,
            tx_name=tx_name,
            dst_addresses=[dst_address],
            tx_files=tx_files,
            ttl=ttl,
        )
        min_change = 0

    src_addr_highest_utxo = cluster_obj.get_utxo_with_highest_amount(src_address)

    # use only the UTxO with the highest amount
    txins = [src_addr_highest_utxo]
    txouts = [
        clusterlib.TxOut(
            address=dst_address, amount=src_addr_highest_utxo.amount - fee - min_change
        ),
    ]
    out_file = Path(f"{helpers.get_timestamped_rand_str()}_tx.body")

    return clusterlib.TxRawOutput(
        txins=txins,
        txouts=txouts,
        tx_files=tx_files,
        out_file=out_file,
        fee=fee,
        invalid_hereafter=ttl,
    )


def _get_txins_txouts(
    txins: List[clusterlib.UTXOData], txouts: List[clusterlib.TxOut]
) -> Tuple[List[str], List[str]]:
    txins_combined = [f"{x.utxo_hash}#{x.utxo_ix}" for x in txins]
    txouts_combined = [f"{x.address}+{x.amount}" for x in txouts]
    return txins_combined, txouts_combined


@pytest.mark.testnets
@pytest.mark.smoke
class TestBasic:
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

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 10_000_000))
    def test_transfer_funds(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
    ):
        """Send funds to payment address.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        """
        temp_template = f"{common.get_test_id(cluster)}_{amount}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_output = cluster.send_funds(
            src_address=src_address,
            destinations=destinations,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            assert (
                cluster.get_address_balance(src_address)
                == dbsync_utils.get_utxo(address=src_address).amount_sum
            ), f"Unexpected balance for source address `{src_address}` in db-sync"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @pytest.mark.dbsync
    def test_build_transfer_funds(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Send funds to payment address.

        Uses `cardano-cli transaction build` command for building the transactions.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        """
        temp_template = common.get_test_id(cluster)
        amount = 1_500_000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_output = cluster.build_tx(
            src_address=src_address,
            tx_name=temp_template,
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=1_000_000,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        assert (
            cluster.get_address_balance(src_address) == src_init_balance - amount - tx_output.fee
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
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

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        txouts_init = [clusterlib.TxOut(address=dst_address, amount=src_init_balance)]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
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

        tx_output = cluster.build_tx(
            src_address=src_address,
            tx_name=temp_template,
            tx_files=tx_files,
            txouts=txouts,
            change_address=src_address,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        assert (
            cluster.get_address_balance(src_address) == 0
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_transfer_all_funds(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send ALL funds from one payment address to another.

        * send all available funds from 1 source address to 1 destination address
        * check expected balance for destination addresses
        * check that balance for source address is 0 Lovelace
        * check output of the `transaction view` command
        """
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs[1].address
        dst_address = payment_addrs[0].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        # amount value -1 means all available funds
        destinations = [clusterlib.TxOut(address=dst_address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[1].skey_file])

        fee = cluster.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

        assert (
            cluster.get_address_balance(src_address) == 0
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address)
            == dst_init_balance + src_init_balance - tx_raw_output.fee
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
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = "addr_test1vpst87uzwafqkxumyf446zr2jsyn44cfpu9fe8yqanyuh6glj2hkl"

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_output = cluster.send_funds(
            src_address=src_address,
            destinations=destinations,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

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
        tx_raw_output = cluster.send_funds(
            src_address=src_address,
            destinations=destinations,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        txid_body = cluster.get_txid(tx_body_file=tx_raw_output.out_file)
        txid_signed = cluster.get_txid(tx_file=tx_raw_output.out_file.with_suffix(".signed"))
        assert txid_body == txid_signed
        assert len(txid_body) == 64

        utxo_src = cluster.get_utxo(txin=f"{txid_body}#1")[0]  # change UTxO
        assert txid_body in utxo_src.utxo_hash

        utxo_dst = cluster.get_utxo(txin=f"{txid_body}#0")[0]
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

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        # use extra signing key
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file, payment_addrs[1].skey_file]
        )
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # it should be possible to submit a transaction with extra signing key
        tx_raw_output = cluster.send_tx(
            src_address=src_address, tx_name=temp_template, txouts=destinations, tx_files=tx_files
        )

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
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

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        # use extra signing key
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file, payment_addrs[0].skey_file]
        )
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # it should be possible to submit a transaction with duplicate signing key
        tx_raw_output = cluster.send_tx(
            src_address=src_address, tx_name=temp_template, txouts=destinations, tx_files=tx_files
        )

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

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
        fee = cluster.get_address_balance(src_record.address)
        tx_raw_output = cluster.send_tx(
            src_address=src_record.address, tx_name=temp_template, tx_files=tx_files, fee=fee
        )

        assert not tx_raw_output.txouts, "Transaction has unexpected txouts"
        assert (
            cluster.get_address_balance(src_record.address) == 0
        ), f"Incorrect balance for source address `{src_record.address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    def test_missing_tx_out(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Build a transaction with a missing `--tx-out` parameter."""
        temp_template = common.get_test_id(cluster)

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=payment_addrs[0],
            dst_record=payment_addrs[1],
        )
        txins, __ = _get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

        cli_args = [
            "transaction",
            "build-raw",
            "--invalid-hereafter",
            str(tx_raw_output.invalid_hereafter),
            "--fee",
            str(tx_raw_output.fee),
            "--out-file",
            str(tx_raw_output.out_file),
            *cluster._prepend_flag("--tx-in", txins),
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

        init_balance = cluster.get_address_balance(src_address)

        tx_raw_template = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=payment_addrs[0],
            dst_record=payment_addrs[0],
        )
        txins, txouts = _get_txins_txouts(tx_raw_template.txins, tx_raw_template.txouts)
        tx_raw_output = tx_raw_template._replace(invalid_hereafter=None)

        cluster.cli(
            [
                "transaction",
                "build-raw",
                "--fee",
                str(tx_raw_output.fee),
                "--out-file",
                str(tx_raw_output.out_file),
                *cluster._prepend_flag("--tx-in", txins),
                *cluster._prepend_flag("--tx-out", txouts),
                *cluster.tx_era_arg,
            ]
        )

        tx_signed_file = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=temp_template,
            signing_key_files=[payment_addrs[0].skey_file],
        )
        cluster.submit_tx(tx_file=tx_signed_file, txins=tx_raw_output.txins)

        assert (
            cluster.get_address_balance(src_address) == init_balance - tx_raw_output.fee
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

        init_balance = cluster.get_address_balance(src_address)

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=payment_addrs[0],
            dst_record=payment_addrs[0],
        )
        txins, txouts = _get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

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
                *cluster._prepend_flag("--tx-in", txins),
                *cluster._prepend_flag("--tx-out", txouts),
                *cluster.tx_era_arg,
            ]
        )

        tx_signed_file = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=temp_template,
            signing_key_files=[payment_addrs[0].skey_file],
        )
        cluster.submit_tx(tx_file=tx_signed_file, txins=tx_raw_output.txins)

        assert (
            cluster.get_address_balance(src_address) == init_balance - tx_raw_output.fee
        ), f"Incorrect balance for source address `{src_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    def test_build_multiple_same_txins(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Build a transaction with multiple identical txins.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=payment_addrs[0],
            dst_record=payment_addrs[1],
            for_build_command=True,
        )
        txins, txouts = _get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

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
                *cluster._prepend_flag("--tx-in", txins),
                *cluster._prepend_flag("--tx-out", txouts),
                *cluster.tx_era_arg,
            ]
        )
        assert tx_raw_output.out_file.exists()


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
            tx_output_fund = cluster_obj.build_tx(
                src_address=src_address,
                tx_name=f"{tx_name}_add_funds",
                tx_files=fund_tx_files,
                txouts=fund_dst,
                fee_buffer=1_000_000,
            )
            tx_signed_fund = cluster_obj.sign_tx(
                tx_body_file=tx_output_fund.out_file,
                signing_key_files=fund_tx_files.signing_key_files,
                tx_name=f"{tx_name}_add_funds",
            )
            cluster_obj.submit_tx(tx_file=tx_signed_fund, txins=tx_output_fund.txins)
        else:
            cluster_obj.send_funds(
                src_address=src_address,
                destinations=fund_dst,
                tx_name=f"{tx_name}_add_funds",
                tx_files=fund_tx_files,
            )

        # record initial balances
        src_init_balance = cluster_obj.get_address_balance(src_address)
        from_init_total_balance = functools.reduce(
            lambda x, y: x + y,
            (cluster_obj.get_address_balance(r.address) for r in from_addr_recs),
            0,
        )
        dst_init_balances = {addr: cluster_obj.get_address_balance(addr) for addr in dst_addresses}

        # create TX data
        _txins = [cluster_obj.get_utxo(address=r.address) for r in from_addr_recs]
        # flatten the list of lists that is _txins
        txins = list(itertools.chain.from_iterable(_txins))
        txouts = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]
        tx_files = clusterlib.TxFiles(signing_key_files=[r.skey_file for r in from_addr_recs])

        # send TX
        if use_build_cmd:
            tx_raw_output = cluster_obj.build_tx(
                src_address=from_addr_recs[0].address,
                tx_name=tx_name,
                txins=txins,
                txouts=txouts,
                change_address=src_address,
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
            tx_signed = cluster_obj.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=tx_name,
            )
            cluster_obj.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
        else:
            tx_raw_output = cluster_obj.send_tx(
                src_address=src_address,  # change is returned to `src_address`
                tx_name=tx_name,
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
            )

        # check balances
        from_final_balance = functools.reduce(
            lambda x, y: x + y,
            (cluster_obj.get_address_balance(r.address) for r in from_addr_recs),
            0,
        )
        src_final_balance = cluster_obj.get_address_balance(src_address)

        assert (
            from_final_balance == 0
        ), f"The output addresses should have no balance, they have {from_final_balance}"

        assert (
            src_final_balance
            == src_init_balance
            + from_init_total_balance
            - tx_raw_output.fee
            - amount * len(dst_addresses)
        ), f"Incorrect balance for source address `{src_address}`"

        for addr in dst_addresses:
            assert (
                cluster_obj.get_address_balance(addr) == dst_init_balances[addr] + amount
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

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        fee = cluster.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            dst_addresses=[dst_address],
            tx_files=tx_files,
            ttl=ttl,
        )
        amount = 2_000_000
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        for i in range(no_of_transactions):
            tx_raw_output = cluster.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=f"{temp_template}_{i}",
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
            )
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - fee * no_of_transactions - amount * no_of_transactions
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address)
            == dst_init_balance + amount * no_of_transactions
        ), f"Incorrect balance for destination address `{dst_address}`"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 10_000_000))
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
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
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 10_000_000))
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
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
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 10_000_000))
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
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
    @pytest.mark.parametrize("amount", (1_500_000, 2_000_000, 5_000_000))
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
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


@pytest.mark.skipif(
    VERSIONS.cluster_era != VERSIONS.transaction_era,
    reason="expensive test, skip when cluster era is different from TX era",
)
@pytest.mark.long
class TestManyUTXOs:
    """Test transaction with many UTxOs and small amounts of Lovelace."""

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
                *[f"tiny_tx_addr_ci{cluster_manager.cluster_instance_num}_{i}" for i in range(3)],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=800_000_000_000,
        )

        return addrs

    def _from_to_transactions(
        self,
        cluster_obj: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        out_addrs: List[clusterlib.AddressRecord],
        tx_name: str,
        amount: int,
    ):
        """Send `amount` of Lovelace to each address in `out_addrs`."""
        src_address = payment_addr.address
        dst_addresses = [rec.address for rec in out_addrs]

        # create TX data
        txouts = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        # send TX
        cluster_obj.send_tx(
            src_address=src_address,  # change is returned to `src_address`
            tx_name=tx_name,
            txouts=txouts,
            tx_files=tx_files,
            join_txouts=False,
        )

    @pytest.fixture
    def many_utxos(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> Tuple[clusterlib.AddressRecord, clusterlib.AddressRecord]:
        """Generate many UTxOs (100000+) with 1-2 ADA."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            temp_template = common.get_test_id(cluster)

            LOGGER.info("Generating lot of UTxO addresses, it will take a while.")
            start = time.time()
            payment_addr = payment_addrs[0]
            out_addrs1 = [payment_addrs[1] for __ in range(200)]
            out_addrs2 = [payment_addrs[2] for __ in range(200)]
            out_addrs = [*out_addrs1, *out_addrs2]

            for i in range(25):
                for multiple in range(1, 21):
                    less_than_1_ada = int(float(multiple / 20) * 1_000_000)
                    amount = less_than_1_ada + 1_000_000

                    # repeat transaction when "BadInputsUTxO" error happens
                    excp: Optional[clusterlib.CLIError] = None
                    for r in range(2):
                        if r > 0:
                            cluster.wait_for_new_block(2)
                        try:
                            self._from_to_transactions(
                                cluster_obj=cluster,
                                payment_addr=payment_addr,
                                tx_name=f"{temp_template}_{amount}_r{r}_{i}",
                                out_addrs=out_addrs,
                                amount=amount,
                            )
                        except clusterlib.CLIError as err:
                            # The "BadInputsUTxO" error happens when a single UTxO is used in two
                            # transactions. This can happen from time to time, we stress
                            # the network here and waiting for 2 blocks may not be enough to get a
                            # transaction through.
                            if "BadInputsUTxO" not in str(err):
                                raise
                            excp = err
                        else:
                            break
                    else:
                        if excp:
                            raise excp

            # create 200 UTxOs with 10 ADA
            cluster.wait_for_new_block(2)
            self._from_to_transactions(
                cluster_obj=cluster,
                payment_addr=payment_addr,
                tx_name=f"{temp_template}_big",
                out_addrs=out_addrs2,
                amount=10_000_000,
            )
            end = time.time()

            retval = payment_addrs[1], payment_addrs[2]
            fixture_cache.value = retval

        num_of_utxo = len(cluster.get_utxo(address=payment_addrs[1].address)) + len(
            cluster.get_utxo(address=payment_addrs[2].address)
        )
        LOGGER.info(f"Generated {num_of_utxo} of UTxO addresses in {end - start} seconds.")

        return retval

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("amount", (1_500_000, 5_000_000, 10_000_000))
    def test_mini_transactions(
        self,
        cluster: clusterlib.ClusterLib,
        many_utxos: Tuple[clusterlib.AddressRecord, clusterlib.AddressRecord],
        amount: int,
    ):
        """Test transaction with many UTxOs (300+) with small amounts of ADA (1-10).

        * use source address with many UTxOs (100000+)
        * use destination address with many UTxOs (100000+)
        * sent transaction with many UTxOs (300+) with tiny amounts of Lovelace from source address
          to destination address
        * check expected balances for both source and destination addresses
        """
        temp_template = f"{common.get_test_id(cluster)}_{amount}"
        big_funds_idx = -190

        src_address = many_utxos[0].address
        dst_address = many_utxos[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[many_utxos[0].skey_file])

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        # sort UTxOs by amount
        utxos_sorted = sorted(cluster.get_utxo(address=src_address), key=lambda x: x.amount)

        # select 350 UTxOs, so we are in a limit of command line arguments lenght and size of the TX
        txins = random.sample(utxos_sorted[:big_funds_idx], k=350)
        # add several UTxOs with "big funds" so we can pay fees
        txins.extend(utxos_sorted[-30:])

        ttl = cluster.calculate_tx_ttl()
        fee = cluster.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txins=txins,
            txouts=destinations,
            tx_files=tx_files,
            ttl=ttl,
        )

        # optimize list of txins so the total amount of funds in selected UTxOs is close
        # to the amount of needed funds
        needed_funds = amount + fee + 5_000_000  # add a buffer
        total_funds = functools.reduce(lambda x, y: x + y.amount, txins, 0)
        funds_optimized = total_funds
        txins_optimized = txins[:]
        while funds_optimized > needed_funds:
            popped_txin = txins_optimized.pop()
            funds_optimized -= popped_txin.amount
            if funds_optimized < needed_funds:
                txins_optimized.append(popped_txin)
                break

        # build, sign and submit the transaction
        txins_filtered, txouts_balanced = cluster.get_tx_ins_outs(
            src_address=src_address,
            tx_files=tx_files,
            txins=txins_optimized,
            txouts=destinations,
            fee=fee,
        )
        tx_raw_output = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_tx.body",
            txins=txins_filtered,
            txouts=txouts_balanced,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
        )
        tx_signed_file = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=temp_template,
            signing_key_files=tx_files.signing_key_files,
        )
        cluster.submit_tx(tx_file=tx_signed_file, txins=tx_raw_output.txins)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)


@pytest.mark.testnets
@pytest.mark.smoke
class TestNotBalanced:
    """Tests for not balanced transactions."""

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
        return cluster.get_utxo_with_highest_amount(payment_addrs[0].address)

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
        ttl = cluster.calculate_tx_ttl()

        fee = cluster.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            dst_addresses=[dst_address],
            tx_files=tx_files,
            ttl=ttl,
        )

        src_addr_highest_utxo = cluster.get_utxo_with_highest_amount(src_address)

        # use only the UTxO with the highest amount
        txins = [src_addr_highest_utxo]
        # try to transfer +1 Lovelace more than available and use a negative change (-1)
        txouts = [
            clusterlib.TxOut(address=dst_address, amount=src_addr_highest_utxo.amount - fee + 1),
            clusterlib.TxOut(address=src_address, amount=-1),
        ]
        assert txins[0].amount - txouts[0].amount - fee == txouts[-1].amount

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_raw_tx_bare(
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
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(transfer_add=st.integers(min_value=0, max_value=MAX_LOVELACE_AMOUNT))
    @common.hypothesis_settings()
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
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        # use only the UTxO with the highest amount
        txins = [pbt_highest_utxo]
        # try to transfer whole balance
        txouts = [
            clusterlib.TxOut(address=dst_address, amount=pbt_highest_utxo.amount + transfer_add),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
            )
        assert "The net balance of the transaction is negative" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(change_amount=st.integers(min_value=2_000_000, max_value=MAX_LOVELACE_AMOUNT))
    @common.hypothesis_settings()
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
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        fee = 200_000
        transferred_amount = pbt_highest_utxo.amount - fee

        out_file_tx = f"{temp_template}_tx.body"
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        # use only the UTxO with the highest amount
        txins = [pbt_highest_utxo]
        txouts = [
            clusterlib.TxOut(address=dst_address, amount=transferred_amount),
            # Add the value from test's parameter to unbalance the transaction. Since the correct
            # change amount here is 0, the value from test's parameter can be used directly.
            clusterlib.TxOut(address=src_address, amount=change_amount),
        ]

        cluster.build_raw_tx_bare(
            out_file=out_file_tx,
            txins=txins,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=out_file_tx,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # it should NOT be possible to submit an unbalanced transaction
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx_bare(out_file_signed)
        assert "ValueNotConservedUTxO" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(change_amount=st.integers(min_value=MAX_LOVELACE_AMOUNT + 1))
    @common.hypothesis_settings()
    def test_out_of_bounds_amount(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        change_amount: int,
    ):
        """Try to build a transaction with output Lovelace amount that is out of bounds."""
        temp_template = common.get_test_id(cluster)
        fee = 200_000

        out_file_tx = f"{temp_template}_tx.body"
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        # use only the UTxO with the highest amount
        txins = [pbt_highest_utxo]
        txouts = [
            clusterlib.TxOut(address=payment_addrs[0].address, amount=change_amount),
        ]

        try:
            cluster.build_raw_tx_bare(
                out_file=out_file_tx,
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
            )
        except clusterlib.CLIError as exc:
            exc_val = str(exc)
            assert "out of bounds" in exc_val or "exceeds the max bound" in exc_val

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(
        amount=st.integers(max_value=MIN_LOVELACE_AMOUNT, min_value=-MAX_LOVELACE_AMOUNT)
    )
    @common.hypothesis_settings()
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
        temp_template = f"{common.get_test_id(cluster)}_{amount}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build",
                    "--testnet-magic",
                    str(cluster.network_magic),
                    "--tx-in",
                    f"{pbt_highest_utxo.utxo_hash}#{pbt_highest_utxo.utxo_ix}",
                    "--change-address",
                    src_address,
                    "--tx-out",
                    f"{dst_address}+{amount}",
                    "--out-file",
                    f"{temp_template}_tx.body",
                ]
            )
        assert "Minimum UTxO threshold not met for tx output" in str(
            excinfo.value
        ) or "Negative quantity" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        amount=st.integers(max_value=MIN_LOVELACE_AMOUNT, min_value=-MAX_LOVELACE_AMOUNT)
    )
    @common.hypothesis_settings()
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
        temp_template = f"{common.get_test_id(cluster)}_{amount}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        fee = 200_000

        out_file = f"{temp_template}.body"

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--fee",
                    f"{fee}",
                    "--tx-in",
                    f"{pbt_highest_utxo.utxo_hash}#{pbt_highest_utxo.utxo_ix}",
                    "--tx-out",
                    f"{dst_address}+{amount}",
                    f"--tx-out={src_address}+{pbt_highest_utxo.amount - amount - fee}",
                    "--out-file",
                    out_file,
                ]
            )

            # create signed transaction
            out_file_signed = cluster.sign_tx(
                tx_body_file=out_file,
                signing_key_files=[payment_addrs[0].skey_file],
                tx_name=f"{temp_template}_signed",
            )

            # submit the signed transaction
            cluster.submit_tx(tx_file=out_file_signed, txins=[pbt_highest_utxo])
        assert "OutputTooSmallUTxO" in str(excinfo.value) or "Negative quantity" in str(
            excinfo.value
        )


@pytest.mark.testnets
@pytest.mark.smoke
class TestNegative:
    """Transaction tests that are expected to fail."""

    # pylint: disable=too-many-public-methods

    @pytest.fixture
    def cluster_wrong_tx_era(
        self,
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
                cluster_obj.build_tx(
                    src_address=pool_users[0].payment.address,
                    tx_name="to_invalid",
                    txouts=destinations,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                cluster_obj.build_raw_tx(
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
                cluster_obj.build_tx(
                    src_address=addr,
                    tx_name="from_invalid",
                    txouts=destinations,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                cluster_obj.build_raw_tx(
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
            cluster_obj.build_tx(
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
                cluster_obj.build_tx(
                    src_address=src_addr.address,
                    tx_name=temp_template,
                    txins=[utxo],
                    txouts=destinations,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                cluster_obj.send_tx(
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
        ttl = cluster.get_slot_no() - 1
        fee = cluster.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            ttl=ttl,
        )

        # it should be possible to build and sign a transaction with ttl in the past
        tx_raw_output = cluster.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # it should NOT be possible to submit a transaction with ttl in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx_bare(out_file_signed)
        exc_val = str(excinfo.value)
        assert "ExpiredUTxO" in exc_val or "ValidityIntervalUTxO" in exc_val

    @allure.link(helpers.get_vcs_link())
    def test_far_future_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send a transaction with ttl too far in the future.

        Too far means slot further away than current slot + 3k/f slot.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]

        # ttl can't be further than 3k/f slot
        furthest_slot = round(
            3 * cluster.genesis["securityParam"] / cluster.genesis["activeSlotsCoeff"]
        )

        ttl = cluster.get_slot_no() + furthest_slot + 100_000
        fee = cluster.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            invalid_hereafter=ttl,
        )

        tx_raw_output = cluster.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
            invalid_hereafter=ttl,
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # it should NOT be possible to submit a transaction with ttl far in the future
        err_str = ""
        try:
            cluster.submit_tx(out_file_signed, txins=tx_raw_output.txins)
        except clusterlib.CLIError as err:
            err_str = str(err)

        if not err_str:
            pytest.xfail("TTL too far in future is not rejected")

        assert "ValidityIntervalUTxO" in err_str

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

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # build and sign a transaction
        fee = cluster.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # submit a transaction for the first time
        cluster.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        # it should NOT be possible to submit a transaction twice
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx_bare(out_file_signed)
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
        fee = cluster.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.sign_tx(
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
            cluster.send_tx(
                src_address=pool_users[0].payment.address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
            )
        assert "MissingVKeyWitnessesUTXOW" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.cluster_era == VERSIONS.LAST_KNOWN_ERA,
        reason=f"doesn't run with the latest cluster era ({VERSIONS.cluster_era_name})",
    )
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
            cluster_wrong_tx_era.send_tx(
                src_address=pool_users[0].payment.address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
            )
        assert "The era of the node and the tx do not match" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
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
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
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
        utxo_addr = cluster.get_utxo(address=dst_addr)[0].utxo_hash
        self._send_funds_to_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=utxo_addr, use_build_cmd=use_build_cmd
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings()
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
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings()
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
    @common.hypothesis_settings()
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
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings()
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
    @common.hypothesis_settings()
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
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings()
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
    @common.hypothesis_settings()
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
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings()
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
    @common.hypothesis_settings()
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
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings()
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
    @common.hypothesis_settings()
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
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings()
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
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings()
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
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings()
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
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings()
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
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
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

        utxo = cluster.get_utxo(address=pool_users[0].payment.address)[0]
        utxo_copy = utxo._replace(utxo_ix=5)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster,
            pool_users=pool_users,
            utxo=utxo_copy,
            temp_template=temp_template,
            use_build_cmd=use_build_cmd,
        )
        if use_build_cmd:
            expected_msg = "The following tx input(s) were not present in the UTxO"
        else:
            expected_msg = "BadInputsUTxO"
        assert expected_msg in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
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

        utxo = cluster.get_utxo(address=pool_users[0].payment.address)[0]
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
            expected_msg = "The following tx input(s) were not present in the UTxO"
        else:
            expected_msg = "BadInputsUTxO"
        assert expected_msg in err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(utxo_hash=st.text(alphabet=ADDR_ALPHABET, min_size=10, max_size=550))
    @common.hypothesis_settings()
    def test_invalid_lenght_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        utxo_hash: str,
    ):
        """Try to use invalid UTxO hash as an input (property-based test).

        Expect failure.
        """
        temp_template = f"test_invalid_lenght_utxo_hash_ci{cluster.cluster_id}"

        utxo = cluster.get_utxo(address=pool_users[0].payment.address)[0]
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
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @hypothesis.given(utxo_hash=st.text(alphabet=ADDR_ALPHABET, min_size=10, max_size=550))
    @common.hypothesis_settings()
    def test_build_invalid_lenght_utxo_hash(
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

        utxo = cluster.get_utxo(address=pool_users[0].payment.address)[0]
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

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
        )
        txins, txouts = _get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--invalid-hereafter",
                    str(tx_raw_output.invalid_hereafter),
                    "--out-file",
                    str(tx_raw_output.out_file),
                    *cluster._prepend_flag("--tx-in", txins),
                    *cluster._prepend_flag("--tx-out", txouts),
                ]
            )
        assert "fee must be specified" in str(excinfo.value)

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

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
        )
        txins, txouts = _get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--fee",
                    str(tx_raw_output.fee),
                    "--out-file",
                    str(tx_raw_output.out_file),
                    *cluster._prepend_flag("--tx-in", txins),
                    *cluster._prepend_flag("--tx-out", txouts),
                    *cluster.tx_era_arg,
                ]
            )
        assert "TTL must be specified" in str(excinfo.value)

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

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
        )
        __, txouts = _get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

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
                    *cluster._prepend_flag("--tx-out", txouts),
                ]
            )
        assert re.search(r"Missing: *\(--tx-in TX-IN", str(excinfo.value))

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
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

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            for_build_command=True,
        )
        __, txouts = _get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

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
                    *cluster._prepend_flag("--tx-out", txouts),
                    *cluster.tx_era_arg,
                ]
            )
        assert re.search(r"Missing: *\(--tx-in TX-IN", str(excinfo.value))

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
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

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            for_build_command=True,
        )
        txins, txouts = _get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build",
                    "--out-file",
                    str(tx_raw_output.out_file),
                    "--testnet-magic",
                    str(cluster.network_magic),
                    *cluster._prepend_flag("--tx-in", txins),
                    *cluster._prepend_flag("--tx-out", txouts),
                    *cluster.tx_era_arg,
                ]
            )
        assert re.search(r"Missing:.* --change-address ADDRESS", str(excinfo.value))

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
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

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            for_build_command=True,
        )
        txins, txouts = _get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build",
                    "--out-file",
                    str(tx_raw_output.out_file),
                    "--testnet-magic",
                    str(cluster.network_magic),
                    *cluster._prepend_flag("--tx-in", txins),
                    *cluster._prepend_flag("--tx-out", txouts),
                    *cluster._prepend_flag(
                        "--change-address",
                        [pool_users[0].payment.address, pool_users[2].payment.address],
                    ),
                    *cluster.tx_era_arg,
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
        fee = cluster.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.sign_tx(
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
        if cluster.use_cddl:
            tx_signed_again = cluster.sign_tx(
                **cli_args,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
            # check that the Tx can be successfully submitted
            cluster.submit_tx(tx_file=tx_signed_again, txins=tx_raw_output.txins)
        else:
            with pytest.raises(clusterlib.CLIError) as exc_body:
                cluster.sign_tx(
                    **cli_args,
                    signing_key_files=tx_files.signing_key_files,
                    tx_name=f"{temp_template}_err1",
                )
            assert "TextEnvelope error" in str(exc_body.value)


@pytest.mark.testnets
@pytest.mark.smoke
class TestMetadata:
    """Tests for transactions with metadata."""

    JSON_METADATA_FILE = DATA_DIR / "tx_metadata.json"
    JSON_METADATA_WRONG_FILE = DATA_DIR / "tx_metadata_wrong.json"
    JSON_METADATA_INVALID_FILE = DATA_DIR / "tx_metadata_invalid.json"
    JSON_METADATA_LONG_FILE = DATA_DIR / "tx_metadata_long.json"
    CBOR_METADATA_FILE = DATA_DIR / "tx_metadata.cbor"
    METADATA_DUPLICATES = "tx_metadata_duplicate_keys*.json"

    @pytest.fixture
    def payment_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.AddressRecord:
        """Create new payment address."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addr = clusterlib_utils.create_payment_addr_records(
                f"addr_test_metadata_ci{cluster_manager.cluster_instance_num}_0",
                cluster_obj=cluster,
            )[0]
            fixture_cache.value = addr

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addr,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return addr

    @allure.link(helpers.get_vcs_link())
    def test_tx_wrong_json_metadata_format(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with wrong format of metadata JSON.

        The metadata file is a valid JSON, but not in a format that is expected.
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_WRONG_FILE],
        )

        # it should NOT be possible to build a transaction using wrongly formatted metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_raw_tx(
                src_address=payment_addr.address,
                tx_name="wrong_json_format",
                tx_files=tx_files,
            )
        assert "The JSON metadata top level must be a map" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    def test_build_tx_wrong_json_metadata_format(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with wrong format of metadata JSON.

        Uses `cardano-cli transaction build` command for building the transactions.

        The metadata file is a valid JSON, but not in a format that is expected.
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_WRONG_FILE],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
                src_address=payment_addr.address,
                tx_name="wrong_json_format",
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        assert "The JSON metadata top level must be a map" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_tx_invalid_json_metadata(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with invalid metadata JSON.

        The metadata file is an invalid JSON.
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_INVALID_FILE],
        )

        # it should NOT be possible to build a transaction using an invalid metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_raw_tx(
                src_address=payment_addr.address,
                tx_name="invalid_metadata",
                tx_files=tx_files,
            )
        assert "Failed reading: satisfy" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    def test_build_tx_invalid_json_metadata(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with invalid metadata JSON.

        Uses `cardano-cli transaction build` command for building the transactions.

        The metadata file is an invalid JSON.
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_INVALID_FILE],
        )

        # it should NOT be possible to build a transaction using an invalid metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
                src_address=payment_addr.address,
                tx_name="invalid_metadata",
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        assert "Failed reading: satisfy" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_tx_too_long_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with metadata JSON longer than 64 bytes."""
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_LONG_FILE],
        )

        # it should NOT be possible to build a transaction using too long metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_raw_tx(
                src_address=payment_addr.address,
                tx_name="too_long_metadata",
                tx_files=tx_files,
            )
        assert "Text string metadata value must consist of at most 64 UTF8 bytes" in str(
            excinfo.value
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    def test_build_tx_too_long_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with metadata JSON longer than 64 bytes.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_LONG_FILE],
        )

        # it should NOT be possible to build a transaction using too long metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
                src_address=payment_addr.address,
                tx_name="too_long_metadata",
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        assert "Text string metadata value must consist of at most 64 UTF8 bytes" in str(
            excinfo.value
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_tx_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata JSON.

        * check that the metadata in TX body matches the original metadata
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
        )
        tx_raw_output = cluster.send_tx(
            src_address=payment_addr.address, tx_name=temp_template, tx_files=tx_files
        )
        assert tx_raw_output.fee, "Transaction had no fee"

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)
        # dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert (
            json_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @pytest.mark.dbsync
    def test_build_tx_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata JSON.

        Uses `cardano-cli transaction build` command for building the transactions.

        * check that the metadata in TX body matches the original metadata
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
        )
        tx_output = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            fee_buffer=1_000_000,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_output.out_file)
        # dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert (
            json_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_tx_metadata_cbor(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata CBOR.

        Check that the metadata in TX body matches the original metadata.
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_cbor_files=[self.CBOR_METADATA_FILE],
        )
        tx_raw_output = cluster.send_tx(
            src_address=payment_addr.address, tx_name=temp_template, tx_files=tx_files
        )
        assert tx_raw_output.fee, "Transaction had no fee"

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)

        with open(self.CBOR_METADATA_FILE, "rb") as metadata_fp:
            cbor_file_metadata = cbor2.load(metadata_fp)

        assert (
            cbor_body_metadata.metadata == cbor_file_metadata
        ), "Metadata in TX body doesn't match original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_file_metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @pytest.mark.dbsync
    def test_build_tx_metadata_cbor(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata CBOR.

        Uses `cardano-cli transaction build` command for building the transactions.

        Check that the metadata in TX body matches the original metadata.
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_cbor_files=[self.CBOR_METADATA_FILE],
        )

        tx_output = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            fee_buffer=1_000_000,
        )

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_output.out_file)

        with open(self.CBOR_METADATA_FILE, "rb") as metadata_fp:
            cbor_file_metadata = cbor2.load(metadata_fp)

        assert (
            cbor_body_metadata.metadata == cbor_file_metadata
        ), "Metadata in TX body doesn't match original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_file_metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_tx_metadata_both(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with both metadata JSON and CBOR.

        Check that the metadata in TX body matches the original metadata.
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
            metadata_cbor_files=[self.CBOR_METADATA_FILE],
        )
        tx_raw_output = cluster.send_tx(
            src_address=payment_addr.address, tx_name=temp_template, tx_files=tx_files
        )
        assert tx_raw_output.fee, "Transaction had no fee"

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)
        # dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp_json:
            json_file_metadata = json.load(metadata_fp_json)

        with open(self.CBOR_METADATA_FILE, "rb") as metadata_fp_cbor:
            cbor_file_metadata = cbor2.load(metadata_fp_cbor)
        cbor_file_metadata = json.loads(json.dumps(cbor_file_metadata))

        assert json_body_metadata == {
            **json_file_metadata,
            **cbor_file_metadata,
        }, "Metadata in TX body doesn't match original metadata"

        # check `transaction view` command
        tx_view_out = tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        assert json_body_metadata == tx_view_out["metadata"]

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
    @pytest.mark.dbsync
    def test_build_tx_metadata_both(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with both metadata JSON and CBOR.

        Uses `cardano-cli transaction build` command for building the transactions.

        Check that the metadata in TX body matches the original metadata.
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
            metadata_cbor_files=[self.CBOR_METADATA_FILE],
        )

        tx_output = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            fee_buffer=1_000_000,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_output.out_file)
        # dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp_json:
            json_file_metadata = json.load(metadata_fp_json)

        with open(self.CBOR_METADATA_FILE, "rb") as metadata_fp_cbor:
            cbor_file_metadata = cbor2.load(metadata_fp_cbor)
        cbor_file_metadata = json.loads(json.dumps(cbor_file_metadata))

        assert json_body_metadata == {
            **json_file_metadata,
            **cbor_file_metadata,
        }, "Metadata in TX body doesn't match original metadata"

        # check `transaction view` command
        tx_view_out = tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output)
        assert json_body_metadata == tx_view_out["metadata"]

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    def test_tx_duplicate_metadata_keys(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with multiple metadata JSON files and with duplicate keys.

        * check that the metadata in TX body matches the original metadata
        * check that in case of duplicate keys the first occurrence is used
        """
        temp_template = common.get_test_id(cluster)

        metadata_json_files = list(DATA_DIR.glob(self.METADATA_DUPLICATES))

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=metadata_json_files,
        )

        tx_raw_output = cluster.send_tx(
            src_address=payment_addr.address, tx_name=temp_template, tx_files=tx_files
        )
        assert tx_raw_output.fee, "Transaction had no fee"

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)
        # dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        # merge the input JSON files and alter the result so it matches the expected metadata
        with open(metadata_json_files[0], encoding="utf-8") as metadata_fp:
            json_file_metadata1 = json.load(metadata_fp)
        with open(metadata_json_files[1], encoding="utf-8") as metadata_fp:
            json_file_metadata2 = json.load(metadata_fp)
        json_file_metadata = {**json_file_metadata2, **json_file_metadata1}  # noqa: SIM904
        json_file_metadata["5"] = "baz1"

        assert (
            json_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_tx_metadata_no_txout(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
    ):
        """Send transaction with just metadata, no UTxO is produced.

        * submit a transaction where all funds available on source address is used for fee
        * check that no UTxOs are created by the transaction
        * check that there are no funds left on source address
        * check that the metadata in TX body matches the original metadata
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

        tx_files = clusterlib.TxFiles(
            signing_key_files=[src_record.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
        )
        fee = cluster.get_address_balance(src_record.address)
        tx_raw_output = cluster.send_tx(
            src_address=src_record.address, tx_name=temp_template, tx_files=tx_files, fee=fee
        )
        assert not tx_raw_output.txouts, "Transaction has unexpected txouts"
        assert (
            cluster.get_address_balance(src_record.address) == 0
        ), f"Incorrect balance for source address `{src_record.address}`"

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)
        # dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert (
            json_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"
