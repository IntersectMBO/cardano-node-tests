import logging

import pytest

from cardano_node_tests.utils.clusterlib import CLIError
from cardano_node_tests.utils.clusterlib import PoolData
from cardano_node_tests.utils.clusterlib import TxFiles
from cardano_node_tests.utils.helpers import check_pool_data
from cardano_node_tests.utils.helpers import create_payment_addrs
from cardano_node_tests.utils.helpers import create_stake_addrs
from cardano_node_tests.utils.helpers import fund_from_faucet
from cardano_node_tests.utils.helpers import wait_for_stake_distribution
from cardano_node_tests.utils.helpers import write_json

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("test_staking")


class TestDelegateAddr:
    def test_delegate_using_addr(self, cluster_session, addrs_data_session, temp_dir):
        """Submit registration certificate and delegate to pool using address."""
        cluster = cluster_session

        stake_addr = create_stake_addrs(cluster, temp_dir, "addr_delegate_using_addr")[0]
        payment_addr = create_payment_addrs(
            cluster, temp_dir, "addr_delegate_using_addr", stake_vkey_file=stake_addr.vkey_file
        )[0]
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            temp_dir, "addr_delegate_using_addr", stake_addr.vkey_file
        )

        src_address = payment_addr.address

        # fund source address
        fund_from_faucet(cluster, addrs_data_session["user1"], src_address)
        src_init_balance = cluster.get_address_balance(src_address)

        tx_files = TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[payment_addr.skey_file, stake_addr.skey_file],
        )

        tx_raw_data = cluster.send_tx(src_address, tx_files=tx_files)
        cluster.wait_for_new_tip(slots_to_wait=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_data.fee - cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        # delegate the addr0 stake address to one stake pool id
        first_pool_id_in_stake_dist = list(wait_for_stake_distribution(cluster))[0]

        src_init_balance = cluster.get_address_balance(src_address)

        delegation_fee = cluster.calculate_tx_fee(src_address, tx_files=tx_files)

        try:
            cluster.delegate_stake_addr(
                stake_addr_skey=stake_addr.skey_file,
                pool_id=first_pool_id_in_stake_dist,
                delegation_fee=delegation_fee,
            )
        except CLIError as excinfo:
            if "command not implemented yet" in str(excinfo):
                return

        cluster.wait_for_new_tip(slots_to_wait=2)

        stake_addr_info = cluster.get_stake_addr_info(stake_addr.address)
        assert (
            stake_addr_info.delegation is not None
        ), f"Stake address was not delegated yet: {stake_addr_info}"

        assert (
            cluster.get_address_balance(src_address) == src_init_balance - delegation_fee
        ), f"Incorrect balance for source address `{src_address}`"

    def test_delegate_using_cert(self, cluster_session, addrs_data_session, temp_dir):
        """Submit registration certificate and delegate to pool using certificate."""
        cluster = cluster_session

        stake_addr = create_stake_addrs(cluster, temp_dir, "addr_delegate_using_cert")[0]
        payment_addr = create_payment_addrs(
            cluster, temp_dir, "addr_delegate_using_cert", stake_vkey_file=stake_addr.vkey_file
        )[0]
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            temp_dir, "addr_delegate_using_cert", stake_addr.vkey_file
        )
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            temp_dir,
            "addr_delegate_using_cert",
            stake_vkey_file=stake_addr.vkey_file,
            node_cold_vkey_file=addrs_data_session["node-pool1"]["cold_key_pair"].vkey_file,
        )

        src_address = payment_addr.address

        # fund source address
        fund_from_faucet(cluster, addrs_data_session["user1"], src_address)
        src_init_balance = cluster.get_address_balance(src_address)

        tx_files = TxFiles(
            certificate_files=[stake_addr_reg_cert_file, stake_addr_deleg_cert_file],
            signing_key_files=[payment_addr.skey_file, stake_addr.skey_file],
        )

        tx_raw_data = cluster.send_tx(src_address, tx_files=tx_files)
        cluster.wait_for_new_tip(slots_to_wait=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_data.fee - cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        wait_for_stake_distribution(cluster)

        stake_addr_info = cluster.get_stake_addr_info(stake_addr.address)
        assert (
            stake_addr_info.delegation is not None
        ), f"Stake address was not delegated yet: {stake_addr_info}"


class TestStakePool:
    def _pool_registration(self, cluster, addrs_data, temp_dir, pool_data, test_name):
        """Create and register a stake pool - common functionality for tests."""
        stake_addr = create_stake_addrs(cluster, temp_dir, f"addr0_{test_name}")[0]
        payment_addr = create_payment_addrs(
            cluster, temp_dir, f"addr0_{test_name}", stake_vkey_file=stake_addr.vkey_file
        )[0]
        node_vrf = cluster.gen_vrf_key_pair(destination_dir=temp_dir, node_name=pool_data.pool_name)
        node_cold = cluster.gen_cold_key_pair_and_counter(
            destination_dir=temp_dir, node_name=pool_data.pool_name
        )

        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            destination_dir=temp_dir,
            addr_name=f"addr0_{test_name}",
            stake_vkey_file=stake_addr.vkey_file,
        )
        pool_reg_cert_file = cluster.gen_pool_registration_cert(
            destination_dir=temp_dir,
            pool_data=pool_data,
            node_vrf_vkey_file=node_vrf.vkey_file,
            node_cold_vkey_file=node_cold.vkey_file,
            owner_stake_vkey_files=[stake_addr.vkey_file],
        )
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            destination_dir=temp_dir,
            addr_name=f"addr0_{test_name}",
            stake_vkey_file=stake_addr.vkey_file,
            node_cold_vkey_file=node_cold.vkey_file,
        )

        src_address = payment_addr.address

        # fund source address
        fund_from_faucet(cluster, addrs_data["user1"], src_address, amount=900_000_000)
        src_init_balance = cluster.get_address_balance(src_address)

        tx_files = TxFiles(
            certificate_files=[
                pool_reg_cert_file,
                stake_addr_reg_cert_file,
                stake_addr_deleg_cert_file,
            ],
            signing_key_files=[payment_addr.skey_file, stake_addr.skey_file, node_cold.skey_file],
        )

        tx_raw_data = cluster.send_tx(src_address, tx_files=tx_files)
        cluster.wait_for_new_tip(slots_to_wait=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance
            - tx_raw_data.fee
            - cluster.get_key_deposit()
            - cluster.get_pool_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        wait_for_stake_distribution(cluster)

        stake_addr_info = cluster.get_stake_addr_info(stake_addr.address)
        assert (
            stake_addr_info.delegation is not None
        ), f"Stake address was not delegated yet: {stake_addr_info}"

        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)
        pool_ledger_state = cluster.get_registered_stake_pools_ledger_state().get(stake_pool_id)

        assert stake_pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"
        assert pool_ledger_state, (
            "The newly created stake pool id is not shown inside the available stake pools;\n"
            f"Pool ID: {stake_pool_id} vs Existing IDs: "
            f"{list(cluster.get_registered_stake_pools_ledger_state())}"
        )
        # TODO: change this once 'stake_addr_info' contain stake address, not hash
        assert (
            f"e0{pool_ledger_state['owners'][0]}" == stake_addr_info.address_hash
        ), "'owner' value is different than expected"
        assert not check_pool_data(pool_ledger_state, pool_data)

    def test_stake_pool_metadata_1owner(self, cluster_session, addrs_data_session, temp_dir):
        """Create and register a stake pool with metadata and 1 owner."""
        cluster = cluster_session

        pool_metadata = {
            "name": "QA E2E test",
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = write_json(temp_dir / "pool_registration_metadata.json", pool_metadata)

        pool_data = PoolData(
            pool_name="poolY",
            pool_pledge=1000,
            pool_cost=15,
            pool_margin=0.2,
            pool_metadata_url="https://www.where_metadata_file_is_located.com",
            pool_metadata_hash=cluster.gen_pool_metadata_hash(
                pool_metadata_file=pool_metadata_file
            ),
        )

        self._pool_registration(
            cluster=cluster,
            addrs_data=addrs_data_session,
            temp_dir=temp_dir,
            pool_data=pool_data,
            test_name="test_stake_pool_metadata_1owner",
        )

    def test_stake_pool_1owner(self, cluster_session, addrs_data_session, temp_dir):
        """Create and register a stake pool with 1 owner."""
        cluster = cluster_session

        pool_data = PoolData(
            pool_name="poolX", pool_pledge=12345, pool_cost=123456789, pool_margin=0.123,
        )

        self._pool_registration(
            cluster=cluster,
            addrs_data=addrs_data_session,
            temp_dir=temp_dir,
            pool_data=pool_data,
            test_name="test_stake_pool_1owner",
        )

    def test_stake_pool_2owners(self, cluster_session, addrs_data_session, temp_dir):
        """Create and register a stake pool with 2 owners."""
        cluster = cluster_session
        temp_template = "test_stake_pool_2owners"
        no_of_addr = 2

        pool_data = PoolData(
            pool_name="pool_multiple_owners", pool_pledge=100_000, pool_cost=500, pool_margin=0.3,
        )

        node_vrf = cluster.gen_vrf_key_pair(destination_dir=temp_dir, node_name=pool_data.pool_name)
        node_cold = cluster.gen_cold_key_pair_and_counter(
            destination_dir=temp_dir, node_name=pool_data.pool_name
        )

        stake_addrs = []
        payment_addrs = []
        stake_addr_reg_cert_files = []
        stake_addr_deleg_cert_files = []
        for i in range(no_of_addr):
            stake_addr = create_stake_addrs(cluster, temp_dir, f"addr{i}_{temp_template}")[0]
            payment_addr = create_payment_addrs(
                cluster, temp_dir, f"addr{i}_{temp_template}", stake_vkey_file=stake_addr.vkey_file
            )[0]
            stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
                destination_dir=temp_dir,
                addr_name=f"addr{i}_{temp_template}",
                stake_vkey_file=stake_addr.vkey_file,
            )
            stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
                destination_dir=temp_dir,
                addr_name=f"addr{i}_{temp_template}",
                stake_vkey_file=stake_addr.vkey_file,
                node_cold_vkey_file=node_cold.vkey_file,
            )
            stake_addrs.append(stake_addr)
            payment_addrs.append(payment_addr)
            stake_addr_reg_cert_files.append(stake_addr_reg_cert_file)
            stake_addr_deleg_cert_files.append(stake_addr_deleg_cert_file)

        pool_reg_cert_file = cluster.gen_pool_registration_cert(
            destination_dir=temp_dir,
            pool_data=pool_data,
            node_vrf_vkey_file=node_vrf.vkey_file,
            node_cold_vkey_file=node_cold.vkey_file,
            owner_stake_vkey_files=[r.vkey_file for r in stake_addrs],
        )

        # fund source addresses
        fund_from_faucet(
            cluster,
            addrs_data_session["user1"],
            *[r.address for r in payment_addrs],
            amount=900_000_000,
        )

        src_address = payment_addrs[0].address
        src_init_balance = cluster.get_address_balance(src_address)

        tx_files = TxFiles(
            certificate_files=[
                pool_reg_cert_file,
                *stake_addr_reg_cert_files,
                *stake_addr_deleg_cert_files,
            ],
            signing_key_files=[
                *[r.skey_file for r in payment_addrs],
                *[r.skey_file for r in stake_addrs],
                node_cold.skey_file,
            ],
        )

        tx_raw_data = cluster.send_tx(src_address, tx_files=tx_files)
        cluster.wait_for_new_tip(slots_to_wait=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance
            - tx_raw_data.fee
            - no_of_addr * cluster.get_key_deposit()
            - cluster.get_pool_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        wait_for_stake_distribution(cluster)

        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)
        pool_ledger_state = cluster.get_registered_stake_pools_ledger_state().get(stake_pool_id)

        assert pool_ledger_state, (
            "The newly created stake pool id is not shown inside the available stake pools;\n"
            f"Pool ID: {stake_pool_id} vs Existing IDs: "
            f"{list(cluster.get_registered_stake_pools_ledger_state())}"
        )

        assert not check_pool_data(pool_ledger_state, pool_data)

        for addr_rec in stake_addrs:
            stake_addr_info = cluster.get_stake_addr_info(addr_rec.address)

            assert (
                stake_addr_info.delegation is not None
            ), f"Stake address was not delegated yet: {stake_addr_info}"

            assert (
                stake_pool_id == stake_addr_info.delegation
            ), "Stake address delegated to wrong pool"

            # TODO: change this once 'stake_addr_info' contain stake address, not hash
            assert (
                # strip 'e0' from the beginning of the address hash
                stake_addr_info.address_hash[2:]
                in pool_ledger_state["owners"]
            ), "'owner' value is different than expected"
