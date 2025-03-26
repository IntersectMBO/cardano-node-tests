"""Tests for fees of various kinds of transactions."""

import itertools
import logging
import pathlib as pl

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import web
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

if VERSIONS.cli >= version.parse("8.21.0.0"):
    POOL_DEREG_PARAMS = [(1, 185_213), (3, 210_205), (5, 225_197), (10, 280_000)]
    TO_FROM_100_PARAMS = [(1, 886265), (100, 890_665), (11_000, 895_065), (100_000, 903_865)]
    TO_10_FROM_1_PARAMS = [(1, 191_813), (100, 192_253), (11_000, 192_693), (100_000, 193_573)]
    TO_1_FROM_10_PARAMS = [(1, 220_501), (100, 220_545), (11_000, 220_589), (100_000, 220_677)]
    TO_10_FROM_10_PARAMS = [(1, 244_657), (100, 245_097), (11_000, 245_537), (100_000, 246_417)]
else:
    POOL_DEREG_PARAMS = [(1, 185_213), (3, 210_205), (5, 235_197), (10, 280_000)]
    TO_FROM_100_PARAMS = [
        (1, 1_200_000),
        (100, 1_210_000),
        (11_000, 1_220_000),
        (100_000, 1_250_000),
    ]
    TO_10_FROM_1_PARAMS = [(1, 226_573), (100, 227_013), (11_000, 227_453), (100_000, 228_333)]
    TO_1_FROM_10_PARAMS = [(1, 259_661), (100, 259_705), (11_000, 259_749), (100_000, 259_837)]
    TO_10_FROM_10_PARAMS = [(1, 309_557), (100, 309_997), (11_000, 310_437), (100_000, 311_317)]


class TestFee:
    """General fees tests."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(fee=st.integers(max_value=-1))
    @common.hypothesis_settings()
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_negative_fee(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        fee: int,
    ):
        """Try to send a transaction with negative fee (property-based test).

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        txouts = [clusterlib.TxOut(address=dst_address, amount=10)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
                fee=fee,
            )
        assert "option --fee:" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("fee_change", (0, 1.1, 1.5, 2))
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_smaller_fee(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        fee_change: float,
    ):
        """Try to send a transaction with smaller-than-expected fee.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        txouts = [clusterlib.TxOut(address=dst_address, amount=10)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        fee = 0.0
        if fee_change:
            fee = (
                cluster.g_transaction.calculate_tx_fee(
                    src_address=src_address,
                    tx_name=temp_template,
                    txouts=txouts,
                    tx_files=tx_files,
                )
                / fee_change
            )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
                fee=int(fee),
            )
        assert "FeeTooSmallUTxO" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("fee_add", (0, 1_000, 100_000, 1_000_000))
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_expected_or_higher_fee(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        fee_add: int,
    ):
        """Send a transaction with fee that is same or higher than expected."""
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        fee = (
            cluster.g_transaction.calculate_tx_fee(
                src_address=src_address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
            )
            + fee_add
        )

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
        )

        assert tx_raw_output.fee == fee, "The actual fee doesn't match the specified fee"

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount, (
            f"Incorrect balance for destination address `{dst_address}`"
        )


class TestExpectedFees:
    """Test expected fees."""

    @pytest.fixture
    def pool_users(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.PoolUser]:
        """Create pool users."""
        created_users = common.get_pool_users(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=201,
            fund_idx=list(range(10)),
            caching_key=helpers.get_current_line_str(),
        )
        return created_users

    def _create_pool_certificates(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_owners: list[clusterlib.PoolUser],
        temp_template: str,
        pool_data: clusterlib.PoolData,
    ) -> tuple[str, clusterlib.TxFiles]:
        """Create certificates for registering a stake pool, delegating stake address."""
        # Create node VRF key pair
        node_vrf = cluster_obj.g_node.gen_vrf_key_pair(node_name=pool_data.pool_name)
        # Create node cold key pair and counter
        node_cold = cluster_obj.g_node.gen_cold_key_pair_and_counter(node_name=pool_data.pool_name)

        # Create stake address registration certs
        stake_addr_reg_cert_files = [
            cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
                addr_name=f"{temp_template}_addr{i}",
                deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster_obj),
                stake_vkey_file=p.stake.vkey_file,
            )
            for i, p in enumerate(pool_owners)
        ]

        # Create stake address delegation cert
        stake_addr_deleg_cert_files = [
            cluster_obj.g_stake_address.gen_stake_addr_delegation_cert(
                addr_name=f"{temp_template}_addr{i}",
                stake_vkey_file=p.stake.vkey_file,
                cold_vkey_file=node_cold.vkey_file,
            )
            for i, p in enumerate(pool_owners)
        ]

        # Create stake pool registration cert
        pool_reg_cert_file = cluster_obj.g_stake_pool.gen_pool_registration_cert(
            pool_data=pool_data,
            vrf_vkey_file=node_vrf.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
            owner_stake_vkey_files=[p.stake.vkey_file for p in pool_owners],
        )

        src_address = pool_owners[0].payment.address

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
        pool_users: list[clusterlib.PoolUser],
        from_num: int,
        to_num: int,
        amount_expected: tuple[int, int],
    ):
        """Check fees for 1 tx from `from_num` payment addresses to `to_num` payment addresses."""
        amount, expected_fee = amount_expected

        src_address = pool_users[0].payment.address
        # Addr1..addr<from_num+1>
        from_addr_recs = [p.payment for p in pool_users[1 : from_num + 1]]
        # Addr<from_num+1>..addr<from_num+to_num+1>
        dst_addresses = [
            pool_users[i].payment.address for i in range(from_num + 1, from_num + to_num + 1)
        ]

        # Create TX data
        _txins = [cluster_obj.g_query.get_utxo(address=r.address) for r in from_addr_recs]
        # Flatten the list of lists that is _txins
        txins = list(itertools.chain.from_iterable(_txins))
        txouts = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]
        tx_files = clusterlib.TxFiles(signing_key_files=[r.skey_file for r in from_addr_recs])

        # Calculate TX fee
        tx_fee = cluster_obj.g_transaction.calculate_tx_fee(
            src_address=src_address, tx_name=tx_name, txins=txins, txouts=txouts, tx_files=tx_files
        )
        assert common.is_fee_in_interval(tx_fee, expected_fee), (
            "Expected fee doesn't match the actual fee"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_fee", [(1, 197_753), (3, 234_009), (5, 270_265), (10, 340_000)])
    @pytest.mark.smoke
    def test_pool_registration_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr_fee: tuple[int, int],
    ):
        """Test pool registration fees."""
        no_of_addr, expected_fee = addr_fee
        rand_str = clusterlib.get_rand_str(4)
        temp_template = f"{common.get_test_id(cluster)}_{rand_str}"

        pool_name = f"pool_{rand_str}"
        pool_metadata = {
            "name": pool_name,
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = pl.Path(
            helpers.write_json(
                out_file=f"{pool_name}_registration_metadata.json", content=pool_metadata
            )
        )
        pool_metadata_url = web.publish(file_path=pool_metadata_file)

        pool_data = clusterlib.PoolData(
            pool_name=pool_name,
            pool_pledge=1_000,
            pool_cost=15,
            pool_margin=0.2,
            pool_metadata_url=pool_metadata_url,
            pool_metadata_hash=cluster.g_stake_pool.gen_pool_metadata_hash(pool_metadata_file),
        )

        # Create pool owners
        selected_owners = pool_users[:no_of_addr]

        # Create certificates
        src_address, tx_files = self._create_pool_certificates(
            cluster_obj=cluster,
            pool_owners=selected_owners,
            temp_template=temp_template,
            pool_data=pool_data,
        )

        # Calculate TX fee
        tx_fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, tx_files=tx_files
        )
        assert common.is_fee_in_interval(tx_fee, expected_fee), (
            "Expected fee doesn't match the actual fee"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_fee", POOL_DEREG_PARAMS)
    @pytest.mark.smoke
    def test_pool_deregistration_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr_fee: tuple[int, int],
    ):
        """Test pool deregistration fees."""
        no_of_addr, expected_fee = addr_fee
        rand_str = clusterlib.get_rand_str(4)
        temp_template = f"{common.get_test_id(cluster)}_{rand_str}"
        src_address = pool_users[0].payment.address

        pool_name = f"pool_{rand_str}"
        pool_metadata = {
            "name": pool_name,
            "description": "Shelley QA E2E test Test",
            "ticker": "QA1",
            "homepage": "www.test1.com",
        }
        pool_metadata_file = pl.Path(
            helpers.write_json(
                out_file=f"{pool_name}_registration_metadata.json", content=pool_metadata
            )
        )
        pool_metadata_url = web.publish(file_path=pool_metadata_file)

        pool_data = clusterlib.PoolData(
            pool_name=pool_name,
            pool_pledge=222,
            pool_cost=123,
            pool_margin=0.512,
            pool_metadata_url=pool_metadata_url,
            pool_metadata_hash=cluster.g_stake_pool.gen_pool_metadata_hash(pool_metadata_file),
        )

        # Create pool owners
        selected_owners = pool_users[:no_of_addr]

        # Create node cold key pair and counter
        node_cold = cluster.g_node.gen_cold_key_pair_and_counter(node_name=pool_data.pool_name)

        # Create deregistration certificate
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

        # Calculate TX fee
        tx_fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, tx_files=tx_files
        )
        assert common.is_fee_in_interval(tx_fee, expected_fee), (
            "Expected fee doesn't match the actual fee"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_fee", [(1, 178_965), (3, 206_949), (5, 234_933), (10, 290_000)])
    @pytest.mark.smoke
    def test_addr_registration_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr_fee: tuple[int, int],
    ):
        """Test stake address registration fees."""
        no_of_addr, expected_fee = addr_fee
        temp_template = common.get_test_id(cluster)
        src_address = pool_users[0].payment.address
        selected_users = pool_users[:no_of_addr]

        stake_addr_reg_certs = [
            cluster.g_stake_address.gen_stake_addr_registration_cert(
                addr_name=f"{temp_template}_addr{i}",
                deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster),
                stake_vkey_file=p.stake.vkey_file,
            )
            for i, p in enumerate(selected_users)
        ]

        # Create TX data
        tx_files = clusterlib.TxFiles(
            certificate_files=[*stake_addr_reg_certs],
            signing_key_files=[
                *[p.payment.skey_file for p in selected_users],
                *[p.stake.skey_file for p in selected_users],
            ],
        )

        # Calculate TX fee
        tx_fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, tx_files=tx_files
        )
        assert common.is_fee_in_interval(tx_fee, expected_fee), (
            "Expected fee doesn't match the actual fee"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_fee", [(1, 178_965), (3, 206_949), (5, 234_933), (10, 290_000)])
    @pytest.mark.smoke
    def test_addr_deregistration_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr_fee: tuple[int, int],
    ):
        """Test stake address deregistration fees."""
        no_of_addr, expected_fee = addr_fee
        temp_template = common.get_test_id(cluster)
        src_address = pool_users[0].payment.address
        selected_users = pool_users[:no_of_addr]

        stake_addr_dereg_certs = [
            cluster.g_stake_address.gen_stake_addr_deregistration_cert(
                addr_name=f"{temp_template}_addr{i}",
                deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster),
                stake_vkey_file=p.stake.vkey_file,
            )
            for i, p in enumerate(selected_users)
        ]

        # Create TX data
        tx_files = clusterlib.TxFiles(
            certificate_files=[*stake_addr_dereg_certs],
            signing_key_files=[
                *[p.payment.skey_file for p in selected_users],
                *[p.stake.skey_file for p in selected_users],
            ],
        )

        # Calculate TX fee
        tx_fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address, tx_name=temp_template, tx_files=tx_files
        )
        assert common.is_fee_in_interval(tx_fee, expected_fee), (
            "Expected fee doesn't match the actual fee"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "amount_expected", [(1, 176_677), (100, 176_721), (11_000, 176_765), (100_000, 176_853)]
    )
    @pytest.mark.smoke
    def test_transaction_to_1_addr_from_1_addr_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        amount_expected: tuple[int, int],
    ):
        """Test fees for 1 tx from 1 payment address to 1 payment address."""
        temp_template = common.get_test_id(cluster)

        self._from_to_transactions(
            cluster_obj=cluster,
            tx_name=temp_template,
            pool_users=pool_users,
            from_num=1,
            to_num=1,
            amount_expected=amount_expected,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("amount_expected", TO_10_FROM_1_PARAMS)
    @pytest.mark.smoke
    def test_transaction_to_10_addrs_from_1_addr_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        amount_expected: tuple[int, int],
    ):
        """Test fees for 1 tx from 1 payment address to 10 payment addresses."""
        temp_template = common.get_test_id(cluster)

        self._from_to_transactions(
            cluster_obj=cluster,
            tx_name=temp_template,
            pool_users=pool_users,
            from_num=1,
            to_num=10,
            amount_expected=amount_expected,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("amount_expected", TO_1_FROM_10_PARAMS)
    @pytest.mark.smoke
    def test_transaction_to_1_addr_from_10_addrs_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        amount_expected: tuple[int, int],
    ):
        """Test fees for 1 tx from 10 payment addresses to 1 payment address."""
        temp_template = common.get_test_id(cluster)

        self._from_to_transactions(
            cluster_obj=cluster,
            tx_name=temp_template,
            pool_users=pool_users,
            from_num=10,
            to_num=1,
            amount_expected=amount_expected,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("amount_expected", TO_10_FROM_10_PARAMS)
    @pytest.mark.smoke
    def test_transaction_to_10_addrs_from_10_addrs_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        amount_expected: tuple[int, int],
    ):
        """Test fees for 1 tx from 10 payment addresses to 10 payment addresses."""
        temp_template = common.get_test_id(cluster)

        self._from_to_transactions(
            cluster_obj=cluster,
            tx_name=temp_template,
            pool_users=pool_users,
            from_num=10,
            to_num=10,
            amount_expected=amount_expected,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.node < version.parse("8.7.0"), reason="Doesn't run on node < 8.7.0"
    )
    @pytest.mark.parametrize("amount_expected", TO_FROM_100_PARAMS)
    @pytest.mark.smoke
    def test_transaction_to_100_addrs_from_100_addrs_fees(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        amount_expected: tuple[int, int],
    ):
        """Test fees for 1 tx from 100 payment addresses to 100 payment addresses."""
        temp_template = common.get_test_id(cluster)

        self._from_to_transactions(
            cluster_obj=cluster,
            tx_name=temp_template,
            pool_users=pool_users,
            from_num=100,
            to_num=100,
            amount_expected=amount_expected,
        )
