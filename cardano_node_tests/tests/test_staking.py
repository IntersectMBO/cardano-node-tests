import logging

import pytest

from cardano_node_tests.utils.clusterlib import CLIError
from cardano_node_tests.utils.clusterlib import KeyPair
from cardano_node_tests.utils.clusterlib import PoolData
from cardano_node_tests.utils.clusterlib import PoolOwner
from cardano_node_tests.utils.clusterlib import TxFiles
from cardano_node_tests.utils.helpers import check_pool_data
from cardano_node_tests.utils.helpers import create_payment_addrs
from cardano_node_tests.utils.helpers import create_stake_addrs
from cardano_node_tests.utils.helpers import fund_from_faucet
from cardano_node_tests.utils.helpers import wait_for
from cardano_node_tests.utils.helpers import wait_for_stake_distribution
from cardano_node_tests.utils.helpers import write_json

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("test_staking")


class TestDelegateAddr:
    def test_delegate_using_addr(self, cluster_session, addrs_data_session, temp_dir, request):
        """Submit registration certificate and delegate to pool using stake address."""
        cluster = cluster_session
        temp_template = "test_delegate_using_addr"

        # create key pairs and addresses
        stake_addr = create_stake_addrs(cluster, temp_dir, f"addr0_{temp_template}")[0]
        payment_addr = create_payment_addrs(
            cluster, temp_dir, f"addr0_{temp_template}", stake_vkey_file=stake_addr.vkey_file
        )[0]
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            temp_dir, f"addr0_{temp_template}", stake_addr.vkey_file
        )

        src_address = payment_addr.address

        # fund source address
        fund_from_faucet(cluster, addrs_data_session["user1"], payment_addr, request=request)
        src_init_balance = cluster.get_address_balance(src_address)

        tx_files = TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[payment_addr.skey_file, stake_addr.skey_file],
        )

        # register stake address
        tx_raw_data = cluster.send_tx(src_address, tx_files=tx_files)
        cluster.wait_for_new_tip(slots_to_wait=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_data.fee - cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{src_address}`"
        src_register_balance = cluster.get_address_balance(src_address)

        first_pool_id_in_stake_dist = list(wait_for_stake_distribution(cluster))[0]
        delegation_fee = cluster.calculate_tx_fee(src_address, tx_files=tx_files)

        # delegate the stake address to pool
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

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(src_address) == src_register_balance - delegation_fee
        ), f"Incorrect balance for source address `{src_address}`"

        wait_for_stake_distribution(cluster)

        # check that the stake address was delegated
        stake_addr_info = cluster.get_stake_addr_info(stake_addr.address)
        assert (
            stake_addr_info.delegation is not None
        ), f"Stake address was not delegated yet: {stake_addr_info}"

        assert (
            first_pool_id_in_stake_dist == stake_addr_info.delegation
        ), "Stake address delegated to wrong pool"

        pool_ledger_state = cluster.get_registered_stake_pools_ledger_state().get(
            first_pool_id_in_stake_dist
        )
        # TODO: change this once 'stake_addr_info' contain stake address, not hash
        assert (
            # strip 'e0' from the beginning of the address hash
            stake_addr_info.address_hash[2:]
            in pool_ledger_state["owners"]
        ), "'owner' value is different than expected"

    def test_delegate_using_cert(self, cluster_session, addrs_data_session, temp_dir, request):
        """Submit registration certificate and delegate to pool using certificate."""
        cluster = cluster_session
        temp_template = "test_delegate_using_cert"
        node_cold = addrs_data_session["node-pool1"]["cold_key_pair"]

        # create key pairs and addresses
        stake_addr = create_stake_addrs(cluster, temp_dir, f"addr0_{temp_template}")[0]
        payment_addr = create_payment_addrs(
            cluster, temp_dir, f"addr0_{temp_template}", stake_vkey_file=stake_addr.vkey_file
        )[0]

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            temp_dir, f"addr0_{temp_template}", stake_addr.vkey_file
        )
        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            destination_dir=temp_dir,
            addr_name=f"addr0_{temp_template}",
            stake_vkey_file=stake_addr.vkey_file,
            node_cold_vkey_file=node_cold.vkey_file,
        )

        src_address = payment_addr.address

        # fund source address
        fund_from_faucet(cluster, addrs_data_session["user1"], payment_addr, request=request)
        src_init_balance = cluster.get_address_balance(src_address)

        tx_files = TxFiles(
            certificate_files=[stake_addr_reg_cert_file, stake_addr_deleg_cert_file],
            signing_key_files=[payment_addr.skey_file, stake_addr.skey_file],
        )

        # register stake address and delegate it to pool
        tx_raw_data = cluster.send_tx(src_address, tx_files=tx_files)
        cluster.wait_for_new_tip(slots_to_wait=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_data.fee - cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        wait_for_stake_distribution(cluster)

        # check that the stake address was delegated
        stake_addr_info = cluster.get_stake_addr_info(stake_addr.address)
        assert (
            stake_addr_info.delegation is not None
        ), f"Stake address was not delegated yet: {stake_addr_info}"

        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)
        assert stake_pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

        pool_ledger_state = cluster.get_registered_stake_pools_ledger_state().get(stake_pool_id)
        # TODO: change this once 'stake_addr_info' contain stake address, not hash
        assert (
            # strip 'e0' from the beginning of the address hash
            stake_addr_info.address_hash[2:]
            in pool_ledger_state["owners"]
        ), "'owner' value is different than expected"


class TestStakePool:
    def _check_staking(self, cluster, stake_pool_id, pool_data, *stake_addrs):
        """Check that pool and staking were correctly setup."""
        LOGGER.info("Waiting up to 2 epochs for stake pool to be registered.")
        wait_for(
            lambda: stake_pool_id in cluster.get_stake_distribution(),
            delay=10,
            num_sec=2 * cluster.epoch_length,
            message="register stake pool",
        )

        # check that the pool was correctly registered on chain
        pool_ledger_state = cluster.get_registered_stake_pools_ledger_state().get(stake_pool_id)
        assert pool_ledger_state, (
            "The newly created stake pool id is not shown inside the available stake pools;\n"
            f"Pool ID: {stake_pool_id} vs Existing IDs: "
            f"{list(cluster.get_registered_stake_pools_ledger_state())}"
        )
        assert not check_pool_data(pool_ledger_state, pool_data)

        for addr_rec in stake_addrs:
            stake_addr_info = cluster.get_stake_addr_info(addr_rec.address)

            # check that the stake address was delegated
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

    def _pool_reg_in_single_tx(
        self, cluster, addrs_data, temp_dir, temp_template, pool_data, request
    ):
        """Create and register a stake pool, delegate stake address - all in single TX.

        Common functionality for tests.
        """
        # create key pairs and addresses
        stake_addr = create_stake_addrs(cluster, temp_dir, f"addr0_{temp_template}")[0]
        payment_addr = create_payment_addrs(
            cluster, temp_dir, f"addr0_{temp_template}", stake_vkey_file=stake_addr.vkey_file
        )[0]
        node_vrf = cluster.gen_vrf_key_pair(destination_dir=temp_dir, node_name=pool_data.pool_name)
        node_cold = cluster.gen_cold_key_pair_and_counter(
            destination_dir=temp_dir, node_name=pool_data.pool_name
        )

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            destination_dir=temp_dir,
            addr_name=f"addr0_{temp_template}",
            stake_vkey_file=stake_addr.vkey_file,
        )
        # create stake pool registration cert
        pool_reg_cert_file = cluster.gen_pool_registration_cert(
            destination_dir=temp_dir,
            pool_data=pool_data,
            node_vrf_vkey_file=node_vrf.vkey_file,
            node_cold_vkey_file=node_cold.vkey_file,
            owner_stake_vkey_files=[stake_addr.vkey_file],
        )
        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            destination_dir=temp_dir,
            addr_name=f"addr0_{temp_template}",
            stake_vkey_file=stake_addr.vkey_file,
            node_cold_vkey_file=node_cold.vkey_file,
        )

        src_address = payment_addr.address

        # fund source address
        fund_from_faucet(
            cluster, addrs_data["user1"], payment_addr, amount=900_000_000, request=request
        )
        src_init_balance = cluster.get_address_balance(src_address)

        tx_files = TxFiles(
            certificate_files=[
                pool_reg_cert_file,
                stake_addr_reg_cert_file,
                stake_addr_deleg_cert_file,
            ],
            signing_key_files=[payment_addr.skey_file, stake_addr.skey_file, node_cold.skey_file],
        )

        # register and delegate stake address, create and register pool
        tx_raw_data = cluster.send_tx(src_address, tx_files=tx_files)
        cluster.wait_for_new_tip(slots_to_wait=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance
            - tx_raw_data.fee
            - cluster.get_key_deposit()
            - cluster.get_pool_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        # check that pool and staking were correctly setup
        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)
        self._check_staking(
            cluster, stake_pool_id, pool_data, stake_addr,
        )

    def _pool_reg_in_multiple_tx(
        self, cluster, addrs_data, temp_dir, temp_template, pool_data, request
    ):
        """Create and register a stake pool - first TX; delegate stake address - second TX.

        Common functionality for tests.
        """
        # create key pairs and addresses
        stake_addr = create_stake_addrs(cluster, temp_dir, f"addr0_{temp_template}")[0]
        payment_addr = create_payment_addrs(
            cluster, temp_dir, f"addr0_{temp_template}", stake_vkey_file=stake_addr.vkey_file
        )[0]

        # fund source address
        fund_from_faucet(
            cluster, addrs_data["user1"], payment_addr, amount=1_000_000_000, request=request,
        )
        src_address = payment_addr.address
        src_init_balance = cluster.get_address_balance(src_address)

        pool_owner = PoolOwner(
            addr=payment_addr.address,
            stake_addr=stake_addr.address,
            addr_key_pair=KeyPair(
                vkey_file=payment_addr.vkey_file, skey_file=payment_addr.skey_file
            ),
            stake_key_pair=KeyPair(vkey_file=stake_addr.vkey_file, skey_file=stake_addr.skey_file),
        )

        # create and register pool
        pool_artifacts = cluster.create_stake_pool(
            destination_dir=temp_dir, pool_data=pool_data, pool_owner=pool_owner
        )

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            destination_dir=temp_dir,
            addr_name=f"addr0_{temp_template}",
            stake_vkey_file=stake_addr.vkey_file,
        )
        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            destination_dir=temp_dir,
            addr_name=f"addr0_{temp_template}",
            stake_vkey_file=stake_addr.vkey_file,
            node_cold_vkey_file=pool_artifacts.cold_key_pair_and_counter.vkey_file,
        )

        tx_files = TxFiles(
            certificate_files=[stake_addr_reg_cert_file, stake_addr_deleg_cert_file],
            signing_key_files=[
                payment_addr.skey_file,
                stake_addr.skey_file,
                pool_artifacts.cold_key_pair_and_counter.skey_file,
            ],
        )

        # register and delegate stake address
        tx_raw_data = cluster.send_tx(src_address, tx_files=tx_files)
        cluster.wait_for_new_tip(slots_to_wait=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_data.fee - cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        # check that pool and staking were correctly setup
        self._check_staking(
            cluster, pool_artifacts.stake_pool_id, pool_data, stake_addr,
        )

        return pool_artifacts, pool_owner

    def test_stake_pool_metadata_1owner(
        self, cluster_session, addrs_data_session, temp_dir, request
    ):
        """Create and register a stake pool with metadata and 1 owner."""
        cluster = cluster_session
        temp_template = "test_stake_pool_metadata_1owner"

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

        self._pool_reg_in_single_tx(
            cluster=cluster,
            addrs_data=addrs_data_session,
            temp_dir=temp_dir,
            temp_template=temp_template,
            pool_data=pool_data,
            request=request,
        )

    def test_stake_pool_1owner(self, cluster_session, addrs_data_session, temp_dir, request):
        """Create and register a stake pool with 1 owner."""
        cluster = cluster_session
        temp_template = "test_stake_pool_1owner"

        pool_data = PoolData(
            pool_name="poolX", pool_pledge=12345, pool_cost=123456789, pool_margin=0.123,
        )

        self._pool_reg_in_single_tx(
            cluster=cluster,
            addrs_data=addrs_data_session,
            temp_dir=temp_dir,
            temp_template=temp_template,
            pool_data=pool_data,
            request=request,
        )

    def test_stake_pool_2owners(self, cluster_session, addrs_data_session, temp_dir, request):
        """Create and register a stake pool with 2 owners."""
        cluster = cluster_session
        temp_template = "test_stake_pool_2owners"
        no_of_addr = 2

        pool_data = PoolData(
            pool_name="pool_multiple_owners", pool_pledge=100_000, pool_cost=500, pool_margin=0.3,
        )

        # create node VRF key pair
        node_vrf = cluster.gen_vrf_key_pair(destination_dir=temp_dir, node_name=pool_data.pool_name)
        # create node cold key pair and counter
        node_cold = cluster.gen_cold_key_pair_and_counter(
            destination_dir=temp_dir, node_name=pool_data.pool_name
        )

        stake_addrs = []
        payment_addrs = []
        stake_addr_reg_cert_files = []
        stake_addr_deleg_cert_files = []
        for i in range(no_of_addr):
            # create key pairs and addresses
            stake_addr = create_stake_addrs(cluster, temp_dir, f"addr{i}_{temp_template}")[0]
            payment_addr = create_payment_addrs(
                cluster, temp_dir, f"addr{i}_{temp_template}", stake_vkey_file=stake_addr.vkey_file
            )[0]

            # create stake address registration cert
            stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
                destination_dir=temp_dir,
                addr_name=f"addr{i}_{temp_template}",
                stake_vkey_file=stake_addr.vkey_file,
            )
            # create stake address delegation cert
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

        # create stake pool registration cert
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
            *payment_addrs,
            amount=900_000_000,
            request=request,
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

        # register and delegate stake addresses, create and register pool
        tx_raw_data = cluster.send_tx(src_address, tx_files=tx_files)
        cluster.wait_for_new_tip(slots_to_wait=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance
            - tx_raw_data.fee
            - no_of_addr * cluster.get_key_deposit()
            - cluster.get_pool_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        # check that pool and staking were correctly setup
        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)
        self._check_staking(
            cluster, stake_pool_id, pool_data, *stake_addrs,
        )

    def test_deregister_stake_pool(self, cluster_session, addrs_data_session, temp_dir, request):
        """Deregister stake pool."""
        cluster = cluster_session
        temp_template = "test_update_stake_pool"

        pool_metadata = {
            "name": "QA E2E test",
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = write_json(temp_dir / "pool_registration_metadata.json", pool_metadata)

        pool_data = PoolData(
            pool_name="poolZ",
            pool_pledge=222,
            pool_cost=123,
            pool_margin=0.512,
            pool_metadata_url="https://www.where_metadata_file_is_located.com",
            pool_metadata_hash=cluster.gen_pool_metadata_hash(
                pool_metadata_file=pool_metadata_file
            ),
        )

        pool_artifacts, pool_owner = self._pool_reg_in_multiple_tx(
            cluster=cluster,
            addrs_data=addrs_data_session,
            temp_dir=temp_dir,
            temp_template=temp_template,
            pool_data=pool_data,
            request=request,
        )

        src_register_balance = cluster.get_address_balance(pool_owner.addr)

        # deregister stake pool
        __, tx_raw_data = cluster.deregister_stake_pool(
            destination_dir=temp_dir,
            pool_owner=pool_owner,
            node_cold_key_pair=pool_artifacts.cold_key_pair_and_counter,
            epoch=cluster.get_last_block_epoch() + 1,
            pool_name=pool_data.pool_name,
        )

        LOGGER.info("Waiting up to 3 epochs for stake pool to be deregistered.")
        wait_for(
            lambda: pool_artifacts.stake_pool_id not in cluster.get_stake_distribution(),
            delay=10,
            num_sec=3 * cluster.epoch_length,
            message="deregister stake pool",
        )
        # TODO: what about pool deposit?
        assert src_register_balance - tx_raw_data.fee == cluster.get_address_balance(
            pool_owner.addr
        )

    def test_update_stake_pool_metadata(
        self, cluster_session, addrs_data_session, temp_dir, request
    ):
        """Update stake pool metadata."""
        cluster = cluster_session
        temp_template = "test_update_stake_pool"

        pool_metadata = {
            "name": "QA E2E test",
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = write_json(temp_dir / "pool_registration_metadata.json", pool_metadata)

        pool_metadata_updated = {
            "name": "QA_test_pool",
            "description": "pool description update",
            "ticker": "QA22",
            "homepage": "www.qa22.com",
        }
        pool_metadata_updated_file = write_json(
            temp_dir / "pool_registration_metadata_updated.json", pool_metadata_updated
        )

        pool_data = PoolData(
            pool_name="poolA",
            pool_pledge=4567,
            pool_cost=3,
            pool_margin=0.01,
            pool_metadata_url="https://init_location.com",
            pool_metadata_hash=cluster.gen_pool_metadata_hash(
                pool_metadata_file=pool_metadata_file
            ),
        )

        pool_data_updated = pool_data._replace(
            pool_metadata_url="https://www.updated_location.com",
            pool_metadata_hash=cluster.gen_pool_metadata_hash(
                pool_metadata_file=pool_metadata_updated_file
            ),
        )

        pool_artifacts, pool_owner = self._pool_reg_in_multiple_tx(
            cluster=cluster,
            addrs_data=addrs_data_session,
            temp_dir=temp_dir,
            temp_template=temp_template,
            pool_data=pool_data,
            request=request,
        )

        # update the pool parameters by resubmitting the pool registration certificate
        cluster.register_stake_pool(
            destination_dir=temp_dir,
            pool_data=pool_data_updated,
            pool_owner=pool_owner,
            node_vrf_vkey_file=pool_artifacts.vrf_key_pair.vkey_file,
            node_cold_key_pair=pool_artifacts.cold_key_pair_and_counter,
            deposit=0,  # no additional deposit, the pool is already registered
        )
        cluster.wait_for_new_epoch()

        # check that the pool has it's original ID after updating the metadata
        new_stake_pool_id = cluster.get_stake_pool_id(
            pool_artifacts.cold_key_pair_and_counter.vkey_file
        )
        assert (
            pool_artifacts.stake_pool_id == new_stake_pool_id
        ), "New pool ID was generated after updating the pool metadata"

        # check that the pool parameters were correctly updated on chain
        updated_pool_ledger_state = cluster.get_registered_stake_pools_ledger_state().get(
            pool_artifacts.stake_pool_id
        )
        assert not check_pool_data(updated_pool_ledger_state, pool_data_updated)

    def test_update_stake_pool_parameters(
        self, cluster_session, addrs_data_session, temp_dir, request
    ):
        """Update stake pool parameters."""
        cluster = cluster_session
        temp_template = "test_update_stake_pool"

        pool_metadata = {
            "name": "QA E2E test",
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = write_json(temp_dir / "pool_registration_metadata.json", pool_metadata)

        pool_data = PoolData(
            pool_name="poolA",
            pool_pledge=4567,
            pool_cost=3,
            pool_margin=0.01,
            pool_metadata_url="https://www.where_metadata_file_is_located.com",
            pool_metadata_hash=cluster.gen_pool_metadata_hash(
                pool_metadata_file=pool_metadata_file
            ),
        )

        pool_data_updated = pool_data._replace(pool_pledge=1, pool_cost=1_000_000, pool_margin=0.9)

        pool_artifacts, pool_owner = self._pool_reg_in_multiple_tx(
            cluster=cluster,
            addrs_data=addrs_data_session,
            temp_dir=temp_dir,
            temp_template=temp_template,
            pool_data=pool_data,
            request=request,
        )

        # update the pool parameters by resubmitting the pool registration certificate
        cluster.register_stake_pool(
            destination_dir=temp_dir,
            pool_data=pool_data_updated,
            pool_owner=pool_owner,
            node_vrf_vkey_file=pool_artifacts.vrf_key_pair.vkey_file,
            node_cold_key_pair=pool_artifacts.cold_key_pair_and_counter,
            deposit=0,  # no additional deposit, the pool is already registered
        )
        cluster.wait_for_new_epoch()

        # check that the pool has it's original ID after updating the parameters
        new_stake_pool_id = cluster.get_stake_pool_id(
            pool_artifacts.cold_key_pair_and_counter.vkey_file
        )
        assert (
            pool_artifacts.stake_pool_id == new_stake_pool_id
        ), "New pool ID was generated after updating the pool parameters"

        # check that the pool parameters were correctly updated on chain
        updated_pool_ledger_state = cluster.get_registered_stake_pools_ledger_state().get(
            pool_artifacts.stake_pool_id
        )
        assert not check_pool_data(updated_pool_ledger_state, pool_data_updated)
