import logging

import pytest

from cardano_node_tests.utils.clusterlib import TxFiles
from cardano_node_tests.utils.clusterlib import TxOut
from cardano_node_tests.utils.helpers import create_addrs
from cardano_node_tests.utils.helpers import fund_addr_from_genesis

LOGGER = logging.getLogger(__name__)


@pytest.mark.clean_cluster
def test_dummy_clean():
    pass


class TestBasic:
    @pytest.fixture(scope="class")
    def temp_dir(self, tmp_path_factory):
        return tmp_path_factory.mktemp("test_basic")

    @pytest.fixture(scope="class")
    def payment_addrs(self, cluster_session, temp_dir):
        """Create 2 new payment addresses (addr0, addr1)."""
        return create_addrs(cluster_session, temp_dir, "addr0", "addr1")

    def test_transfer_funds(self, cluster_session, addrs_data_session, payment_addrs):
        """Send (tx_fee + 2000) Lovelace from user1 (the faucet) to addr0."""
        cluster = cluster_session
        amount = 2000

        src_address = addrs_data_session["user1"]["payment_addr"]
        dst_address = payment_addrs[0].address

        # fund source address
        fund_addr_from_genesis(cluster, src_address)

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        destinations = [TxOut(address=dst_address, amount=amount)]
        tx_files = TxFiles(
            signing_key_files=[addrs_data_session["user1"]["payment_key_pair"].skey_file]
        )

        tx_raw_data = cluster.send_funds(src_address, destinations, tx_files=tx_files)
        cluster.wait_for_new_tip(slots_to_wait=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_data.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

    def test_transfer_all_funds(self, cluster_session, payment_addrs):
        """Send ALL funds from addr0 to addr1."""
        cluster = cluster_session

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        # fund source address
        fund_addr_from_genesis(cluster, src_address)

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        # amount value -1 means all available funds
        destinations = [TxOut(address=payment_addrs[1].address, amount=-1)]
        tx_files = TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_data = cluster.send_funds(src_address, destinations, tx_files=tx_files)
        cluster.wait_for_new_tip(slots_to_wait=2)

        assert (
            cluster.get_address_balance(src_address) == 0
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address)
            == dst_init_balance + src_init_balance - tx_raw_data.fee
        ), f"Incorrect balance for destination address `{dst_address}`"


class Test10InOut:
    @pytest.fixture(scope="class")
    def temp_dir(self, tmp_path_factory):
        return tmp_path_factory.mktemp("test_10_in_out")

    @pytest.fixture(scope="class")
    def payment_addrs(self, cluster_session, temp_dir):
        """Create 11 new payment addresses (addr0..addr10)."""
        return create_addrs(cluster_session, temp_dir, *[f"addr{i}" for i in range(11)])

    def test_10_transactions(self, cluster_session, addrs_data_session, payment_addrs):
        """Send 10 transactions of (tx_fee / 10 + 1000) Lovelace from user1 (the faucet) to addr0.

        Test 10 different UTXOs in addr0.
        """
        cluster = cluster_session
        no_of_transactions = len(payment_addrs) - 1

        src_address = addrs_data_session["user1"]["payment_addr"]
        dst_address = payment_addrs[0].address

        # fund source address
        fund_addr_from_genesis(cluster, src_address)

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        tx_files = TxFiles(
            signing_key_files=[addrs_data_session["user1"]["payment_key_pair"].skey_file]
        )
        ttl = cluster.calculate_tx_ttl()

        fee_txouts = [TxOut(address=dst_address, amount=1)]
        fee = cluster.calculate_tx_fee(src_address, txouts=fee_txouts, tx_files=tx_files, ttl=ttl)
        amount = int(fee / no_of_transactions + 1000)
        destinations = [TxOut(address=dst_address, amount=amount)]

        for __ in range(no_of_transactions):
            cluster.send_funds(src_address, destinations, tx_files=tx_files, fee=fee, ttl=ttl)
            cluster.wait_for_new_tip(slots_to_wait=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - fee * no_of_transactions - amount * no_of_transactions
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address)
            == dst_init_balance + amount * no_of_transactions
        ), f"Incorrect balance for destination address `{dst_address}`"

    def test_transaction_to_10_addrs(self, cluster_session, payment_addrs):
        """Send 1 transaction from addr0 to addr1..addr10"."""
        cluster = cluster_session
        src_address = payment_addrs[0].address
        # addr1..addr10
        dst_addresses = [payment_addrs[i].address for i in range(1, len(payment_addrs))]

        # fund source address
        fund_addr_from_genesis(cluster, src_address)

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balances = {addr: cluster.get_address_balance(addr) for addr in dst_addresses}

        tx_files = TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        fee_txouts = [TxOut(address=addr, amount=1) for addr in dst_addresses]
        fee = cluster.calculate_tx_fee(src_address, txouts=fee_txouts, tx_files=tx_files, ttl=ttl)
        amount = int((cluster.get_address_balance(src_address) - fee) / len(dst_addresses))
        destinations = [TxOut(address=addr, amount=amount) for addr in dst_addresses]

        cluster.send_funds(src_address, destinations, tx_files=tx_files, fee=fee, ttl=ttl)
        cluster.wait_for_new_tip(slots_to_wait=2)

        assert cluster.get_address_balance(src_address) == src_init_balance - fee - amount * len(
            dst_addresses
        ), f"Incorrect balance for source address `{src_address}`"

        for addr in dst_addresses:
            assert (
                cluster.get_address_balance(addr) == dst_init_balances[addr] + amount
            ), f"Incorrect balance for destination address `{addr}`"
