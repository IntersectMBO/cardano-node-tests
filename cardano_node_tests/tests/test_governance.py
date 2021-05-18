"""Tests for governance.

* update proposals
* MIR certificates
"""
import logging
from pathlib import Path
from typing import List

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
                f"addr_test_update_proposal_ci{cluster_manager.cluster_instance_num}_0",
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
        self,
        cluster_update_proposal: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
    ):
        """Test changing protocol parameters using update proposal ."""
        clusterlib_utils.update_params(
            cluster_obj=cluster_update_proposal,
            src_addr_record=payment_addr,
            update_proposals=[
                clusterlib_utils.UpdateProposal(
                    arg="--min-fee-linear",
                    value=45,
                    name="txFeePerByte",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--min-utxo-value",
                    value=2,
                    name="minUTxOValue",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--pool-reg-deposit",
                    value=400000000,
                    name="stakePoolDeposit",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--decentralization-parameter",
                    value=0.5,
                    name="decentralization",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--pool-retirement-epoch-boundary",
                    value=19,
                    name="poolRetireMaxEpoch",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--number-of-pools",
                    value=9,
                    name="stakePoolTargetNum",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--max-block-body-size",
                    value=65544,
                    name="maxBlockBodySize",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--max-tx-size",
                    value=16392,
                    name="maxTxSize",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--min-pool-cost",
                    value=1,
                    name="minPoolCost",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--max-block-header-size",
                    value=1200,
                    name="maxBlockHeaderSize",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--min-fee-constant",
                    value=155380,
                    name="txFeeFixed",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--key-reg-deposit-amt",
                    value=300000,
                    name="stakeAddressDeposit",
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--pool-influence",
                    value=0.4,
                    name="poolPledgeInfluence",
                ),
            ],
        )


class TestMIRCerts:
    """Tests for MIR certificates."""

    RESERVES = "reserves"
    TREASURY = "treasury"

    @pytest.fixture
    def cluster_pots(
        self,
        cluster_manager: cluster_management.ClusterManager,
    ) -> clusterlib.ClusterLib:
        return cluster_manager.get(
            lock_resources=[
                cluster_management.Resources.RESERVES,
                cluster_management.Resources.TREASURY,
            ]
        )

    @pytest.fixture
    def pool_users(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_pots: clusterlib.ClusterLib,
    ) -> List[clusterlib.PoolUser]:
        """Create pool user."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            created_users = clusterlib_utils.create_pool_users(
                cluster_obj=cluster_pots,
                name_template=f"test_mir_certs_ci{cluster_manager.cluster_instance_num}",
                no_of_addr=2,
            )
            fixture_cache.value = created_users

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *created_users,
            cluster_obj=cluster_pots,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return created_users

    @pytest.fixture
    def registered_user(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_pots: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ) -> clusterlib.PoolUser:
        """Register pool user's stake address."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore
            fixture_cache.value = pool_users[1]

        temp_template = f"test_mir_certs_ci{cluster_manager.cluster_instance_num}"
        pool_user = pool_users[1]
        clusterlib_utils.register_stake_address(
            cluster_obj=cluster_pots, pool_user=pool_users[1], name_template=temp_template
        )
        return pool_user

    @allure.link(helpers.get_vcs_link())
    def test_transfer_to_treasury(
        self, cluster_pots: clusterlib.ClusterLib, pool_users: List[clusterlib.PoolUser]
    ):
        """Send funds from the reserves pot to the treasury pot.

        Expected to fail until Alonzo.
        """
        temp_template = helpers.get_func_name()
        cluster = cluster_pots
        pool_user = pool_users[0]
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
        self, cluster_pots: clusterlib.ClusterLib, pool_users: List[clusterlib.PoolUser]
    ):
        """Send funds from the treasury pot to the reserves pot.

        Expected to fail until Alonzo.
        """
        temp_template = helpers.get_func_name()
        cluster = cluster_pots
        pool_user = pool_users[0]
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
    @pytest.mark.parametrize("fund_src", (RESERVES, TREASURY))
    def test_pay_stake_addr_from(
        self,
        cluster_pots: clusterlib.ClusterLib,
        registered_user: clusterlib.PoolUser,
        fund_src: str,
    ):
        """Send funds from the reserves or treasury pot to stake address.

        * generate an MIR certificate
        * submit a TX with the MIR certificate
        * check that the expected amount was added to the stake address reward account
        """
        temp_template = helpers.get_func_name()
        cluster = cluster_pots
        amount = 50_000_000

        init_reward = cluster.get_stake_addr_info(
            registered_user.stake.address
        ).reward_account_balance
        init_balance = cluster.get_address_balance(registered_user.payment.address)

        mir_cert = cluster.gen_mir_cert_stake_addr(
            stake_addr=registered_user.stake.address,
            reward=amount,
            tx_name=temp_template,
            use_treasury=fund_src == self.TREASURY,
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
            if fund_src == self.TREASURY:
                assert tx_db_record.treasury[0].amount == amount, (
                    "Incorrect amount transferred from treasury "
                    f"({tx_db_record.treasury[0].amount} != {amount})"
                )
            else:
                assert tx_db_record.reserve[0].amount == amount, (
                    "Incorrect amount transferred from reserve "
                    f"({tx_db_record.reserve[0].amount} != {amount})"
                )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("fund_src", (RESERVES, TREASURY))
    def test_exceed_pay_stake_addr_from(
        self,
        cluster_pots: clusterlib.ClusterLib,
        registered_user: clusterlib.PoolUser,
        fund_src: str,
    ):
        """Try to send more funds than available from the reserves or treasury pot to stake address.

        Expect failure.

        * generate an MIR certificate
        * submit a TX with the MIR certificate
        * check that submitting the transaction fails with an expected error
        """
        temp_template = helpers.get_func_name()
        cluster = cluster_pots
        amount = 30_000_000_000_000_000

        init_balance = cluster.get_address_balance(registered_user.payment.address)

        mir_cert = cluster.gen_mir_cert_stake_addr(
            stake_addr=registered_user.stake.address,
            reward=amount,
            tx_name=temp_template,
            use_treasury=fund_src == self.TREASURY,
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

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=registered_user.payment.address,
                tx_name=temp_template,
                tx_files=tx_files,
            )
        assert "InsufficientForInstantaneousRewardsDELEG" in str(excinfo.value)

        assert (
            cluster.get_address_balance(registered_user.payment.address) == init_balance
        ), f"Incorrect balance for source address `{registered_user.payment.address}`"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("fund_src", (RESERVES, TREASURY))
    def test_pay_unregistered_stake_addr_from(
        self,
        cluster_pots: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        fund_src: str,
    ):
        """Send funds from the reserves or treasury pot to unregistered stake address.

        * generate an MIR certificate
        * submit a TX with the MIR certificate
        * check that the amount was NOT added to the stake address reward account
        """
        temp_template = helpers.get_func_name()
        cluster = cluster_pots
        pool_user = pool_users[0]

        if fund_src == self.TREASURY:
            amount = 1_500_000_000_000
        else:
            amount = 50_000_000_000_000

        init_balance = cluster.get_address_balance(pool_user.payment.address)

        mir_cert = cluster.gen_mir_cert_stake_addr(
            stake_addr=pool_user.stake.address,
            reward=amount,
            tx_name=temp_template,
            use_treasury=fund_src == self.TREASURY,
        )
        tx_files = clusterlib.TxFiles(
            certificate_files=[mir_cert],
            signing_key_files=[
                pool_user.payment.skey_file,
                *cluster.genesis_keys.delegate_skeys,
            ],
        )

        # send the transaction at the beginning of an epoch
        if cluster.time_from_epoch_start() > (cluster.epoch_length_sec // 6):
            cluster.wait_for_new_epoch()

        tx_raw_output = cluster.send_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            tx_files=tx_files,
        )

        tx_epoch = cluster.get_epoch()

        assert (
            cluster.get_address_balance(pool_user.payment.address)
            == init_balance - tx_raw_output.fee
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            if fund_src == self.TREASURY:
                assert tx_db_record.treasury[0].amount == amount, (
                    "Incorrect amount transferred from treasury "
                    f"({tx_db_record.treasury[0].amount} != {amount})"
                )
            else:
                assert tx_db_record.reserve[0].amount == amount, (
                    "Incorrect amount transferred from reserve "
                    f"({tx_db_record.reserve[0].amount} != {amount})"
                )

        cluster.wait_for_new_epoch()

        assert not cluster.get_stake_addr_info(
            pool_user.stake.address
        ).reward_account_balance, (
            f"Reward was added for unregistered stake address `{pool_user.stake.address}`"
        )

        if tx_db_record:
            # check that the amount was not transferred out of the pot
            pots_records = list(dbsync_utils.query_ada_pots(epoch_from=tx_epoch))
            if fund_src == self.TREASURY:
                # normally `treasury[-1]` < `treasury[0]`
                assert abs(pots_records[-1].treasury - pots_records[0].treasury) < amount
            else:
                # normally `reserves[0]` < `reserves[-1]`
                assert abs(pots_records[0].reserves - pots_records[-1].reserves) < amount
