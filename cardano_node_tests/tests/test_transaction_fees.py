"""Tests for fees of various kinds of transactions."""
import itertools
import logging
from pathlib import Path
from typing import List
from typing import Tuple

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
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


@pytest.mark.testnets
class TestFee:
    """General fees tests."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                f"addr_test_fee_ci{cluster_manager.cluster_instance_num}_0",
                f"addr_test_fee_ci{cluster_manager.cluster_instance_num}_1",
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return addrs

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(fee=st.integers(max_value=-1))
    @helpers.hypothesis_settings()
    def test_negative_fee(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fee: int,
    ):
        """Try to send a transaction with negative fee (property-based test).

        Expect failure.
        """
        temp_template = f"{helpers.get_func_name()}_{helpers.get_timestamped_rand_str()}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=10)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=temp_template,
                tx_files=tx_files,
                fee=fee,
            )
        assert "option --fee: cannot parse value" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("fee_change", (0, 1.1, 1.5, 2))
    def test_smaller_fee(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fee_change: float,
    ):
        """Try to send a transaction with smaller-than-expected fee.

        Expect failure.
        """
        temp_template = f"{helpers.get_func_name()}_{fee_change}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=10)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        fee = 0.0
        if fee_change:
            fee = (
                cluster.calculate_tx_fee(
                    src_address=src_address,
                    tx_name=temp_template,
                    txouts=destinations,
                    tx_files=tx_files,
                )
                / fee_change
            )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=temp_template,
                tx_files=tx_files,
                fee=int(fee),
            )
        assert "FeeTooSmallUTxO" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("fee_add", (0, 1000, 100_000, 1_000_000))
    def test_expected_or_higher_fee(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fee_add: int,
    ):
        """Send a transaction with fee that is same or higher than expected."""
        temp_template = f"{helpers.get_func_name()}_{fee_add}"
        amount = 100

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        fee = (
            cluster.calculate_tx_fee(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
            )
            + fee_add
        )

        tx_raw_output = cluster.send_funds(
            src_address=src_address,
            destinations=destinations,
            tx_name=temp_template,
            tx_files=tx_files,
            fee=fee,
        )

        assert tx_raw_output.fee == fee, "The actual fee doesn't match the specified fee"

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"


class TestExpectedFees:
    """Test expected fees."""

    @pytest.fixture
    def pool_users(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.PoolUser]:
        """Create pool users."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            created_users = clusterlib_utils.create_pool_users(
                cluster_obj=cluster,
                name_template=f"test_expected_fees_ci{cluster_manager.cluster_instance_num}",
                no_of_addr=201,
            )
            fixture_cache.value = created_users

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *created_users[:10],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return created_users

    def _create_pool_certificates(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_owners: List[clusterlib.PoolUser],
        temp_template: str,
        pool_data: clusterlib.PoolData,
    ) -> Tuple[str, clusterlib.TxFiles]:
        """Create certificates for registering a stake pool, delegating stake address."""
        # create node VRF key pair
        node_vrf = cluster_obj.gen_vrf_key_pair(node_name=pool_data.pool_name)
        # create node cold key pair and counter
        node_cold = cluster_obj.gen_cold_key_pair_and_counter(node_name=pool_data.pool_name)

        # create stake address registration certs
        stake_addr_reg_cert_files = [
            cluster_obj.gen_stake_addr_registration_cert(
                addr_name=f"{temp_template}_addr{i}", stake_vkey_file=p.stake.vkey_file
            )
            for i, p in enumerate(pool_owners)
        ]

        # create stake address delegation cert
        stake_addr_deleg_cert_files = [
            cluster_obj.gen_stake_addr_delegation_cert(
                addr_name=f"{temp_template}_addr{i}",
                stake_vkey_file=p.stake.vkey_file,
                cold_vkey_file=node_cold.vkey_file,
            )
            for i, p in enumerate(pool_owners)
        ]

        # create stake pool registration cert
        pool_reg_cert_file = cluster_obj.gen_pool_registration_cert(
            pool_data=pool_data,
            vrf_vkey_file=node_vrf.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
            owner_stake_vkey_files=[p.stake.vkey_file for p in pool_owners],
        )

        src_address = pool_owners[0].payment.address

        # register and delegate stake address, create and register pool
        tx_files = clusterlib.TxFiles(
            certificate_files=[
                pool_reg_cert_file,
                *stake_addr_reg_cert_files,
                *stake_addr_deleg_cert_files,
            ],
            signing_key_files=[
                *[p.payment.skey_file for p in pool_owners],
                *[p.stake.skey_file for p in pool_owners],
                node_cold.skey_file,
            ],
        )

        return src_address, tx_files

    def _from_to_transactions(
        self,
        cluster_obj: clusterlib.ClusterLib,
        tx_name: str,
        pool_users: List[clusterlib.PoolUser],
        from_num: int,
        to_num: int,
        amount_expected: Tuple[int, int],
    ):
        """Check fees for 1 tx from `from_num` payment addresses to `to_num` payment addresses."""
        amount, expected_fee = amount_expected

        src_address = pool_users[0].payment.address
        # addr1..addr<from_num+1>
        from_addr_recs = [p.payment for p in pool_users[1 : from_num + 1]]
        # addr<from_num+1>..addr<from_num+to_num+1>
        dst_addresses = [
            pool_users[i].payment.address for i in range(from_num + 1, from_num + to_num + 1)
        ]

        # create TX data
        _txins = [cluster_obj.get_utxo(r.address) for r in from_addr_recs]
        # flatten the list of lists that is _txins
        txins = list(itertools.chain.from_iterable(_txins))
        txouts = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]
        tx_files = clusterlib.TxFiles(signing_key_files=[r.skey_file for r in from_addr_recs])

        # calculate TX fee
        tx_fee = cluster_obj.calculate_tx_fee(
            src_address=src_address, tx_name=tx_name, txins=txins, txouts=txouts, tx_files=tx_files
        )
        assert helpers.is_in_interval(
            tx_fee, expected_fee
        ), "Expected fee doesn't match the actual fee"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_fee", [(1, 197753), (3, 234009), (5, 270265), (10, 360905)])
    def test_pool_registration_fees(
        self,
        cluster: clusterlib.ClusterLib,
        temp_dir: Path,
        pool_users: List[clusterlib.PoolUser],
        addr_fee: Tuple[int, int],
    ):
        """Test pool registration fees."""
        no_of_addr, expected_fee = addr_fee
        rand_str = clusterlib.get_rand_str(4)
        temp_template = f"{helpers.get_func_name()}_{rand_str}_{no_of_addr}"

        pool_name = f"pool_{rand_str}"
        pool_metadata = {
            "name": pool_name,
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = helpers.write_json(
            temp_dir / f"{pool_name}_registration_metadata.json", pool_metadata
        )

        pool_data = clusterlib.PoolData(
            pool_name=pool_name,
            pool_pledge=1000,
            pool_cost=15,
            pool_margin=0.2,
            pool_metadata_url="https://www.where_metadata_file_is_located.com",
            pool_metadata_hash=cluster.gen_pool_metadata_hash(pool_metadata_file),
        )

        # create pool owners
        selected_owners = pool_users[:no_of_addr]

        # create certificates
        src_address, tx_files = self._create_pool_certificates(
            cluster_obj=cluster,
            pool_owners=selected_owners,
            temp_template=temp_template,
            pool_data=pool_data,
        )

        # calculate TX fee
        tx_fee = cluster.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, tx_files=tx_files
        )
        assert helpers.is_in_interval(
            tx_fee, expected_fee
        ), "Expected fee doesn't match the actual fee"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_fee", [(1, 185213), (3, 210205), (5, 235197), (10, 297677)])
    def test_pool_deregistration_fees(
        self,
        cluster: clusterlib.ClusterLib,
        temp_dir: Path,
        pool_users: List[clusterlib.PoolUser],
        addr_fee: Tuple[int, int],
    ):
        """Test pool deregistration fees."""
        no_of_addr, expected_fee = addr_fee
        rand_str = clusterlib.get_rand_str(4)
        temp_template = f"{helpers.get_func_name()}_{rand_str}_{no_of_addr}"
        src_address = pool_users[0].payment.address

        pool_name = f"pool_{rand_str}"
        pool_metadata = {
            "name": pool_name,
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = helpers.write_json(
            temp_dir / f"{pool_name}_registration_metadata.json", pool_metadata
        )

        pool_data = clusterlib.PoolData(
            pool_name=pool_name,
            pool_pledge=222,
            pool_cost=123,
            pool_margin=0.512,
            pool_metadata_url="https://www.where_metadata_file_is_located.com",
            pool_metadata_hash=cluster.gen_pool_metadata_hash(pool_metadata_file),
        )

        # create pool owners
        selected_owners = pool_users[:no_of_addr]

        # create node cold key pair and counter
        node_cold = cluster.gen_cold_key_pair_and_counter(node_name=pool_data.pool_name)

        # create deregistration certificate
        pool_dereg_cert_file = cluster.gen_pool_deregistration_cert(
            pool_name=pool_data.pool_name,
            cold_vkey_file=node_cold.vkey_file,
            epoch=cluster.get_epoch() + 1,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[pool_dereg_cert_file],
            signing_key_files=[
                *[p.payment.skey_file for p in selected_owners],
                *[p.stake.skey_file for p in selected_owners],
                node_cold.skey_file,
            ],
        )

        # calculate TX fee
        tx_fee = cluster.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, tx_files=tx_files
        )
        assert helpers.is_in_interval(
            tx_fee, expected_fee
        ), "Expected fee doesn't match the actual fee"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_fee", [(1, 178965), (3, 206949), (5, 234933), (10, 304893)])
    def test_addr_registration_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr_fee: Tuple[int, int],
    ):
        """Test stake address registration fees."""
        no_of_addr, expected_fee = addr_fee
        temp_template = f"{helpers.get_func_name()}_{no_of_addr}"
        src_address = pool_users[0].payment.address
        selected_users = pool_users[:no_of_addr]

        stake_addr_reg_certs = [
            cluster.gen_stake_addr_registration_cert(
                addr_name=f"{temp_template}_addr{i}", stake_vkey_file=p.stake.vkey_file
            )
            for i, p in enumerate(selected_users)
        ]

        # create TX data
        tx_files = clusterlib.TxFiles(
            certificate_files=[*stake_addr_reg_certs],
            signing_key_files=[
                *[p.payment.skey_file for p in selected_users],
                *[p.stake.skey_file for p in selected_users],
            ],
        )

        # calculate TX fee
        tx_fee = cluster.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, tx_files=tx_files
        )
        assert helpers.is_in_interval(
            tx_fee, expected_fee
        ), "Expected fee doesn't match the actual fee"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_fee", [(1, 178965), (3, 206949), (5, 234933), (10, 304893)])
    def test_addr_deregistration_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr_fee: Tuple[int, int],
    ):
        """Test stake address deregistration fees."""
        no_of_addr, expected_fee = addr_fee
        temp_template = f"{helpers.get_func_name()}_{no_of_addr}"
        src_address = pool_users[0].payment.address
        selected_users = pool_users[:no_of_addr]

        stake_addr_dereg_certs = [
            cluster.gen_stake_addr_deregistration_cert(
                addr_name=f"{temp_template}_addr{i}", stake_vkey_file=p.stake.vkey_file
            )
            for i, p in enumerate(selected_users)
        ]

        # create TX data
        tx_files = clusterlib.TxFiles(
            certificate_files=[*stake_addr_dereg_certs],
            signing_key_files=[
                *[p.payment.skey_file for p in selected_users],
                *[p.stake.skey_file for p in selected_users],
            ],
        )

        # calculate TX fee
        tx_fee = cluster.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, tx_files=tx_files
        )
        assert helpers.is_in_interval(
            tx_fee, expected_fee
        ), "Expected fee doesn't match the actual fee"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "amount_expected", [(1, 176677), (100, 176721), (11_000, 176765), (100_000, 176853)]
    )
    def test_transaction_to_1_addr_from_1_addr_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        amount_expected: Tuple[int, int],
    ):
        """Test fees for 1 tx from 1 payment address to 1 payment address."""
        temp_template = f"{helpers.get_func_name()}_{amount_expected[0]}"

        self._from_to_transactions(
            cluster_obj=cluster,
            tx_name=temp_template,
            pool_users=pool_users,
            from_num=1,
            to_num=1,
            amount_expected=amount_expected,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "amount_expected", [(1, 226573), (100, 227013), (11_000, 227453), (100_000, 228333)]
    )
    def test_transaction_to_10_addrs_from_1_addr_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        amount_expected: Tuple[int, int],
    ):
        """Test fees for 1 tx from 1 payment address to 10 payment addresses."""
        temp_template = f"{helpers.get_func_name()}_{amount_expected[0]}"

        self._from_to_transactions(
            cluster_obj=cluster,
            tx_name=temp_template,
            pool_users=pool_users,
            from_num=1,
            to_num=10,
            amount_expected=amount_expected,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "amount_expected", [(1, 259661), (100, 259705), (11_000, 259749), (100_000, 259837)]
    )
    def test_transaction_to_1_addr_from_10_addrs_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        amount_expected: Tuple[int, int],
    ):
        """Test fees for 1 tx from 10 payment addresses to 1 payment address."""
        temp_template = f"{helpers.get_func_name()}_{amount_expected[0]}"

        self._from_to_transactions(
            cluster_obj=cluster,
            tx_name=temp_template,
            pool_users=pool_users,
            from_num=10,
            to_num=1,
            amount_expected=amount_expected,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "amount_expected", [(1, 309557), (100, 309997), (11_000, 310437), (100_000, 311317)]
    )
    def test_transaction_to_10_addrs_from_10_addrs_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        amount_expected: Tuple[int, int],
    ):
        """Test fees for 1 tx from 10 payment addresses to 10 payment addresses."""
        temp_template = f"{helpers.get_func_name()}_{amount_expected[0]}"

        self._from_to_transactions(
            cluster_obj=cluster,
            tx_name=temp_template,
            pool_users=pool_users,
            from_num=10,
            to_num=10,
            amount_expected=amount_expected,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "amount_expected", [(1, 1370881), (100, 1375281), (11_000, 1379681), (100_000, 1388481)]
    )
    def test_transaction_to_100_addrs_from_100_addrs_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        amount_expected: Tuple[int, int],
    ):
        """Test fees for 1 tx from 100 payment addresses to 100 payment addresses."""
        temp_template = f"{helpers.get_func_name()}_{amount_expected[0]}"

        self._from_to_transactions(
            cluster_obj=cluster,
            tx_name=temp_template,
            pool_users=pool_users,
            from_num=100,
            to_num=100,
            amount_expected=amount_expected,
        )
