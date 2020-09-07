import functools
import itertools
import json
import logging
import string
from pathlib import Path
from typing import List
from typing import Tuple

import cbor2
import hypothesis
import hypothesis.strategies as st
import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"

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

    @pytest.mark.parametrize("amount", (1, 10, 200, 2000, 100_000))
    def test_transfer_funds(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
    ):
        """Send funds to payment address."""
        cluster = cluster_session

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_output = cluster.send_funds(
            src_address=src_address,
            destinations=destinations,
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
            src_address=src_address,
            destinations=destinations,
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
            src_address=src_address,
            destinations=destinations,
            tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        txid = cluster.get_txid(tx_raw_output.out_file)
        utxo = cluster.get_utxo(src_address)
        assert len(txid) == 64
        assert txid in (u.utxo_hash for u in utxo)


class TestMultiInOut:
    @pytest.fixture(scope="class")
    def payment_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ) -> List[clusterlib.AddressRecord]:
        """Create 11 new payment addresses."""
        addrs = helpers.create_payment_addr_records(
            *[f"addr_multi_in_out{i}" for i in range(201)],
            cluster_obj=cluster_session,
        )

        # fund source addresses
        helpers.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            amount=100_000_000,
            request=request,
        )

        return addrs

    def _from_to_transactions(
        self,
        cluster_obj: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
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

        # send TX - change is returned to `src_address`
        tx_raw_output = cluster_obj.send_tx(
            src_address=src_address,
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

    def test_10_transactions(
        self, cluster_session: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send 10 transactions to payment address.

        Test 10 different UTXOs in addr0.
        """
        cluster = cluster_session
        no_of_transactions = 10

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        fee = cluster.calculate_tx_fee(
            src_address=src_address,
            dst_addresses=[dst_address],
            tx_files=tx_files,
            ttl=ttl,
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

    @pytest.mark.parametrize("amount", [1, 100, 11_000])
    def test_transaction_to_10_addrs_from_1_addr(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
    ):
        """Tests 1 tx from 1 payment address to 10 payment addresses."""
        self._from_to_transactions(
            cluster_obj=cluster_session,
            payment_addrs=payment_addrs,
            from_num=1,
            to_num=10,
            amount=amount,
        )

    @pytest.mark.parametrize("amount", [1, 100, 11_000, 100_000])
    def test_transaction_to_1_addr_from_10_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
    ):
        """Tests 1 tx from 10 payment addresses to 1 payment address."""
        self._from_to_transactions(
            cluster_obj=cluster_session,
            payment_addrs=payment_addrs,
            from_num=10,
            to_num=1,
            amount=amount,
        )

    @pytest.mark.parametrize("amount", [1, 100, 11_000])
    def test_transaction_to_10_addrs_from_10_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
    ):
        """Tests 1 tx from 10 payment addresses to 10 payment addresses."""
        self._from_to_transactions(
            cluster_obj=cluster_session,
            payment_addrs=payment_addrs,
            from_num=10,
            to_num=10,
            amount=amount,
        )

    @pytest.mark.parametrize("amount", [1, 100, 1000])
    def test_transaction_to_100_addrs_from_50_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        amount: int,
    ):
        """Tests 1 tx from 100 payment addresses to 50 payment addresses."""
        self._from_to_transactions(
            cluster_obj=cluster_session,
            payment_addrs=payment_addrs,
            from_num=50,
            to_num=100,
            amount=amount,
        )


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
            src_address=src_address,
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
                out_file=temp_dir / f"{clusterlib.get_timestamped_rand_str()}_tx.body",
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
            cluster_obj=cluster_session,
            name_template="test_negative",
            no_of_addr=2,
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
                src_address=addr,
                txouts=destinations,
                tx_files=tx_files,
                fee=0,
            )
        assert "invalid address" in str(excinfo.value)

    def _get_raw_tx_values(
        self,
        cluster_obj: clusterlib.ClusterLib,
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
        out_file = temp_dir / f"{clusterlib.get_timestamped_rand_str()}_tx.body"

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

    def test_past_ttl(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
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
            src_address=src_address,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
        )

        # it should NOT be possible to submit a transaction with ttl in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(out_file_signed)
        assert "ExpiredUTxO" in str(excinfo.value)

    def test_duplicated_tx(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
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
            src_address=src_address,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.build_raw_tx(
            src_address=src_address, txouts=destinations, tx_files=tx_files, fee=fee
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
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

    def test_wrong_signing_key(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Send a transaction signed with wrong signing key."""
        # use wrong signing key
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[1].payment.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=100)]

        # it should NOT be possible to submit a transaction with wrong signing key
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_session.send_tx(
                src_address=pool_users[0].payment.address, txouts=destinations, tx_files=tx_files
            )
        assert "MissingVKeyWitnessesUTXOW" in str(excinfo.value)

    def test_extra_signing_keys(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Send a transaction with extra signing key."""
        cluster = cluster_session
        amount = 100

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        # use extra signing key
        tx_files = clusterlib.TxFiles(
            signing_key_files=[pool_users[0].payment.skey_file, pool_users[1].payment.skey_file]
        )
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # it should be possible to submit a transaction with extra signing key
        tx_raw_output = cluster.send_tx(
            src_address=src_address, txouts=destinations, tx_files=tx_files
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

    def test_duplicate_signing_keys(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Send a transaction with duplicate signing key."""
        cluster = cluster_session
        amount = 100

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        # use extra signing key
        tx_files = clusterlib.TxFiles(
            signing_key_files=[pool_users[0].payment.skey_file, pool_users[0].payment.skey_file]
        )
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # it should be possible to submit a transaction with duplicate signing key
        tx_raw_output = cluster.send_tx(
            src_address=src_address, txouts=destinations, tx_files=tx_files
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

    def test_send_funds_to_reward_address(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
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
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
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
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
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

    def test_missing_fee(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        temp_dir: Path,
    ):
        """Build a transaction with a missing `--fee` parameter."""
        cluster = cluster_session

        tx_raw_output = self._get_raw_tx_values(
            cluster_obj=cluster, pool_users=pool_users, temp_dir=temp_dir
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

    def test_missing_ttl(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        temp_dir: Path,
    ):
        """Build a transaction with a missing `--ttl` parameter."""
        cluster = cluster_session

        tx_raw_output = self._get_raw_tx_values(
            cluster_obj=cluster, pool_users=pool_users, temp_dir=temp_dir
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

    def test_missing_tx_in(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        temp_dir: Path,
    ):
        """Build a transaction with a missing `--tx-in` parameter."""
        cluster = cluster_session

        tx_raw_output = self._get_raw_tx_values(
            cluster_obj=cluster, pool_users=pool_users, temp_dir=temp_dir
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
        assert "Missing: --tx-in TX-IN" in str(excinfo.value)

    def test_missing_tx_out(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        temp_dir: Path,
    ):
        """Build a transaction with a missing `--tx-out` parameter."""
        cluster = cluster_session

        tx_raw_output = self._get_raw_tx_values(
            cluster_obj=cluster, pool_users=pool_users, temp_dir=temp_dir
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
        assert "Missing: --tx-out TX-OUT" in str(excinfo.value)


class TestMetadata:
    JSON_METADATA_FILE = DATA_DIR / "tx_metadata.json"
    JSON_METADATA_WRONG_FILE = DATA_DIR / "tx_metadata_wrong.json"
    JSON_METADATA_INVALID_FILE = DATA_DIR / "tx_metadata_invalid.json"
    JSON_METADATA_LONG_FILE = DATA_DIR / "tx_metadata_long.json"
    CBOR_METADATA_FILE = DATA_DIR / "tx_metadata.cbor"

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
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_WRONG_FILE],
        )

        # it should NOT be possible to build a transaction using wrongly formatted metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_session.build_raw_tx(
                src_address=payment_addr.address,
                tx_files=tx_files,
            )
        assert "The JSON metadata top level must be a map with unsigned integer keys" in str(
            excinfo.value
        )

    def test_tx_invalid_json_metadata(
        self, cluster_session: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Build transaction with invalid metadata JSON."""
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_INVALID_FILE],
        )

        # it should NOT be possible to build a transaction using an invalid metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_session.build_raw_tx(
                src_address=payment_addr.address,
                tx_files=tx_files,
            )
        assert "Failed reading: satisfy" in str(excinfo.value)

    def test_tx_too_long_metadata_json(
        self, cluster_session: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Build transaction with metadata JSON longer than 64 bytes."""
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_LONG_FILE],
        )

        # it should NOT be possible to build a transaction using too long metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_session.build_raw_tx(
                src_address=payment_addr.address,
                tx_files=tx_files,
            )
        assert "JSON string is longer than 64 bytes" in str(excinfo.value)

    def test_tx_metadata_json(
        self, cluster_session: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata JSON."""
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
        )
        tx_raw_output = cluster_session.send_tx(src_address=payment_addr.address, tx_files=tx_files)
        cluster_session.wait_for_new_block(new_blocks=2)
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
        ), "Metadata in TX body doesn't match original metadata"

    def test_tx_metadata_cbor(
        self, cluster_session: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata CBOR."""
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_cbor_files=[self.CBOR_METADATA_FILE],
        )
        tx_raw_output = cluster_session.send_tx(src_address=payment_addr.address, tx_files=tx_files)
        cluster_session.wait_for_new_block(new_blocks=2)
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

    def test_tx_metadata_both(
        self, cluster_session: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with both metadata JSON and CBOR."""
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
            metadata_cbor_files=[self.CBOR_METADATA_FILE],
        )
        tx_raw_output = cluster_session.send_tx(src_address=payment_addr.address, tx_files=tx_files)
        cluster_session.wait_for_new_block(new_blocks=2)
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
