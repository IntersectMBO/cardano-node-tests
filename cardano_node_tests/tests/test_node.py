import logging
import time

import pytest

from cardano_node_tests.utils.clusterlib import TxFiles
from cardano_node_tests.utils.clusterlib import TxOut
from cardano_node_tests.utils.helpers import create_addrs
from cardano_node_tests.utils.helpers import fund_addr_from_genesis

LOGGER = logging.getLogger(__name__)


def test_update_proposal(cluster_session):
    """Submit update proposal."""
    cluster_session.refresh_pparams()
    orig_value = cluster_session.pparams["decentralisationParam"]
    sleep_time = cluster_session.slot_length * cluster_session.epoch_length

    if cluster_session.get_last_block_epoch() < 1:
        LOGGER.info(f"Waiting 1 epoch to submit proposal ({sleep_time} seconds).")
        time.sleep(sleep_time)

    def _update_proposal(param_value):
        cluster_session.submit_update_proposal(
            ["--decentralization-parameter", str(param_value)],
            epoch=cluster_session.get_last_block_epoch(),
        )

        LOGGER.info(
            f"Update Proposal submited (param_value={param_value}). "
            f"Sleeping until next epoch ({sleep_time} seconds)."
        )
        time.sleep(sleep_time + 15)

        cluster_session.refresh_pparams()
        d = cluster_session.pparams["decentralisationParam"]
        assert str(d) == str(
            param_value
        ), f"Cluster update proposal failed! Param value: {d}.\nTip:{cluster_session.get_tip()}"

    _update_proposal(0.5)
    # revert to original value
    _update_proposal(orig_value)


@pytest.mark.clean_cluster
def test_dummy_clean():
    pass


class TestBasic:
    @pytest.fixture(scope="class")
    def temp_dir(self, tmp_path_factory):
        return tmp_path_factory.mktemp("test_basic")

    @pytest.fixture(scope="class")
    def created_addrs(self, cluster_session, temp_dir):
        """Create 2 new payment addresses (addr0, addr1)."""
        return create_addrs(cluster_session, temp_dir, "addr0", "addr1")

    def test_transfer_funds(self, cluster_session, addrs_data_session, created_addrs):
        """Send (tx_fee + 2000) Lovelace from user1 (the faucet) to addr0."""
        cluster = cluster_session
        src_address = addrs_data_session["user1"]["payment_addr"]
        amount = 2000

        destinations = [TxOut(address=created_addrs[0].addr, amount=amount)]
        src_init_balances = cluster.get_address_balance(src_address)
        dst_init_balances = {
            d.address: cluster.get_address_balance(d.address) for d in destinations
        }

        tx_files = TxFiles(
            signing_key_files=[addrs_data_session["user1"]["payment_key_pair"].skey_file]
        )
        tx_raw_data = cluster.send_funds(src_address, destinations, tx_files=tx_files)

        cluster.wait_for_new_tip(slots_to_wait=2)

        expected_src_balance = src_init_balances - tx_raw_data.fee - len(destinations) * amount
        assert (
            cluster.get_address_balance(src_address) == expected_src_balance
        ), f"Incorrect balance for source address `{src_address}`"

        for dst in destinations:
            assert (
                cluster.get_address_balance(dst.address) == dst_init_balances[dst.address] + amount
            ), f"Incorrect balance for destination address `{dst.address}`"

    def test_transfer_all_funds(self, cluster_session, created_addrs):
        """Send ALL funds from addr0 to addr1."""
        cluster = cluster_session
        src_addr_data = created_addrs[0]
        dst_addr_data = created_addrs[1]

        # fund source address
        fund_addr_from_genesis(cluster, src_addr_data.addr)

        src_init_balance = cluster.get_address_balance(src_addr_data.addr)
        dst_init_balance = cluster.get_address_balance(dst_addr_data.addr)

        # amount value -1 means all available funds
        destinations = [TxOut(address=created_addrs[1].addr, amount=-1)]
        tx_files = TxFiles(signing_key_files=[src_addr_data.skey_file])
        tx_raw_data = cluster.send_funds(src_addr_data.addr, destinations, tx_files=tx_files)

        cluster.wait_for_new_tip(slots_to_wait=2)

        assert (
            cluster.get_address_balance(src_addr_data.addr) == 0
        ), f"Incorrect balance for source address `{src_addr_data.addr}`"

        assert (
            cluster.get_address_balance(dst_addr_data.addr)
            == dst_init_balance + src_init_balance - tx_raw_data.fee
        ), f"Incorrect balance for destination address `{dst_addr_data.addr}`"
