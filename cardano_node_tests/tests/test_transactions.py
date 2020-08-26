import functools
import itertools
import logging
import string
from pathlib import Path
from typing import List

import hypothesis
import hypothesis.strategies as st
import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

ADDR_ALPHABET = list(f"{string.ascii_lowercase}{string.digits}")


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir and change to it."""
    tmp_path = Path(tmp_path_factory.mktemp("test_transactions"))
    with helpers.change_cwd(tmp_path):
        yield tmp_path


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


class TestBasic:
    @pytest.fixture(scope="class")
    def payment_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        addrs = helpers.create_payment_addr_records(
            "addr_basic0", "addr_basic1", cluster_obj=cluster_session
        )

        # fund source addresses
        helpers.fund_from_faucet(
            *addrs,
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        return addrs

    def test_transfer_funds(
        self, cluster_session: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send funds to payment address."""
        cluster = cluster_session
        amount = 2000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_output = cluster.send_funds(
            src_address=src_address, destinations=destinations, tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

    def test_transfer_all_funds(
        self, cluster_session: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send ALL funds from one payment address to another."""
        cluster = cluster_session

        src_address = payment_addrs[1].address
        dst_address = payment_addrs[0].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        # amount value -1 means all available funds
        destinations = [clusterlib.TxOut(address=dst_address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[1].skey_file])

        tx_raw_output = cluster.send_funds(
            src_address=src_address, destinations=destinations, tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address) == 0
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address)
            == dst_init_balance + src_init_balance - tx_raw_output.fee
        ), f"Incorrect balance for destination address `{dst_address}`"

    def test_get_txid(
        self, cluster_session: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Get transaction ID (txid) from transaction body.

        Transaction ID is a hash of transaction body and doesn't change for a signed TX.
        """
        cluster = cluster_session

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=2000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        tx_raw_output = cluster.send_funds(
            src_address=src_address, destinations=destinations, tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        txid = cluster.get_txid(tx_raw_output.out_file)
        utxo = cluster.get_utxo(src_address)
        assert len(txid) == 64
        assert txid in (u.utxo_hash for u in utxo)


class Test10InOut:
    @pytest.fixture(scope="class")
    def payment_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ) -> List[clusterlib.AddressRecord]:
        """Create 11 new payment addresses."""
        addrs = helpers.create_payment_addr_records(
            *[f"addr_10_in_out{i}" for i in range(11)], cluster_obj=cluster_session,
        )

        # fund source addresses
        helpers.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        return addrs

    def test_10_transactions(
        self, cluster_session: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send 10 transactions to payment address.

        Test 10 different UTXOs in addr0.
        """
        cluster = cluster_session
        no_of_transactions = len(payment_addrs) - 1

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        fee = cluster.calculate_tx_fee(
            src_address=src_address, dst_addresses=[dst_address], tx_files=tx_files, ttl=ttl,
        )
        amount = int(fee / no_of_transactions + 1000)
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        for __ in range(no_of_transactions):
            cluster.send_funds(
                src_address=src_address,
                destinations=destinations,
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

    def test_transaction_to_10_addrs(
        self, cluster_session: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send 1 transaction from one payment address to 10 payment addresses."""
        cluster = cluster_session
        src_address = payment_addrs[0].address
        # addr1..addr10
        dst_addresses = [payment_addrs[i].address for i in range(1, len(payment_addrs))]

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balances = {addr: cluster.get_address_balance(addr) for addr in dst_addresses}

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        fee = cluster.calculate_tx_fee(
            src_address=src_address, dst_addresses=dst_addresses, tx_files=tx_files, ttl=ttl,
        )
        amount = int((cluster.get_address_balance(src_address) - fee) / len(dst_addresses))
        destinations = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]

        cluster.send_funds(
            src_address=src_address, destinations=destinations, tx_files=tx_files, fee=fee, ttl=ttl,
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert cluster.get_address_balance(src_address) == src_init_balance - fee - amount * len(
            dst_addresses
        ), f"Incorrect balance for source address `{src_address}`"

        for addr in dst_addresses:
            assert (
                cluster.get_address_balance(addr) == dst_init_balances[addr] + amount
            ), f"Incorrect balance for destination address `{addr}`"

    def test_transaction_to_5_addrs_from_5_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        payment_addrs: List[clusterlib.AddressRecord],
        request: FixtureRequest,
    ):
        """Send 1 transaction from 5 payment address to 5 payment addresses."""
        cluster = cluster_session
        src_address = payment_addrs[0].address
        amount = 100
        # addr1..addr5
        from_addr_recs = payment_addrs[1:6]
        # addr6..addr10
        dst_addresses = [payment_addrs[i].address for i in range(6, 11)]

        # fund from addresses
        helpers.fund_from_faucet(
            *from_addr_recs,
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        src_init_balance = cluster.get_address_balance(src_address)
        from_init_balance = functools.reduce(
            lambda x, y: x + y, (cluster.get_address_balance(r.address) for r in from_addr_recs), 0
        )
        dst_init_balances = {addr: cluster.get_address_balance(addr) for addr in dst_addresses}

        # send funds
        _txins = [cluster.get_utxo(r.address) for r in from_addr_recs]
        # flatten the list of lists that is _txins
        txins = list(itertools.chain.from_iterable(_txins))
        txouts = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]
        tx_files = clusterlib.TxFiles(signing_key_files=[r.skey_file for r in from_addr_recs])

        tx_raw_output = cluster.send_tx(
            src_address=src_address, txins=txins, txouts=txouts, tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        # check balances
        from_final_balance = functools.reduce(
            lambda x, y: x + y, (cluster.get_address_balance(r.address) for r in from_addr_recs), 0
        )
        src_final_balance = cluster.get_address_balance(src_address)

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
                cluster.get_address_balance(addr) == dst_init_balances[addr] + amount
            ), f"Incorrect balance for destination address `{addr}`"


class TestNotBalanced:
    @pytest.fixture(scope="class")
    def payment_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        addrs = helpers.create_payment_addr_records(
            "addr_not_balanced0", "addr_not_balanced1", cluster_obj=cluster_session
        )

        # fund source addresses
        helpers.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        return addrs

    def test_negative_change(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        temp_dir: Path,
    ):
        """Build a transaction with a negative change."""
        cluster = cluster_session
        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        fee = cluster.calculate_tx_fee(
            src_address=src_address, dst_addresses=[dst_address], tx_files=tx_files, ttl=ttl,
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
                out_file=temp_dir / "tx.body",
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
            )
        assert "option --tx-out: Failed reading" in str(excinfo.value)

    @hypothesis.given(transfer_add=st.integers(), change_amount=st.integers(min_value=0))
    @hypothesis.settings(deadline=None)
    def test_wrong_balance(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        temp_dir: Path,
        transfer_add: int,
        change_amount: int,
    ):
        """Build a transaction with unbalanced change."""
        # we want to test only unbalanced transactions
        hypothesis.assume((transfer_add + change_amount) != 0)

        cluster = cluster_session

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_addr_highest_utxo = cluster.get_utxo_with_highest_amount(src_address)
        fee = 200_000

        # add to `transferred_amount` the value from test's parameter to unbalance the transaction
        transferred_amount = src_addr_highest_utxo.amount - fee + transfer_add
        # make sure the change amount is valid
        hypothesis.assume(0 <= transferred_amount <= src_addr_highest_utxo.amount)

        out_file_tx = temp_dir / f"{clusterlib.get_timestamped_rand_str()}_tx.body"
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
        cluster.build_raw_tx_bare(
            out_file=out_file_tx, txins=txins, txouts=txouts, tx_files=tx_files, fee=fee, ttl=ttl,
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=out_file_tx, signing_key_files=tx_files.signing_key_files,
        )

        # it should NOT be possible to submit an unbalanced transaction
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(out_file_signed)
        assert "ValueNotConservedUTxO" in str(excinfo.value)


class TestNegative:
    @pytest.fixture(scope="class")
    def pool_users(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ) -> List[clusterlib.PoolUser]:
        """Create pool users."""
        pool_users = helpers.create_pool_users(
            cluster_obj=cluster_session, name_template="test_negative", no_of_addr=2,
        )

        # fund source addresses
        helpers.fund_from_faucet(
            pool_users[0],
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            amount=1_000_000,
            request=request,
        )

        return pool_users

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
                src_address=addr, txouts=destinations, tx_files=tx_files, fee=0,
            )
        assert "invalid address" in str(excinfo.value)

    def test_past_ttl(
        self, cluster_session: clusterlib.ClusterLib, pool_users: List[clusterlib.PoolUser],
    ):
        """Send a transaction with ttl in the past."""
        cluster = cluster_session

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=100)]
        ttl = cluster.get_last_block_slot_no() - 1
        fee = cluster.calculate_tx_fee(
            src_address=src_address, txouts=destinations, tx_files=tx_files, ttl=ttl
        )

        # it should be possible to build and sign a transaction with ttl in the past
        tx_raw_output = cluster.build_raw_tx(
            src_address=src_address, txouts=destinations, tx_files=tx_files, fee=fee, ttl=ttl,
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file, signing_key_files=tx_files.signing_key_files,
        )

        # it should NOT be possible to submit a transaction with ttl in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(out_file_signed)
        assert "ExpiredUTxO" in str(excinfo.value)

    def test_duplicated_tx(
        self, cluster_session: clusterlib.ClusterLib, pool_users: List[clusterlib.PoolUser],
    ):
        """Send a single transaction twice."""
        cluster = cluster_session
        amount = 100

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # build and sign a transaction
        fee = cluster.calculate_tx_fee(
            src_address=src_address, txouts=destinations, tx_files=tx_files,
        )
        tx_raw_output = cluster.build_raw_tx(
            src_address=src_address, txouts=destinations, tx_files=tx_files, fee=fee
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file, signing_key_files=tx_files.signing_key_files,
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

    def test_send_funds_to_reward_address(
        self, cluster_session: clusterlib.ClusterLib, pool_users: List[clusterlib.PoolUser],
    ):
        """Send funds from payment address to stake address."""
        addr = pool_users[0].stake.address
        self._send_funds_to_invalid_address(
            cluster_obj=cluster_session, pool_users=pool_users, addr=addr
        )

    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @hypothesis.settings(deadline=None)
    def test_send_funds_to_non_existent_address(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Send funds from payment address to non-existent address."""
        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster_session, pool_users=pool_users, addr=addr
        )

    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @hypothesis.settings(deadline=None)
    def test_send_funds_to_invalid_length_address(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Send funds from payment address to address with invalid length."""
        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster_session, pool_users=pool_users, addr=addr
        )

    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98,)
    )
    @hypothesis.settings(deadline=None)
    def test_send_funds_to_invalid_chars_address(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Send funds from payment address to address with invalid characters."""
        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster_session, pool_users=pool_users, addr=addr
        )

    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @hypothesis.settings(deadline=None)
    def test_send_funds_from_non_existent_address(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Send funds from non-existent address."""
        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster_session, pool_users=pool_users, addr=addr
        )

    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @hypothesis.settings(deadline=None)
    def test_send_funds_from_invalid_length_address(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Send funds from address with invalid length."""
        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster_session, pool_users=pool_users, addr=addr
        )

    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98,)
    )
    @hypothesis.settings(deadline=None)
    def test_send_funds_from_invalid_chars_address(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Send funds from address with invalid characters."""
        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster_session, pool_users=pool_users, addr=addr
        )


class TestMetadata:
    @pytest.fixture(scope="class")
    def payment_addr(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ) -> clusterlib.AddressRecord:
        """Create new payment address."""
        addr = helpers.create_payment_addr_records(
            "addr_test_metadata0", cluster_obj=cluster_session
        )[0]

        # fund source addresses
        helpers.fund_from_faucet(
            addr,
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        return addr

    def test_tx_wrong_json_metadata_format(
        self, cluster_session: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Build transaction with wrong fromat of metadata JSON."""
        json_file = Path(__file__).parent / "data" / "tx_metadata_wrong.json"
        assert json_file.exists()

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file], metadata_json_files=[json_file]
        )

        # it should NOT be possible to build a transaction using wrongly formatted metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_session.build_raw_tx(
                src_address=payment_addr.address, tx_files=tx_files,
            )
        assert "The JSON metadata top level must be a map with unsigned integer keys" in str(
            excinfo.value
        )

    def test_tx_invalid_json_metadata(
        self, cluster_session: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Build transaction with invalid metadata JSON."""
        json_file = Path(__file__).parent / "data" / "tx_metadata_invalid.json"
        assert json_file.exists()

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file], metadata_json_files=[json_file]
        )

        # it should NOT be possible to build a transaction using an invalid metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_session.build_raw_tx(
                src_address=payment_addr.address, tx_files=tx_files,
            )
        assert "Failed reading: satisfy" in str(excinfo.value)

    def test_tx_too_long_metadata_json(
        self, cluster_session: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Build transaction with metadata JSON longer than 64 bytes."""
        json_file = Path(__file__).parent / "data" / "tx_metadata_long.json"
        assert json_file.exists()

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file], metadata_json_files=[json_file]
        )

        # it should NOT be possible to build a transaction using too long metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_session.build_raw_tx(
                src_address=payment_addr.address, tx_files=tx_files,
            )
        assert "JSON string is longer than 64 bytes" in str(excinfo.value)

    def test_tx_metadata_json(
        self, cluster_session: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata JSON."""
        json_file = Path(__file__).parent / "data" / "tx_metadata.json"
        assert json_file.exists()

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file], metadata_json_files=[json_file]
        )
        tx_raw_output = cluster_session.send_tx(src_address=payment_addr.address, tx_files=tx_files)
        assert tx_raw_output.fee, "Transaction had no fee"
        # TODO: check that the data is on blockchain
