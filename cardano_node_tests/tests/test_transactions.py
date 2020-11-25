"""Tests for general transactions.

* transfering funds (from 1 address to many, many to 1, many to many)
* not ballanced transactions
* other negative tests like duplicated transaction, sending funds to wrong addresses,
  wrong fee, wrong ttl
* transactions with metadata
"""
import functools
import itertools
import json
import logging
import string
from pathlib import Path
from typing import List
from typing import Tuple

import allure
import cbor2
import hypothesis
import hypothesis.strategies as st
import pytest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import parallel_run

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"

ADDR_ALPHABET = list(f"{string.ascii_lowercase}{string.digits}")


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    return Path(tmp_path_factory.mktemp(helpers.get_id_for_mktemp(__file__))).resolve()


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


class TestBasic:
    """Test basic transactions - transfering funds, transaction IDs."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        data_key = id(TestBasic)
        cached_value = cluster_manager.cache.test_data.get(data_key)
        if cached_value:
            return cached_value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            f"addr_basic_ci{cluster_manager.cluster_instance}_0",
            f"addr_basic_ci{cluster_manager.cluster_instance}_1",
            cluster_obj=cluster,
        )
        cluster_manager.cache.test_data[data_key] = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *addrs,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )
        return addrs

    @pytest.mark.parametrize("amount", (1, 10, 200, 2000, 100_000))
    @allure.link(helpers.get_vcs_link())
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
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

    @allure.link(helpers.get_vcs_link())
    def test_transfer_all_funds(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send ALL funds from one payment address to another.

        * send all available funds from 1 source address to 1 destination address
        * check expected balance for destination addresses
        * check that balance for source address is 0 Lovelace
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
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address) == 0
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address)
            == dst_init_balance + src_init_balance - tx_raw_output.fee
        ), f"Incorrect balance for destination address `{dst_address}`"

    @allure.link(helpers.get_vcs_link())
    def test_get_txid(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Get transaction ID (txid) from transaction body.

        Transaction ID is a hash of transaction body and doesn't change for a signed TX.

        * send funds from 1 source address to 1 destination address
        * get txid from transaction body
        * check that txid has expected lenght
        * check that the txid is listed in UTXO hashes for both source and destination addresses
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
        cluster.wait_for_new_block(new_blocks=2)

        txid = cluster.get_txid(tx_raw_output.out_file)
        utxo_src = cluster.get_utxo(src_address)
        utxo_dst = cluster.get_utxo(dst_address)
        assert len(txid) == 64
        assert txid in (u.utxo_hash for u in utxo_src)
        assert txid in (u.utxo_hash for u in utxo_dst)

    @allure.link(helpers.get_vcs_link())
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
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

    @allure.link(helpers.get_vcs_link())
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
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"


class TestMultiInOut:
    """Test transactions with multiple txins and/or txouts."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 11 new payment addresses."""
        data_key = id(TestMultiInOut)
        cached_value = cluster_manager.cache.test_data.get(data_key)
        if cached_value:
            return cached_value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            *[f"multi_in_out_addr_ci{cluster_manager.cluster_instance}_{i}" for i in range(201)],
            cluster_obj=cluster,
        )
        cluster_manager.cache.test_data[data_key] = addrs

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
        cluster_obj.wait_for_new_block(new_blocks=2)

        # record initial balances
        src_init_balance = cluster_obj.get_address_balance(src_address)
        from_init_balance = functools.reduce(
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
        cluster_obj.wait_for_new_block(new_blocks=2)

        # check balances
        from_final_balance = functools.reduce(
            lambda x, y: x + y,
            (cluster_obj.get_address_balance(r.address) for r in from_addr_recs),
            0,
        )
        src_final_balance = cluster_obj.get_address_balance(src_address)

        assert (
            from_final_balance == 0
        ), f"The output addresses should have no balance, the have {from_final_balance}"

        assert (
            src_final_balance
            == src_init_balance
            + from_init_balance
            - tx_raw_output.fee
            - amount * len(dst_addresses)
        ), f"Incorrect balance for source address `{src_address}`"

        for addr in dst_addresses:
            assert (
                cluster_obj.get_address_balance(addr) == dst_init_balances[addr] + amount
            ), f"Incorrect balance for destination address `{addr}`"

    @allure.link(helpers.get_vcs_link())
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
            cluster.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=f"{temp_template}_{i}",
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
            )
            cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - fee * no_of_transactions - amount * no_of_transactions
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address)
            == dst_init_balance + amount * no_of_transactions
        ), f"Incorrect balance for destination address `{dst_address}`"

    @pytest.mark.parametrize("amount", (1, 100, 11_000))
    @allure.link(helpers.get_vcs_link())
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

    @pytest.mark.parametrize("amount", (1, 100, 11_000, 100_000))
    @allure.link(helpers.get_vcs_link())
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

    @pytest.mark.parametrize("amount", (1, 100, 11_000))
    @allure.link(helpers.get_vcs_link())
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

    @pytest.mark.parametrize("amount", (1, 100, 1000))
    @allure.link(helpers.get_vcs_link())
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


class TestNotBalanced:
    """Tests for not ballanced transactions."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        data_key = id(TestNotBalanced)
        cached_value = cluster_manager.cache.test_data.get(data_key)
        if cached_value:
            return cached_value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            f"addr_not_balanced_ci{cluster_manager.cluster_instance}_0",
            f"addr_not_balanced_ci{cluster_manager.cluster_instance}_1",
            cluster_obj=cluster,
        )
        cluster_manager.cache.test_data[data_key] = addrs

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

        # use only the UTXO with highest amount
        txins = [src_addr_highest_utxo]
        # try to transfer +1 Lovelace more than available and use a negative change (-1)
        txouts = [
            clusterlib.TxOut(address=dst_address, amount=src_addr_highest_utxo.amount - fee + 1),
            clusterlib.TxOut(address=src_address, amount=-1),
        ]
        assert txins[0].amount - txouts[0].amount - fee == txouts[-1].amount

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_raw_tx_bare(
                out_file=temp_dir / f"{clusterlib_utils.get_timestamped_rand_str()}_tx.body",
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
            )
        assert "option --tx-out: Failed reading" in str(excinfo.value)

    @hypothesis.given(transfer_add=st.integers(), change_amount=st.integers(min_value=0))
    @helpers.HYPOTHESIS_SETTINGS
    @allure.link(helpers.get_vcs_link())
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

        tx_name = f"test_wrong_balance_{clusterlib_utils.get_timestamped_rand_str()}"
        out_file_tx = temp_dir / f"{tx_name}_tx.body"
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        # use only the UTXO with highest amount
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
                assert "out of bounds" in str(exc)
                return
            raise

        out_file_signed = cluster.sign_tx(
            tx_body_file=out_file_tx,
            signing_key_files=tx_files.signing_key_files,
            tx_name=tx_name,
        )

        # it should NOT be possible to submit an unbalanced transaction
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(out_file_signed)
        assert "ValueNotConservedUTxO" in str(excinfo.value)


class TestNegative:
    """Transaction tests that are expected to fail."""

    @pytest.fixture
    def pool_users(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.PoolUser]:
        """Create pool users."""
        data_key = id(TestNegative)
        cached_value = cluster_manager.cache.test_data.get(data_key)
        if cached_value:
            return cached_value  # type: ignore

        created_users = clusterlib_utils.create_pool_users(
            cluster_obj=cluster,
            name_template=f"test_negative_ci{cluster_manager.cluster_instance}",
            no_of_addr=2,
        )
        cluster_manager.cache.test_data[data_key] = created_users

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
        assert "invalid address" in str(excinfo.value)

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

    def _get_raw_tx_values(
        self,
        cluster_obj: clusterlib.ClusterLib,
        tx_name: str,
        pool_users: List[clusterlib.PoolUser],
        temp_dir: Path,
    ) -> clusterlib.TxRawOutput:
        """Get values for building raw TX using `clusterlib.build_raw_tx_bare`."""
        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        ttl = cluster_obj.calculate_tx_ttl()

        fee = cluster_obj.calculate_tx_fee(
            src_address=src_address,
            tx_name=tx_name,
            dst_addresses=[dst_address],
            tx_files=tx_files,
            ttl=ttl,
        )

        src_addr_highest_utxo = cluster_obj.get_utxo_with_highest_amount(src_address)

        # use only the UTXO with highest amount
        txins = [src_addr_highest_utxo]
        txouts = [
            clusterlib.TxOut(address=dst_address, amount=src_addr_highest_utxo.amount - fee),
        ]
        out_file = temp_dir / f"{clusterlib_utils.get_timestamped_rand_str()}_tx.body"

        return clusterlib.TxRawOutput(
            txins=txins,
            txouts=txouts,
            tx_files=tx_files,
            out_file=out_file,
            fee=fee,
            ttl=ttl,
            withdrawals=(),
        )

    def _get_txins_txouts(
        self, txins: List[clusterlib.UTXOData], txouts: List[clusterlib.TxOut]
    ) -> Tuple[List[str], List[str]]:
        txins_combined = [f"{x[0]}#{x[1]}" for x in txins]
        txouts_combined = [f"{x[0]}+{x[1]}" for x in txouts]
        return txins_combined, txouts_combined

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
        ttl = cluster.get_last_block_slot_no() - 1
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
            cluster.submit_tx(out_file_signed)
        assert "ExpiredUTxO" in str(excinfo.value)

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
        cluster.submit_tx(out_file_signed)
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        # it should NOT be possible to submit a transaction twice
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(out_file_signed)
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
        """Try to send funds from payment address to UTXO address.

        Expect failure.
        """
        dst_addr = pool_users[1].payment.address
        utxo_addr = cluster.get_utxo(dst_addr)[0].utxo_hash
        self._send_funds_to_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=utxo_addr
        )

    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @helpers.HYPOTHESIS_SETTINGS
    @allure.link(helpers.get_vcs_link())
    def test_send_funds_to_non_existent_address(
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

    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @helpers.HYPOTHESIS_SETTINGS
    @allure.link(helpers.get_vcs_link())
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

    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @helpers.HYPOTHESIS_SETTINGS
    @allure.link(helpers.get_vcs_link())
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

    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @helpers.HYPOTHESIS_SETTINGS
    @allure.link(helpers.get_vcs_link())
    def test_send_funds_from_non_existent_address(
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

    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @helpers.HYPOTHESIS_SETTINGS
    @allure.link(helpers.get_vcs_link())
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

    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @helpers.HYPOTHESIS_SETTINGS
    @allure.link(helpers.get_vcs_link())
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

        tx_raw_output = self._get_raw_tx_values(
            cluster_obj=cluster, tx_name=temp_template, pool_users=pool_users, temp_dir=temp_dir
        )
        txins, txouts = self._get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--ttl",
                    str(tx_raw_output.ttl),
                    "--out-file",
                    str(tx_raw_output.out_file),
                    *cluster._prepend_flag("--tx-in", txins),
                    *cluster._prepend_flag("--tx-out", txouts),
                ]
            )
        assert "Missing: --fee LOVELACE" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_missing_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        temp_dir: Path,
    ):
        """Try to build a transaction with a missing `--ttl` parameter.

        Expect failure.
        """
        temp_template = helpers.get_func_name()

        tx_raw_output = self._get_raw_tx_values(
            cluster_obj=cluster, tx_name=temp_template, pool_users=pool_users, temp_dir=temp_dir
        )
        txins, txouts = self._get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

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
                ]
            )
        assert "Missing: --ttl SLOT" in str(excinfo.value)

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

        tx_raw_output = self._get_raw_tx_values(
            cluster_obj=cluster, tx_name=temp_template, pool_users=pool_users, temp_dir=temp_dir
        )
        __, txouts = self._get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--ttl",
                    str(tx_raw_output.ttl),
                    "--fee",
                    str(tx_raw_output.fee),
                    "--out-file",
                    str(tx_raw_output.out_file),
                    *cluster._prepend_flag("--tx-out", txouts),
                ]
            )
        assert "Missing: (--tx-in TX-IN)" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_missing_tx_out(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        temp_dir: Path,
    ):
        """Try to build a transaction with a missing `--tx-out` parameter.

        Expect failure.
        """
        temp_template = helpers.get_func_name()

        tx_raw_output = self._get_raw_tx_values(
            cluster_obj=cluster, tx_name=temp_template, pool_users=pool_users, temp_dir=temp_dir
        )
        txins, __ = self._get_txins_txouts(tx_raw_output.txins, tx_raw_output.txouts)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--ttl",
                    str(tx_raw_output.ttl),
                    "--fee",
                    str(tx_raw_output.fee),
                    "--out-file",
                    str(tx_raw_output.out_file),
                    *cluster._prepend_flag("--tx-in", txins),
                ]
            )
        assert "Missing: (--tx-out TX-OUT)" in str(excinfo.value)


class TestMetadata:
    """Tests for transactions with metadata."""

    JSON_METADATA_FILE = DATA_DIR / "tx_metadata.json"
    JSON_METADATA_WRONG_FILE = DATA_DIR / "tx_metadata_wrong.json"
    JSON_METADATA_INVALID_FILE = DATA_DIR / "tx_metadata_invalid.json"
    JSON_METADATA_LONG_FILE = DATA_DIR / "tx_metadata_long.json"
    CBOR_METADATA_FILE = DATA_DIR / "tx_metadata.cbor"

    @pytest.fixture
    def payment_addr(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.AddressRecord:
        """Create new payment address."""
        data_key = id(TestMetadata)
        cached_value = cluster_manager.cache.test_data.get(data_key)
        if cached_value:
            return cached_value  # type: ignore

        addr = clusterlib_utils.create_payment_addr_records(
            f"addr_test_metadata_ci{cluster_manager.cluster_instance}_0", cluster_obj=cluster
        )[0]
        cluster_manager.cache.test_data[data_key] = addr

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
        cluster.wait_for_new_block(new_blocks=2)
        assert tx_raw_output.fee, "Transaction had no fee"
        # TODO: check that the data is on blockchain

        with open(tx_raw_output.out_file) as body_fp:
            tx_body_json = json.load(body_fp)

        cbor_body = bytes.fromhex(tx_body_json["cborHex"])
        cbor_body_metadata = cbor2.loads(cbor_body)[1]
        # dump it as JSON first, so keys are converted to strings
        cbor_body_metadata = json.loads(json.dumps(cbor_body_metadata))

        with open(self.JSON_METADATA_FILE) as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert (
            cbor_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
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
        cluster.wait_for_new_block(new_blocks=2)
        assert tx_raw_output.fee, "Transaction had no fee"

        with open(tx_raw_output.out_file) as body_fp:
            tx_body_json = json.load(body_fp)

        cbor_body = bytes.fromhex(tx_body_json["cborHex"])
        cbor_body_metadata = cbor2.loads(cbor_body)[1]

        with open(self.CBOR_METADATA_FILE, "rb") as metadata_fp:
            cbor_file_metadata = cbor2.load(metadata_fp)

        assert (
            cbor_body_metadata == cbor_file_metadata
        ), "Metadata in TX body doesn't match original metadata"

    @allure.link(helpers.get_vcs_link())
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
        cluster.wait_for_new_block(new_blocks=2)
        assert tx_raw_output.fee, "Transaction had no fee"

        with open(tx_raw_output.out_file) as body_fp:
            tx_body_json = json.load(body_fp)

        cbor_body = bytes.fromhex(tx_body_json["cborHex"])
        cbor_body_metadata = cbor2.loads(cbor_body)[1]
        # dump it as JSON first, so keys are converted to strings
        cbor_body_metadata = json.loads(json.dumps(cbor_body_metadata))

        with open(self.JSON_METADATA_FILE) as metadata_fp_json:
            json_file_metadata = json.load(metadata_fp_json)

        with open(self.CBOR_METADATA_FILE, "rb") as metadata_fp_cbor:
            cbor_file_metadata = cbor2.load(metadata_fp_cbor)
        cbor_file_metadata = json.loads(json.dumps(cbor_file_metadata))

        assert cbor_body_metadata == {
            **json_file_metadata,
            **cbor_file_metadata,
        }, "Metadata in TX body doesn't match original metadata"
