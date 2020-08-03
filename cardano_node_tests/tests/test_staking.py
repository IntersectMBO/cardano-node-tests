import logging
from typing import List
from typing import Tuple

import pytest
from _pytest.fixtures import FixtureRequest

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import UnpackableSequence

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory):
    """Create a temporary dir and change to it."""
    tmp_path = tmp_path_factory.mktemp("test_staking")
    with helpers.change_cwd(tmp_path):
        yield tmp_path


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


class TestDelegateAddr:
    def test_delegate_using_addr(self, cluster_session, addrs_data_session, request):
        """Submit registration certificate and delegate to pool using stake address."""
        cluster = cluster_session
        temp_template = "test_delegate_using_addr"

        # create key pairs and addresses
        stake_addr = helpers.create_stake_addrs(f"addr0_{temp_template}", cluster_obj=cluster)[0]
        payment_addr = helpers.create_payment_addrs(
            f"addr0_{temp_template}", cluster_obj=cluster, stake_vkey_file=stake_addr.vkey_file,
        )[0]
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=stake_addr.vkey_file
        )

        # fund source address
        helpers.fund_from_faucet(
            payment_addr,
            cluster_obj=cluster,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file],
            signing_key_files=[payment_addr.skey_file, stake_addr.skey_file],
        )

        src_address = payment_addr.address
        src_init_balance = cluster.get_address_balance(src_address)

        # register stake address
        tx_raw_data = cluster.send_tx(src_address=src_address, tx_files=tx_files)
        cluster.wait_for_new_tip(new_blocks=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_data.fee - cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{src_address}`"
        src_register_balance = cluster.get_address_balance(src_address)

        first_pool_id_in_stake_dist = list(helpers.wait_for_stake_distribution(cluster))[0]
        delegation_fee = cluster.calculate_tx_fee(src_address=src_address, tx_files=tx_files)

        # delegate the stake address to pool
        # TODO: remove try..catch once the functionality is implemented
        try:
            cluster.delegate_stake_addr(
                stake_addr_skey=stake_addr.skey_file,
                pool_id=first_pool_id_in_stake_dist,
                delegation_fee=delegation_fee,
            )
        except clusterlib.CLIError as excinfo:
            if "command not implemented yet" in str(excinfo):
                pytest.xfail(
                    "Delegating stake address using `cardano-cli shelley stake-address delegate` "
                    "not implemented yet."
                )
        cluster.wait_for_new_tip(new_blocks=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(src_address) == src_register_balance - delegation_fee
        ), f"Incorrect balance for source address `{src_address}`"

        helpers.wait_for_stake_distribution(cluster)

        # check that the stake address was delegated
        stake_addr_info = cluster.get_stake_addr_info(stake_addr.address)
        assert (
            stake_addr_info.delegation is not None
        ), f"Stake address was not delegated yet: {stake_addr_info}"

        assert (
            first_pool_id_in_stake_dist == stake_addr_info.delegation
        ), "Stake address delegated to wrong pool"

    def test_delegate_using_cert(self, cluster_session, addrs_data_session, request):
        """Submit registration certificate and delegate to pool using certificate."""
        cluster = cluster_session
        temp_template = "test_delegate_using_cert"
        node_cold = addrs_data_session["node-pool1"]["cold_key_pair"]

        # create key pairs and addresses
        stake_addr = helpers.create_stake_addrs(f"addr0_{temp_template}", cluster_obj=cluster)[0]
        payment_addr = helpers.create_payment_addrs(
            f"addr0_{temp_template}", cluster_obj=cluster, stake_vkey_file=stake_addr.vkey_file,
        )[0]

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=stake_addr.vkey_file
        )
        # create stake address delegation cert
        stake_addr_deleg_cert_file = cluster.gen_stake_addr_delegation_cert(
            addr_name=f"addr0_{temp_template}",
            stake_vkey_file=stake_addr.vkey_file,
            node_cold_vkey_file=node_cold.vkey_file,
        )

        # fund source address
        helpers.fund_from_faucet(
            payment_addr,
            cluster_obj=cluster,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_reg_cert_file, stake_addr_deleg_cert_file],
            signing_key_files=[payment_addr.skey_file, stake_addr.skey_file],
        )

        src_address = payment_addr.address
        src_init_balance = cluster.get_address_balance(src_address)

        # register stake address and delegate it to pool
        tx_raw_data = cluster.send_tx(src_address=src_address, tx_files=tx_files)
        cluster.wait_for_new_tip(new_blocks=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_data.fee - cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        helpers.wait_for_stake_distribution(cluster)

        # check that the stake address was delegated
        stake_addr_info = cluster.get_stake_addr_info(stake_addr.address)
        assert (
            stake_addr_info.delegation is not None
        ), f"Stake address was not delegated yet: {stake_addr_info}"

        stake_pool_id = cluster.get_stake_pool_id(node_cold.vkey_file)
        assert stake_pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"


class TestStakePool:
    def _check_staking(
        self,
        *stake_addrs: UnpackableSequence,
        cluster_obj: clusterlib.ClusterLib,
        stake_pool_id: str,
        pool_data: clusterlib.PoolData,
    ):
        """Check that pool and staking were correctly setup."""
        LOGGER.info("Waiting up to 2 epochs for stake pool to be registered.")
        helpers.wait_for(
            lambda: stake_pool_id in cluster_obj.get_stake_distribution(),
            delay=10,
            num_sec=2 * cluster_obj.epoch_length,
            message="register stake pool",
        )

        # check that the pool was correctly registered on chain
        pool_ledger_state = cluster_obj.get_registered_stake_pools_ledger_state().get(stake_pool_id)
        assert pool_ledger_state, (
            "The newly created stake pool id is not shown inside the available stake pools;\n"
            f"Pool ID: {stake_pool_id} vs Existing IDs: "
            f"{list(cluster_obj.get_registered_stake_pools_ledger_state())}"
        )
        assert not helpers.check_pool_data(pool_ledger_state, pool_data)

        for addr_rec in stake_addrs:
            stake_addr_info = cluster_obj.get_stake_addr_info(addr_rec.address)

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
                stake_addr_info.addr_hash[2:]
                in pool_ledger_state["owners"]
            ), "'owner' value is different than expected"

    def _create_register_pool_delegate_stake_tx(
        self,
        cluster_obj: clusterlib.ClusterLib,
        addrs_data: dict,
        temp_template: str,
        pool_data: clusterlib.PoolData,
        request: FixtureRequest,
        no_of_addr: int = 1,
    ):
        """Create and register a stake pool, delegate stake address - all in single TX.

        Common functionality for tests.
        """
        # create node VRF key pair
        node_vrf = cluster_obj.gen_vrf_key_pair(node_name=pool_data.pool_name)
        # create node cold key pair and counter
        node_cold = cluster_obj.gen_cold_key_pair_and_counter(node_name=pool_data.pool_name)

        stake_addrs = []
        payment_addrs = []
        stake_addr_reg_cert_files = []
        stake_addr_deleg_cert_files = []
        for i in range(no_of_addr):
            # create key pairs and addresses
            stake_addr = helpers.create_stake_addrs(
                f"addr{i}_{temp_template}", cluster_obj=cluster_obj
            )[0]
            payment_addr = helpers.create_payment_addrs(
                f"addr{i}_{temp_template}",
                cluster_obj=cluster_obj,
                stake_vkey_file=stake_addr.vkey_file,
            )[0]
            # create stake address registration cert
            stake_addr_reg_cert_file = cluster_obj.gen_stake_addr_registration_cert(
                addr_name=f"addr{i}_{temp_template}", stake_vkey_file=stake_addr.vkey_file,
            )
            # create stake address delegation cert
            stake_addr_deleg_cert_file = cluster_obj.gen_stake_addr_delegation_cert(
                addr_name=f"addr{i}_{temp_template}",
                stake_vkey_file=stake_addr.vkey_file,
                node_cold_vkey_file=node_cold.vkey_file,
            )
            stake_addrs.append(stake_addr)
            payment_addrs.append(payment_addr)
            stake_addr_reg_cert_files.append(stake_addr_reg_cert_file)
            stake_addr_deleg_cert_files.append(stake_addr_deleg_cert_file)

        # create stake pool registration cert
        pool_reg_cert_file = cluster_obj.gen_pool_registration_cert(
            pool_data=pool_data,
            node_vrf_vkey_file=node_vrf.vkey_file,
            node_cold_vkey_file=node_cold.vkey_file,
            owner_stake_vkey_files=[r.vkey_file for r in stake_addrs],
        )

        # fund source address
        helpers.fund_from_faucet(
            *payment_addrs,
            cluster_obj=cluster_obj,
            faucet_data=addrs_data["user1"],
            amount=900_000_000,
            request=request,
        )

        tx_files = clusterlib.TxFiles(
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

        src_address = payment_addrs[0].address
        src_init_balance = cluster_obj.get_address_balance(src_address)

        # register and delegate stake address, create and register pool
        tx_raw_data = cluster_obj.send_tx(src_address=src_address, tx_files=tx_files)
        cluster_obj.wait_for_new_tip(new_blocks=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster_obj.get_address_balance(src_address)
            == src_init_balance
            - tx_raw_data.fee
            - no_of_addr * cluster_obj.get_key_deposit()
            - cluster_obj.get_pool_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        # check that pool and staking were correctly setup
        stake_pool_id = cluster_obj.get_stake_pool_id(node_cold.vkey_file)
        self._check_staking(
            *stake_addrs, cluster_obj=cluster_obj, stake_pool_id=stake_pool_id, pool_data=pool_data,
        )

    def _create_register_pool_tx_delegate_stake_tx(
        self,
        cluster_obj: clusterlib.ClusterLib,
        addrs_data: dict,
        temp_template: str,
        pool_data: clusterlib.PoolData,
        request: FixtureRequest,
        no_of_addr: int = 1,
    ) -> Tuple[clusterlib.PoolCreationArtifacts, List[clusterlib.PoolOwner]]:
        """Create and register a stake pool - first TX; delegate stake address - second TX.

        Common functionality for tests.
        """
        stake_addrs = []
        payment_addrs = []
        stake_addr_reg_cert_files = []
        pool_owners = []
        for i in range(no_of_addr):
            # create key pairs and addresses
            stake_addr = helpers.create_stake_addrs(
                f"addr{i}_{temp_template}", cluster_obj=cluster_obj
            )[0]
            payment_addr = helpers.create_payment_addrs(
                f"addr{i}_{temp_template}",
                cluster_obj=cluster_obj,
                stake_vkey_file=stake_addr.vkey_file,
            )[0]
            # create stake address registration cert
            stake_addr_reg_cert_file = cluster_obj.gen_stake_addr_registration_cert(
                addr_name=f"addr{i}_{temp_template}", stake_vkey_file=stake_addr.vkey_file,
            )
            # create pool owner struct
            pool_owner = clusterlib.PoolOwner(
                addr=payment_addr.address,
                stake_addr=stake_addr.address,
                addr_key_pair=clusterlib.KeyPair(
                    vkey_file=payment_addr.vkey_file, skey_file=payment_addr.skey_file
                ),
                stake_key_pair=clusterlib.KeyPair(
                    vkey_file=stake_addr.vkey_file, skey_file=stake_addr.skey_file
                ),
            )
            stake_addrs.append(stake_addr)
            payment_addrs.append(payment_addr)
            stake_addr_reg_cert_files.append(stake_addr_reg_cert_file)
            pool_owners.append(pool_owner)

        # fund source address
        helpers.fund_from_faucet(
            *payment_addrs,
            cluster_obj=cluster_obj,
            faucet_data=addrs_data["user1"],
            amount=1_000_000_000,
            request=request,
        )

        # create and register pool
        pool_artifacts = cluster_obj.create_stake_pool(pool_data=pool_data, pool_owners=pool_owners)

        # create stake address delegation cert
        stake_addr_deleg_cert_files = [
            cluster_obj.gen_stake_addr_delegation_cert(
                addr_name=f"addr{i}_{temp_template}",
                stake_vkey_file=stake_addrs[i].vkey_file,
                node_cold_vkey_file=pool_artifacts.cold_key_pair_and_counter.vkey_file,
            )
            for i in range(no_of_addr)
        ]

        tx_files = clusterlib.TxFiles(
            certificate_files=[*stake_addr_reg_cert_files, *stake_addr_deleg_cert_files],
            signing_key_files=[
                *[r.skey_file for r in payment_addrs],
                *[r.skey_file for r in stake_addrs],
                pool_artifacts.cold_key_pair_and_counter.skey_file,
            ],
        )

        src_address = payment_addrs[0].address
        src_init_balance = cluster_obj.get_address_balance(src_address)

        # register and delegate stake address
        tx_raw_data = cluster_obj.send_tx(src_address=src_address, tx_files=tx_files)
        cluster_obj.wait_for_new_tip(new_blocks=2)

        # check that the balance for source address was correctly updated
        assert (
            cluster_obj.get_address_balance(src_address)
            == src_init_balance - tx_raw_data.fee - no_of_addr * cluster_obj.get_key_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        # check that pool and staking were correctly setup
        self._check_staking(
            *stake_addrs,
            cluster_obj=cluster_obj,
            stake_pool_id=pool_artifacts.stake_pool_id,
            pool_data=pool_data,
        )

        return pool_artifacts, pool_owners

    @pytest.mark.parametrize("no_of_addr", [1, 3])
    def test_stake_pool_metadata(
        self, cluster_session, addrs_data_session, temp_dir, no_of_addr, request
    ):
        """Create and register a stake pool with metadata."""
        cluster = cluster_session
        temp_template = f"test_stake_pool_metadata_{no_of_addr}owners"

        pool_metadata = {
            "name": "QA E2E test",
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = helpers.write_json(
            temp_dir / f"poolY_{no_of_addr}_registration_metadata.json", pool_metadata
        )

        pool_data = clusterlib.PoolData(
            pool_name=f"poolY_{no_of_addr}",
            pool_pledge=1000,
            pool_cost=15,
            pool_margin=0.2,
            pool_metadata_url="https://www.where_metadata_file_is_located.com",
            pool_metadata_hash=cluster.gen_pool_metadata_hash(
                pool_metadata_file=pool_metadata_file
            ),
        )

        self._create_register_pool_delegate_stake_tx(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            pool_data=pool_data,
            request=request,
            no_of_addr=no_of_addr,
        )

    @pytest.mark.parametrize("no_of_addr", [1, 3])
    def test_stake_pool(self, cluster_session, addrs_data_session, no_of_addr, request):
        """Create and register a stake pool."""
        cluster = cluster_session
        temp_template = f"test_stake_pool_{no_of_addr}owners"

        pool_data = clusterlib.PoolData(
            pool_name=f"poolX_{no_of_addr}",
            pool_pledge=12345,
            pool_cost=123456789,
            pool_margin=0.123,
        )

        self._create_register_pool_delegate_stake_tx(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            pool_data=pool_data,
            request=request,
            no_of_addr=no_of_addr,
        )

    @pytest.mark.parametrize("no_of_addr", [1, 3])
    def test_deregister_stake_pool(
        self, cluster_session, addrs_data_session, temp_dir, no_of_addr, request
    ):
        """Deregister stake pool."""
        cluster = cluster_session
        temp_template = f"test_deregister_stake_pool_{no_of_addr}owners"

        pool_metadata = {
            "name": "QA E2E test",
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = helpers.write_json(
            temp_dir / f"poolZ_{no_of_addr}_registration_metadata.json", pool_metadata
        )

        pool_data = clusterlib.PoolData(
            pool_name=f"poolZ_{no_of_addr}",
            pool_pledge=222,
            pool_cost=123,
            pool_margin=0.512,
            pool_metadata_url="https://www.where_metadata_file_is_located.com",
            pool_metadata_hash=cluster.gen_pool_metadata_hash(
                pool_metadata_file=pool_metadata_file
            ),
        )

        pool_artifacts, pool_owners = self._create_register_pool_tx_delegate_stake_tx(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            pool_data=pool_data,
            request=request,
            no_of_addr=no_of_addr,
        )

        pool_owner = pool_owners[0]
        src_register_balance = cluster.get_address_balance(pool_owner.addr)

        # deregister stake pool
        __, tx_raw_data = cluster.deregister_stake_pool(
            pool_owners=pool_owners,
            node_cold_key_pair=pool_artifacts.cold_key_pair_and_counter,
            epoch=cluster.get_last_block_epoch() + 1,
            pool_name=pool_data.pool_name,
        )

        LOGGER.info("Waiting up to 3 epochs for stake pool to be deregistered.")
        helpers.wait_for(
            lambda: pool_artifacts.stake_pool_id not in cluster.get_stake_distribution(),
            delay=10,
            num_sec=3 * cluster.epoch_length,
            message="deregister stake pool",
        )

        # check that the balance for source address was correctly updated
        # TODO: what about pool deposit?
        assert src_register_balance - tx_raw_data.fee == cluster.get_address_balance(
            pool_owner.addr
        )

        # check that the pool was correctly de-registered on chain
        pool_ledger_state = cluster.get_registered_stake_pools_ledger_state().get(
            pool_artifacts.stake_pool_id
        )
        assert not pool_ledger_state, (
            "The de-registered stake pool id is still shown inside the available stake pools;\n"
            f"Pool ID: {pool_artifacts.stake_pool_id} vs Existing IDs: "
            f"{list(cluster.get_registered_stake_pools_ledger_state())}"
        )

        for owner_rec in pool_owners:
            stake_addr_info = cluster.get_stake_addr_info(owner_rec.stake_addr)

            # check that the stake address was delegated
            assert (
                stake_addr_info.delegation is None
            ), f"Stake address is still delegated: {stake_addr_info}"

    @pytest.mark.parametrize("no_of_addr", [1, 2])
    def test_update_stake_pool_metadata(
        self, cluster_session, addrs_data_session, temp_dir, no_of_addr, request
    ):
        """Update stake pool metadata."""
        cluster = cluster_session
        temp_template = f"test_update_stake_pool_metadata_{no_of_addr}owners"

        pool_metadata = {
            "name": "QA E2E test",
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = helpers.write_json(
            temp_dir / f"poolA_{no_of_addr}_registration_metadata.json", pool_metadata
        )

        pool_metadata_updated = {
            "name": "QA_test_pool",
            "description": "pool description update",
            "ticker": "QA22",
            "homepage": "www.qa22.com",
        }
        pool_metadata_updated_file = helpers.write_json(
            temp_dir / f"poolA_{no_of_addr}_registration_metadata_updated.json",
            pool_metadata_updated,
        )

        pool_data = clusterlib.PoolData(
            pool_name=f"poolA_{no_of_addr}",
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

        pool_artifacts, pool_owners = self._create_register_pool_tx_delegate_stake_tx(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            pool_data=pool_data,
            request=request,
            no_of_addr=no_of_addr,
        )

        # update the pool parameters by resubmitting the pool registration certificate
        cluster.register_stake_pool(
            pool_data=pool_data_updated,
            pool_owners=pool_owners,
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
        assert not helpers.check_pool_data(updated_pool_ledger_state, pool_data_updated)

    @pytest.mark.parametrize("no_of_addr", [1, 2])
    def test_update_stake_pool_parameters(
        self, cluster_session, addrs_data_session, temp_dir, no_of_addr, request
    ):
        """Update stake pool parameters."""
        cluster = cluster_session
        temp_template = f"test_update_stake_pool_{no_of_addr}owners"

        pool_metadata = {
            "name": "QA E2E test",
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = helpers.write_json(
            temp_dir / f"poolB_{no_of_addr}_registration_metadata.json", pool_metadata
        )

        pool_data = clusterlib.PoolData(
            pool_name=f"poolB_{no_of_addr}",
            pool_pledge=4567,
            pool_cost=3,
            pool_margin=0.01,
            pool_metadata_url="https://www.where_metadata_file_is_located.com",
            pool_metadata_hash=cluster.gen_pool_metadata_hash(
                pool_metadata_file=pool_metadata_file
            ),
        )

        pool_data_updated = pool_data._replace(pool_pledge=1, pool_cost=1_000_000, pool_margin=0.9)

        pool_artifacts, pool_owners = self._create_register_pool_tx_delegate_stake_tx(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            pool_data=pool_data,
            request=request,
            no_of_addr=no_of_addr,
        )

        # update the pool parameters by resubmitting the pool registration certificate
        cluster.register_stake_pool(
            pool_data=pool_data_updated,
            pool_owners=pool_owners,
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
        assert not helpers.check_pool_data(updated_pool_ledger_state, pool_data_updated)
