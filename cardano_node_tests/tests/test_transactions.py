"""Tests for general transactions.

* transfering funds (from 1 address to many, many to 1, many to many)
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
import string
import time
from pathlib import Path
from typing import List
from typing import Tuple

import allure
import cbor2
import hypothesis
import hypothesis.strategies as st
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"

ADDR_ALPHABET = list(f"{string.ascii_lowercase}{string.digits}")


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    p = Path(tmp_path_factory.getbasetemp()).joinpath(helpers.get_id_for_mktemp(__file__)).resolve()
    p.mkdir(exist_ok=True, parents=True)
    return p


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


def _get_raw_tx_values(
    cluster_obj: clusterlib.ClusterLib,
    tx_name: str,
    src_record: clusterlib.AddressRecord,
    dst_record: clusterlib.AddressRecord,
    temp_dir: Path,
) -> clusterlib.TxRawOutput:
    """Get values for building raw TX using `clusterlib.build_raw_tx_bare`."""
    src_address = src_record.address
    dst_address = dst_record.address

    tx_files = clusterlib.TxFiles(signing_key_files=[src_record.skey_file])
    ttl = cluster_obj.calculate_tx_ttl()

    fee = cluster_obj.calculate_tx_fee(
        src_address=src_address,
        tx_name=tx_name,
        dst_addresses=[dst_address],
        tx_files=tx_files,
        ttl=ttl,
    )

    src_addr_highest_utxo = cluster_obj.get_utxo_with_highest_amount(src_address)

    # use only the UTxO with highest amount
    txins = [src_addr_highest_utxo]
    txouts = [
        clusterlib.TxOut(address=dst_address, amount=src_addr_highest_utxo.amount - fee),
    ]
    out_file = temp_dir / f"{helpers.get_timestamped_rand_str()}_tx.body"

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
class TestBasic:
    """Test basic transactions - transfering funds, transaction IDs."""

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

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("amount", (1, 10, 200, 2000, 100_000))
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
        temp_template = f"{helpers.get_func_name()}_{amount}"

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
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

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
        temp_template = helpers.get_func_name()

        src_address = payment_addrs[1].address
        dst_address = payment_addrs[0].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        # amount value -1 means all available funds
        destinations = [clusterlib.TxOut(address=dst_address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[1].skey_file])

        tx_raw_output = cluster.send_funds(
            src_address=src_address,
            destinations=destinations,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        assert (
            cluster.get_address_balance(src_address) == 0
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address)
            == dst_init_balance + src_init_balance - tx_raw_output.fee
        ), f"Incorrect balance for destination address `{dst_address}`"

        # check `transaction view` command
        clusterlib_utils.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

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
        even though it was not generated while running a speciffic cardano
        network.

        * send funds from 1 source address to 1 destination address
        * check expected balances for both source and destination addresses
        """
        temp_template = helpers.get_func_name()
        amount = 100

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
        * check that txid has expected lenght
        * check that the txid is listed in UTxO hashes for both source and destination addresses
        """
        temp_template = helpers.get_func_name()

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=2000)]
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

        utxo_src = cluster.get_utxo(src_address)
        utxo_dst = cluster.get_utxo(dst_address)
        assert len(txid_body) == 64
        assert txid_body in (u.utxo_hash for u in utxo_src)
        assert txid_body in (u.utxo_hash for u in utxo_dst)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_extra_signing_keys(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Send a transaction with extra signing key.

        Check that it is possible to use unneded signing key in addition to the necessary
        signing keys for signing the transaction.
        """
        temp_template = helpers.get_func_name()
        amount = 100

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
        temp_template = helpers.get_func_name()
        amount = 100

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
        temp_template = helpers.get_func_name()

        src_record = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_0", cluster_obj=cluster
        )[0]
        clusterlib_utils.fund_from_faucet(
            src_record,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=200_000,
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
        temp_dir: Path,
    ):
        """Try to build a transaction with a missing `--tx-out` parameter.

        Expect failure on node version < 1.24.2 commit 3d201869.
        """
        temp_template = helpers.get_func_name()

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=payment_addrs[0],
            dst_record=payment_addrs[1],
            temp_dir=temp_dir,
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
        temp_dir: Path,
    ):
        """Submit a transaction with a missing `--ttl` (`--invalid-hereafter`) parameter."""
        temp_template = helpers.get_func_name()
        src_address = payment_addrs[0].address

        init_balance = cluster.get_address_balance(src_address)

        tx_raw_template = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=payment_addrs[0],
            dst_record=payment_addrs[0],
            temp_dir=temp_dir,
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


@pytest.mark.testnets
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
            amount=100_000_000,
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
        fund_dst = [
            clusterlib.TxOut(address=d.address, amount=fund_amount) for d in from_addr_recs[:-1]
        ]
        # add more funds to the last "from" address so it can cover TX fee
        last_from_addr_rec = from_addr_recs[-1]
        fund_dst.append(
            clusterlib.TxOut(address=last_from_addr_rec.address, amount=fund_amount + 5_000_000)
        )
        fund_tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
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
        _txins = [cluster_obj.get_utxo(r.address) for r in from_addr_recs]
        # flatten the list of lists that is _txins
        txins = list(itertools.chain.from_iterable(_txins))
        txouts = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]
        tx_files = clusterlib.TxFiles(signing_key_files=[r.skey_file for r in from_addr_recs])

        # send TX
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
        temp_template = helpers.get_func_name()
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
        amount = int(fee / no_of_transactions + 1000)
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
    @pytest.mark.parametrize("amount", (1, 100, 11_000))
    @pytest.mark.dbsync
    def test_transaction_to_10_addrs_from_1_addr(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
    ):
        """Test 1 transaction from 1 payment address to 10 payment addresses.

        * send funds from 1 source address to 10 destination addresses
        * check expected balances for both source and destination addresses
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=f"{helpers.get_func_name()}_{amount}",
            from_num=1,
            to_num=10,
            amount=amount,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("amount", (1, 100, 11_000, 100_000))
    @pytest.mark.dbsync
    def test_transaction_to_1_addr_from_10_addrs(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
    ):
        """Test 1 transaction from 10 payment addresses to 1 payment address.

        * send funds from 10 source addresses to 1 destination address
        * check expected balances for both source and destination addresses
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=f"{helpers.get_func_name()}_{amount}",
            from_num=10,
            to_num=1,
            amount=amount,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("amount", (1, 100, 11_000))
    @pytest.mark.dbsync
    def test_transaction_to_10_addrs_from_10_addrs(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
    ):
        """Test 1 transaction from 10 payment addresses to 10 payment addresses.

        * send funds from 10 source addresses to 10 destination addresses
        * check expected balances for both source and destination addresses
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=f"{helpers.get_func_name()}_{amount}",
            from_num=10,
            to_num=10,
            amount=amount,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("amount", (1, 100, 1000))
    @pytest.mark.dbsync
    def test_transaction_to_100_addrs_from_50_addrs(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
    ):
        """Test 1 transaction from 50 payment addresses to 100 payment addresses.

        * send funds from 50 source addresses to 100 destination addresses
        * check expected balances for both source and destination addresses
        """
        self._from_to_transactions(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            tx_name=f"{helpers.get_func_name()}_{amount}",
            from_num=50,
            to_num=100,
            amount=amount,
        )


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
            amount=100_000_000_000,
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
        """Generate many UTxOs (100000+) with small amounts of Lovelace (1-1000000)."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            temp_template = helpers.get_func_name()

            LOGGER.info("Generating lot of UTxO addresses, will take a while.")
            start = time.time()
            payment_addr = payment_addrs[0]
            out_addrs1 = [payment_addrs[1] for __ in range(200)]
            out_addrs2 = [payment_addrs[2] for __ in range(200)]
            out_addrs = [*out_addrs1, *out_addrs2]

            for i in range(25):
                for amount in range(1, 21):
                    self._from_to_transactions(
                        cluster_obj=cluster,
                        payment_addr=payment_addr,
                        tx_name=f"{temp_template}_{amount}_{i}",
                        out_addrs=out_addrs,
                        amount=amount,
                    )

            self._from_to_transactions(
                cluster_obj=cluster,
                payment_addr=payment_addr,
                tx_name=f"{temp_template}_big",
                out_addrs=out_addrs,
                amount=1000_000,
            )
            end = time.time()

            retval = payment_addrs[1], payment_addrs[2]
            fixture_cache.value = retval

        num_of_utxo = len(cluster.get_utxo(payment_addrs[1].address)) + len(
            cluster.get_utxo(payment_addrs[2].address)
        )
        LOGGER.info(f"Generated {num_of_utxo} of UTxO addresses in {end - start} seconds.")

        return retval

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("amount", (1, 10, 200, 2000, 10_000, 100_000, 1000_000))
    def test_mini_transactions(
        self,
        cluster: clusterlib.ClusterLib,
        many_utxos: Tuple[clusterlib.AddressRecord, clusterlib.AddressRecord],
        amount: int,
    ):
        """Test transaction with many UTxOs (300+) with tiny amounts of Lovelace (1-1000000).

        * use source address with many UTxOs (100000+)
        * use destination address with many UTxOs (100000+)
        * sent transaction with many UTxOs (300+) with tiny amounts of Lovelace from source address
          to destination address
        * check expected balances for both source and destination addresses
        """
        temp_template = f"{helpers.get_func_name()}_{amount}"
        big_funds_idx = -190

        src_address = many_utxos[0].address
        dst_address = many_utxos[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[many_utxos[0].skey_file])

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        # sort UTxOs by amount
        utxos_sorted = sorted(cluster.get_utxo(src_address), key=lambda x: x.amount)

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
        needed_funds = amount + fee + 100_000  # add a buffer
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

    @allure.link(helpers.get_vcs_link())
    def test_negative_change(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        temp_dir: Path,
    ):
        """Try to build a transaction with a negative change.

        Check that it is not possible to built such transaction.
        """
        temp_template = helpers.get_func_name()

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

        # use only the UTxO with highest amount
        txins = [src_addr_highest_utxo]
        # try to transfer +1 Lovelace more than available and use a negative change (-1)
        txouts = [
            clusterlib.TxOut(address=dst_address, amount=src_addr_highest_utxo.amount - fee + 1),
            clusterlib.TxOut(address=src_address, amount=-1),
        ]
        assert txins[0].amount - txouts[0].amount - fee == txouts[-1].amount

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_raw_tx_bare(
                out_file=temp_dir / f"{helpers.get_timestamped_rand_str()}_tx.body",
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
    @hypothesis.given(transfer_add=st.integers(), change_amount=st.integers(min_value=0))
    @helpers.hypothesis_settings()
    def test_wrong_balance(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        temp_dir: Path,
        transfer_add: int,
        change_amount: int,
    ):
        """Build a transaction with unbalanced change (property-based test).

        * build a not balanced transaction
        * check that it is not possible to submit such transaction
        """
        # we want to test only unbalanced transactions
        hypothesis.assume((transfer_add + change_amount) != 0)

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_addr_highest_utxo = cluster.get_utxo_with_highest_amount(src_address)
        fee = 200_000

        # add to `transferred_amount` the value from test's parameter to unbalance the transaction
        transferred_amount = src_addr_highest_utxo.amount - fee + transfer_add
        # make sure the change amount is valid
        hypothesis.assume(0 <= transferred_amount <= src_addr_highest_utxo.amount)

        tx_name = f"test_wrong_balance_{helpers.get_timestamped_rand_str()}"
        out_file_tx = temp_dir / f"{tx_name}_tx.body"
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        # use only the UTxO with highest amount
        txins = [src_addr_highest_utxo]
        txouts = [
            clusterlib.TxOut(address=dst_address, amount=transferred_amount),
            # Add the value from test's parameter to unbalance the transaction. Since the correct
            # change amount here is 0, the value from test's parameter can be used directly.
            clusterlib.TxOut(address=src_address, amount=change_amount),
        ]

        # it should be possible to build and sign an unbalanced transaction
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
            if change_amount >= 2 ** 64:
                exc_val = str(exc)
                assert "out of bounds" in exc_val or "exceeds the max bound" in exc_val
                return
            raise

        out_file_signed = cluster.sign_tx(
            tx_body_file=out_file_tx,
            signing_key_files=tx_files.signing_key_files,
            tx_name=tx_name,
        )

        # it should NOT be possible to submit an unbalanced transaction
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx_bare(out_file_signed)
        assert "ValueNotConservedUTxO" in str(excinfo.value)


@pytest.mark.testnets
class TestNegative:
    """Transaction tests that are expected to fail."""

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
                no_of_addr=2,
            )
            fixture_cache.value = created_users

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *created_users,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=1_000_000,
        )

        return created_users

    def _send_funds_to_invalid_address(
        self, cluster_obj: clusterlib.ClusterLib, pool_users: List[clusterlib.PoolUser], addr: str
    ):
        """Send funds from payment address to invalid address."""
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=addr, amount=1000)]

        # it should NOT be possible to build a transaction using an invalid address
        with pytest.raises(clusterlib.CLIError) as excinfo:
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
        self, cluster_obj: clusterlib.ClusterLib, pool_users: List[clusterlib.PoolUser], addr: str
    ):
        """Send funds from invalid payment address."""
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1000)]

        # it should NOT be possible to build a transaction using an invalid address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.build_raw_tx(
                src_address=addr,
                tx_name="from_invalid",
                txouts=destinations,
                tx_files=tx_files,
                fee=0,
            )
        assert "invalid address" in str(excinfo.value)

    def _send_funds_with_invalid_utxo(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        utxo: clusterlib.UTXOData,
        temp_template: str,
    ) -> str:
        """Send funds with invalid UTxO."""
        src_addr = pool_users[0].payment
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1000)]

        with pytest.raises(clusterlib.CLIError) as excinfo:
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
        temp_template = helpers.get_func_name()

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=100)]
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
    def test_duplicated_tx(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send an identical transaction twice.

        Expect failure.
        """
        temp_template = helpers.get_func_name()
        amount = 100

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
    def test_wrong_signing_key(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send a transaction signed with wrong signing key.

        Expect failure.
        """
        temp_template = helpers.get_func_name()

        # use wrong signing key
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[1].payment.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=100)]

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
    def test_send_funds_to_reward_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send funds from payment address to stake address.

        Expect failure.
        """
        addr = pool_users[0].stake.address
        self._send_funds_to_invalid_address(cluster_obj=cluster, pool_users=pool_users, addr=addr)

    @allure.link(helpers.get_vcs_link())
    def test_send_funds_to_utxo_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send funds from payment address to UTxO address.

        Expect failure.
        """
        dst_addr = pool_users[1].payment.address
        utxo_addr = cluster.get_utxo(dst_addr)[0].utxo_hash
        self._send_funds_to_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=utxo_addr
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @helpers.hypothesis_settings()
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
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @helpers.hypothesis_settings()
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
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @helpers.hypothesis_settings()
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
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @helpers.hypothesis_settings()
    def test_send_funds_from_invalid_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from non-existent address (property-based test).

        Expect failure.
        """
        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(cluster_obj=cluster, pool_users=pool_users, addr=addr)

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @helpers.hypothesis_settings()
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
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @helpers.hypothesis_settings()
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
    def test_nonexistent_utxo_ix(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to use nonexistent UTxO TxIx as an input.

        Expect failure.
        """
        temp_template = helpers.get_func_name()

        utxo = cluster.get_utxo(pool_users[0].payment.address)[0]
        utxo_copy = utxo._replace(utxo_ix=5)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster, pool_users=pool_users, utxo=utxo_copy, temp_template=temp_template
        )
        assert "BadInputsUTxO" in err

    @allure.link(helpers.get_vcs_link())
    def test_nonexistent_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to use nonexistent UTxO hash as an input.

        Expect failure.
        """
        temp_template = helpers.get_func_name()

        utxo = cluster.get_utxo(pool_users[0].payment.address)[0]
        new_hash = f"{utxo.utxo_hash[:-4]}fd42"
        utxo_copy = utxo._replace(utxo_hash=new_hash)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster, pool_users=pool_users, utxo=utxo_copy, temp_template=temp_template
        )
        assert "BadInputsUTxO" in err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(utxo_hash=st.text(alphabet=ADDR_ALPHABET, min_size=10, max_size=550))
    @helpers.hypothesis_settings()
    def test_invalid_lenght_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        utxo_hash: str,
    ):
        """Try to use invalid UTxO hash as an input (property-based test).

        Expect failure.
        """
        temp_template = "test_invalid_lenght_utxo_hash"

        utxo = cluster.get_utxo(pool_users[0].payment.address)[0]
        utxo_copy = utxo._replace(utxo_hash=utxo_hash)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster, pool_users=pool_users, utxo=utxo_copy, temp_template=temp_template
        )
        assert "Incorrect transaction id format" in err or "Failed reading" in err

    @allure.link(helpers.get_vcs_link())
    def test_missing_fee(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        temp_dir: Path,
    ):
        """Try to build a transaction with a missing `--fee` parameter.

        Expect failure.
        """
        temp_template = helpers.get_func_name()

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            temp_dir=temp_dir,
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
        temp_dir: Path,
    ):
        """Try to build a Shelley era TX with a missing `--ttl` (`--invalid-hereafter`) parameter.

        Expect failure.
        """
        temp_template = helpers.get_func_name()

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            temp_dir=temp_dir,
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
        temp_dir: Path,
    ):
        """Try to build a transaction with a missing `--tx-in` parameter.

        Expect failure.
        """
        temp_template = helpers.get_func_name()

        tx_raw_output = _get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            temp_dir=temp_dir,
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
        assert "Missing: (--tx-in TX-IN)" in str(excinfo.value)


@pytest.mark.testnets
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
        """Try to build a transaction with wrong fromat of metadata JSON.

        The metadata file is a valid JSON, but not in a format that is expected.
        """
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
    def test_tx_invalid_json_metadata(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with invalid metadata JSON.

        The metadata file is an invalid JSON.
        """
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
    def test_tx_too_long_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with metadata JSON longer than 64 bytes."""
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
    @pytest.mark.dbsync
    def test_tx_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata JSON.

        Check that the metadata in TX body matches the original metadata.
        """
        temp_template = helpers.get_func_name()

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
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata))

        with open(self.JSON_METADATA_FILE) as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert (
            json_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_tx_metadata_cbor(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata CBOR.

        Check that the metadata in TX body matches the original metadata.
        """
        temp_template = helpers.get_func_name()

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
            cbor_body_metadata == cbor_file_metadata
        ), "Metadata in TX body doesn't match original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
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
        temp_template = helpers.get_func_name()

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
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata))

        with open(self.JSON_METADATA_FILE) as metadata_fp_json:
            json_file_metadata = json.load(metadata_fp_json)

        with open(self.CBOR_METADATA_FILE, "rb") as metadata_fp_cbor:
            cbor_file_metadata = cbor2.load(metadata_fp_cbor)
        cbor_file_metadata = json.loads(json.dumps(cbor_file_metadata))

        assert json_body_metadata == {
            **json_file_metadata,
            **cbor_file_metadata,
        }, "Metadata in TX body doesn't match original metadata"

        # check `transaction view` command
        tx_view = clusterlib_utils.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        assert 'txMetadata = fromList [(1,S "foo")' in tx_view

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    def test_tx_duplicate_metadata_keys(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with multiple metadata JSON files and with duplicate keys.

        * check that the metadata in TX body matches the original metadata
        * check that in case of duplicate keys the first occurrence is used
        """
        temp_template = helpers.get_func_name()

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
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata))

        # merge the input JSON files and alter the result so it matches the expected metadata
        with open(metadata_json_files[0]) as metadata_fp:
            json_file_metadata1 = json.load(metadata_fp)
        with open(metadata_json_files[1]) as metadata_fp:
            json_file_metadata2 = json.load(metadata_fp)
        json_file_metadata = {**json_file_metadata2, **json_file_metadata1}
        json_file_metadata["5"] = "baz1"

        assert (
            json_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata
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
        temp_template = helpers.get_func_name()

        src_record = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_0", cluster_obj=cluster
        )[0]
        clusterlib_utils.fund_from_faucet(
            src_record,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=500_000,
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
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata))

        with open(self.JSON_METADATA_FILE) as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert (
            json_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata
            ), "Metadata in db-sync doesn't match the original metadata"
