"""Tests for governance.

* update proposals
* MIR certificates
"""
import logging
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


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


@pytest.mark.run(order=3)
class TestUpdateProposal:
    """Tests for update proposal."""

    @pytest.fixture
    def cluster_update_proposal(
        self,
        cluster_manager: cluster_management.ClusterManager,
    ) -> clusterlib.ClusterLib:
        return cluster_manager.get(singleton=True, cleanup=True)

    @pytest.fixture
    def payment_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_update_proposal: clusterlib.ClusterLib,
    ) -> clusterlib.AddressRecord:
        """Create new payment address."""
        cluster = cluster_update_proposal

        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addr = clusterlib_utils.create_payment_addr_records(
                f"addr_test_update_proposal_ci{cluster_manager.cluster_instance}_0",
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
    def test_update_proposal(
        self, cluster_update_proposal: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Test changing *decentralisationParam* using update proposal ."""
        clusterlib_utils.update_params(
            cluster_obj=cluster_update_proposal,
            src_addr_record=payment_addr,
            update_proposals=[
                clusterlib_utils.UpdateProposal(
                    arg="--decentralization-parameter",
                    value=0.5,
                    name="decentralization",
                )
            ],
        )


class TestMIRCerts:
    """Tests for MIR certificates."""

    @pytest.fixture
    def pool_user(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.PoolUser:
        """Create pool user."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            created_user = clusterlib_utils.create_pool_users(
                cluster_obj=cluster,
                name_template=f"test_mir_certs_ci{cluster_manager.cluster_instance}",
                no_of_addr=1,
            )[0]
            fixture_cache.value = created_user

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            created_user,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return created_user

    @pytest.fixture
    def registered_user(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        pool_user: clusterlib.PoolUser,
    ) -> clusterlib.PoolUser:
        """Register pool user's stake address."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore
            fixture_cache.value = pool_user

        temp_template = f"test_mir_certs_ci{cluster_manager.cluster_instance}"

        addr_reg_cert = cluster.gen_stake_addr_registration_cert(
            addr_name=temp_template,
            stake_vkey_file=pool_user.stake.vkey_file,
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[addr_reg_cert],
            signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
        )
        cluster.send_tx(
            src_address=pool_user.payment.address, tx_name=f"{temp_template}_reg", tx_files=tx_files
        )
        assert cluster.get_stake_addr_info(
            pool_user.stake.address
        ), f"The address {pool_user.stake.address} was not registered"

        return pool_user

    @allure.link(helpers.get_vcs_link())
    def test_transfer_to_treasury(
        self, cluster: clusterlib.ClusterLib, pool_user: clusterlib.PoolUser
    ):
        """Send funds from the reserves pot to the treasury pot.

        Expected to fail until Alonzo.
        """
        temp_template = helpers.get_func_name()
        amount = 50_000

        mir_cert = cluster.gen_mir_cert_to_treasury(transfer=amount, tx_name=temp_template)
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[pool_user.payment.skey_file, *cluster.genesis_keys.delegate_skeys],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        # fail is expected until Alonzo
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=pool_user.payment.address,
                tx_name=temp_template,
                tx_files=tx_files,
            )
        assert "MIRTransferNotCurrentlyAllowed" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_transfer_to_rewards(
        self, cluster: clusterlib.ClusterLib, pool_user: clusterlib.PoolUser
    ):
        """Send funds from the treasury pot to the reserves pot.

        Expected to fail until Alonzo.
        """
        temp_template = helpers.get_func_name()
        amount = 50_000

        mir_cert = cluster.gen_mir_cert_to_rewards(transfer=amount, tx_name=temp_template)
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[pool_user.payment.skey_file, *cluster.genesis_keys.delegate_skeys],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        # fail is expected until Alonzo
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=pool_user.payment.address,
                tx_name=temp_template,
                tx_files=tx_files,
            )
        assert "MIRTransferNotCurrentlyAllowed" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("fund_src", ("reserves", "treasury"))
    def test_pay_stake_addr_from(
        self, cluster: clusterlib.ClusterLib, registered_user: clusterlib.PoolUser, fund_src: str
    ):
        """Send funds from the reserves or treasury pot to stake address.

        * generate an MIR certificate
        * submit a TX with the MIR certificate
        * check that the expected amount was added to the stake address reward account
        """
        temp_template = helpers.get_func_name()
        amount = 50_000_000

        init_reward = cluster.get_stake_addr_info(
            registered_user.stake.address
        ).reward_account_balance
        init_balance = cluster.get_address_balance(registered_user.payment.address)

        mir_cert = cluster.gen_mir_cert_stake_addr(
            stake_addr=registered_user.stake.address,
            reward=amount,
            tx_name=temp_template,
            use_treasury=fund_src == "treasury",
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[
                registered_user.payment.skey_file,
                *cluster.genesis_keys.delegate_skeys,
            ],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        tx_raw_output = cluster.send_tx(
            src_address=registered_user.payment.address,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        assert (
            cluster.get_address_balance(registered_user.payment.address)
            == init_balance - tx_raw_output.fee
        ), f"Incorrect balance for source address `{registered_user.payment.address}`"

        cluster.wait_for_new_epoch()

        assert (
            cluster.get_stake_addr_info(registered_user.stake.address).reward_account_balance
            == init_reward + amount
        ), f"Incorrect reward balance for stake address `{registered_user.stake.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            if fund_src == "treasury":
                assert tx_db_record.treasury[0].amount == amount, (
                    "Incorrect amount transferred from treasury "
                    f"({tx_db_record.treasury[0].amount} != {amount})"
                )
            else:
                assert tx_db_record.reserve[0].amount == amount, (
                    "Incorrect amount transferred from reserve "
                    f"({tx_db_record.reserve[0].amount} != {amount})"
                )
