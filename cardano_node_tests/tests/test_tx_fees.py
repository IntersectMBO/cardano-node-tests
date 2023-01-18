"""Tests for fees of various kinds of transactions."""
import itertools
import logging
from typing import List
from typing import Tuple

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@pytest.mark.testnets
@pytest.mark.smoke
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
    @common.hypothesis_settings()
    def test_negative_fee(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fee: int,
    ):
        """Try to send a transaction with negative fee (property-based test).

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=10)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_funds(
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
        temp_template = f"{common.get_test_id(cluster)}_{fee_change}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=10)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        fee = 0.0
        if fee_change:
            fee = (
                cluster.g_transaction.calculate_tx_fee(
                    src_address=src_address,
                    tx_name=temp_template,
                    txouts=destinations,
                    tx_files=tx_files,
                )
                / fee_change
            )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=temp_template,
                tx_files=tx_files,
                fee=int(fee),
            )
        assert "FeeTooSmallUTxO" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("fee_add", (0, 1_000, 100_000, 1_000_000))
    def test_expected_or_higher_fee(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fee_add: int,
    ):
        """Send a transaction with fee that is same or higher than expected."""
        temp_template = f"{common.get_test_id(cluster)}_{fee_add}"
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        fee = (
            cluster.g_transaction.calculate_tx_fee(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
            )
            + fee_add
        )

        tx_raw_output = cluster.g_transaction.send_funds(
            src_address=src_address,
            destinations=destinations,
            tx_name=temp_template,
            tx_files=tx_files,
            fee=fee,
        )

        assert tx_raw_output.fee == fee, "The actual fee doesn't match the specified fee"

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_address}`"


@pytest.mark.smoke
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
        node_vrf = cluster_obj.g_node.gen_vrf_key_pair(node_name=pool_data.pool_name)
        # create node cold key pair and counter
        node_cold = cluster_obj.g_node.gen_cold_key_pair_and_counter(node_name=pool_data.pool_name)

        # create stake address registration certs
        stake_addr_reg_cert_files = [
            cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
                addr_name=f"{temp_template}_addr{i}", stake_vkey_file=p.stake.vkey_file
            )
            for i, p in enumerate(pool_owners)
        ]

        # create stake address delegation cert
        stake_addr_deleg_cert_files = [
            cluster_obj.g_stake_address.gen_stake_addr_delegation_cert(
                addr_name=f"{temp_template}_addr{i}",
                stake_vkey_file=p.stake.vkey_file,
                cold_vkey_file=node_cold.vkey_file,
            )
            for i, p in enumerate(pool_owners)
        ]

        # create stake pool registration cert
        pool_reg_cert_file = cluster_obj.g_stake_pool.gen_pool_registration_cert(
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
        _txins = [cluster_obj.g_query.get_utxo(address=r.address) for r in from_addr_recs]
        # flatten the list of lists that is _txins
        txins = list(itertools.chain.from_iterable(_txins))
        txouts = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]
        tx_files = clusterlib.TxFiles(signing_key_files=[r.skey_file for r in from_addr_recs])

        # calculate TX fee
        tx_fee = cluster_obj.g_transaction.calculate_tx_fee(
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
        pool_users: List[clusterlib.PoolUser],
        addr_fee: Tuple[int, int],
    ):
        """Test pool registration fees."""
        no_of_addr, expected_fee = addr_fee
        rand_str = clusterlib.get_rand_str(4)
        temp_template = f"{common.get_test_id(cluster)}_{rand_str}_{no_of_addr}"

        pool_name = f"pool_{rand_str}"
        pool_metadata = {
            "name": pool_name,
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = helpers.write_json(
            f"{pool_name}_registration_metadata.json", pool_metadata
        )

        pool_data = clusterlib.PoolData(
            pool_name=pool_name,
            pool_pledge=1_000,
            pool_cost=15,
            pool_margin=0.2,
            pool_metadata_url="https://www.where_metadata_file_is_located.com",
            pool_metadata_hash=cluster.g_stake_pool.gen_pool_metadata_hash(pool_metadata_file),
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
        tx_fee = cluster.g_transaction.calculate_tx_fee(
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
        pool_users: List[clusterlib.PoolUser],
        addr_fee: Tuple[int, int],
    ):
        """Test pool deregistration fees."""
        no_of_addr, expected_fee = addr_fee
        rand_str = clusterlib.get_rand_str(4)
        temp_template = f"{common.get_test_id(cluster)}_{rand_str}_{no_of_addr}"
        src_address = pool_users[0].payment.address

        pool_name = f"pool_{rand_str}"
        pool_metadata = {
            "name": pool_name,
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = helpers.write_json(
            f"{pool_name}_registration_metadata.json", pool_metadata
        )

        pool_data = clusterlib.PoolData(
            pool_name=pool_name,
            pool_pledge=222,
            pool_cost=123,
            pool_margin=0.512,
            pool_metadata_url="https://www.where_metadata_file_is_located.com",
            pool_metadata_hash=cluster.g_stake_pool.gen_pool_metadata_hash(pool_metadata_file),
        )

        # create pool owners
        selected_owners = pool_users[:no_of_addr]

        # create node cold key pair and counter
        node_cold = cluster.g_node.gen_cold_key_pair_and_counter(node_name=pool_data.pool_name)

        # create deregistration certificate
        pool_dereg_cert_file = cluster.g_stake_pool.gen_pool_deregistration_cert(
            pool_name=pool_data.pool_name,
            cold_vkey_file=node_cold.vkey_file,
            epoch=cluster.g_query.get_epoch() + 1,
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
        tx_fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, tx_files=tx_files
        )
        assert helpers.is_in_interval(
            tx_fee, expected_fee
        ), "Expected fee doesn't match the actual fee"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_fee", [(1, 178_965), (3, 206_949), (5, 234_933), (10, 304_893)])
    def test_addr_registration_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr_fee: Tuple[int, int],
    ):
        """Test stake address registration fees."""
        no_of_addr, expected_fee = addr_fee
        temp_template = f"{common.get_test_id(cluster)}_{no_of_addr}"
        src_address = pool_users[0].payment.address
        selected_users = pool_users[:no_of_addr]

        stake_addr_reg_certs = [
            cluster.g_stake_address.gen_stake_addr_registration_cert(
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
        tx_fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, tx_files=tx_files
        )
        assert helpers.is_in_interval(
            tx_fee, expected_fee
        ), "Expected fee doesn't match the actual fee"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_fee", [(1, 178_965), (3, 206_949), (5, 234_933), (10, 304_893)])
    def test_addr_deregistration_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr_fee: Tuple[int, int],
    ):
        """Test stake address deregistration fees."""
        no_of_addr, expected_fee = addr_fee
        temp_template = f"{common.get_test_id(cluster)}_{no_of_addr}"
        src_address = pool_users[0].payment.address
        selected_users = pool_users[:no_of_addr]

        stake_addr_dereg_certs = [
            cluster.g_stake_address.gen_stake_addr_deregistration_cert(
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
        tx_fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, tx_files=tx_files
        )
        assert helpers.is_in_interval(
            tx_fee, expected_fee
        ), "Expected fee doesn't match the actual fee"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "amount_expected", [(1, 176_677), (100, 176_721), (11_000, 176_765), (100_000, 176_853)]
    )
    def test_transaction_to_1_addr_from_1_addr_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        amount_expected: Tuple[int, int],
    ):
        """Test fees for 1 tx from 1 payment address to 1 payment address."""
        temp_template = f"{common.get_test_id(cluster)}_{amount_expected[0]}"

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
        "amount_expected", [(1, 226_573), (100, 227_013), (11_000, 227_453), (100_000, 228_333)]
    )
    def test_transaction_to_10_addrs_from_1_addr_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        amount_expected: Tuple[int, int],
    ):
        """Test fees for 1 tx from 1 payment address to 10 payment addresses."""
        temp_template = f"{common.get_test_id(cluster)}_{amount_expected[0]}"

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
        "amount_expected", [(1, 259_661), (100, 259_705), (11_000, 259_749), (100_000, 259_837)]
    )
    def test_transaction_to_1_addr_from_10_addrs_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        amount_expected: Tuple[int, int],
    ):
        """Test fees for 1 tx from 10 payment addresses to 1 payment address."""
        temp_template = f"{common.get_test_id(cluster)}_{amount_expected[0]}"

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
        "amount_expected", [(1, 309_557), (100, 309_997), (11_000, 310_437), (100_000, 311_317)]
    )
    def test_transaction_to_10_addrs_from_10_addrs_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        amount_expected: Tuple[int, int],
    ):
        """Test fees for 1 tx from 10 payment addresses to 10 payment addresses."""
        temp_template = f"{common.get_test_id(cluster)}_{amount_expected[0]}"

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
        "amount_expected",
        [(1, 1_370_881), (100, 1_375_281), (11_000, 1_379_681), (100_000, 1_388_481)],
    )
    def test_transaction_to_100_addrs_from_100_addrs_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        amount_expected: Tuple[int, int],
    ):
        """Test fees for 1 tx from 100 payment addresses to 100 payment addresses."""
        temp_template = f"{common.get_test_id(cluster)}_{amount_expected[0]}"

        self._from_to_transactions(
            cluster_obj=cluster,
            tx_name=temp_template,
            pool_users=pool_users,
            from_num=100,
            to_num=100,
            amount_expected=amount_expected,
        )
