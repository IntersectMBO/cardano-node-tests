import logging
from pathlib import Path
from typing import List
from typing import Tuple

import hypothesis
import hypothesis.strategies as st
import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir and change to it."""
    tmp_path = Path(tmp_path_factory.mktemp("test_transactions"))
    with helpers.change_cwd(tmp_path):
        yield tmp_path


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


class TestBasic:
    @pytest.fixture(scope="class")
    def payment_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        addrs = helpers.create_payment_addr_records(
            "addr_basic0", "addr_basic1", cluster_obj=cluster_session
        )

        # fund source addresses
        helpers.fund_from_faucet(
            *addrs,
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        return addrs

    def test_transfer_funds(
        self, cluster_session: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send funds from faucet to payment address."""
        cluster = cluster_session
        amount = 2000

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_data = cluster.send_funds(
            src_address=src_address, destinations=destinations, tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_data.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"

    def test_transfer_all_funds(
        self, cluster_session: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send ALL funds from one payment address to another."""
        cluster = cluster_session

        src_address = payment_addrs[1].address
        dst_address = payment_addrs[0].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        # amount value -1 means all available funds
        destinations = [clusterlib.TxOut(address=dst_address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[1].skey_file])

        tx_raw_data = cluster.send_funds(
            src_address=src_address, destinations=destinations, tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address) == 0
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address)
            == dst_init_balance + src_init_balance - tx_raw_data.fee
        ), f"Incorrect balance for destination address `{dst_address}`"

    def test_get_txid(
        self, cluster_session: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Get transaction ID (txid) from transaction body.

        Transaction ID is a hash of transaction body and doesn't change for a signed TX.
        """
        cluster = cluster_session

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=2000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        tx_raw_data = cluster.send_funds(
            src_address=src_address, destinations=destinations, tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        txid = cluster.get_txid(tx_raw_data.out_file)
        utxo = cluster.get_utxo(src_address)
        assert len(txid) == 64
        assert txid in (u.utxo_hash for u in utxo)


class Test10InOut:
    @pytest.fixture(scope="class")
    def payment_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ) -> List[clusterlib.AddressRecord]:
        """Create 11 new payment addresses."""
        addrs = helpers.create_payment_addr_records(
            *[f"addr_10_in_out{i}" for i in range(11)], cluster_obj=cluster_session,
        )

        # fund source addresses
        helpers.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        return addrs

    def test_10_transactions(
        self, cluster_session: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send 10 transactions from faucet to payment address.

        Test 10 different UTXOs in addr0.
        """
        cluster = cluster_session
        no_of_transactions = len(payment_addrs) - 1

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        fee = cluster.calculate_tx_fee(
            src_address, dst_addresses=[dst_address], tx_files=tx_files, ttl=ttl,
        )
        amount = int(fee / no_of_transactions + 1000)
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        for __ in range(no_of_transactions):
            cluster.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
            )
            cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - fee * no_of_transactions - amount * no_of_transactions
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address)
            == dst_init_balance + amount * no_of_transactions
        ), f"Incorrect balance for destination address `{dst_address}`"

    def test_transaction_to_10_addrs(
        self, cluster_session: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send 1 transaction from one payment address to 10 payment addresses."""
        cluster = cluster_session
        src_address = payment_addrs[0].address
        # addr1..addr10
        dst_addresses = [payment_addrs[i].address for i in range(1, len(payment_addrs))]

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balances = {addr: cluster.get_address_balance(addr) for addr in dst_addresses}

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        fee = cluster.calculate_tx_fee(
            src_address, dst_addresses=dst_addresses, tx_files=tx_files, ttl=ttl,
        )
        amount = int((cluster.get_address_balance(src_address) - fee) / len(dst_addresses))
        destinations = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]

        cluster.send_funds(
            src_address=src_address, destinations=destinations, tx_files=tx_files, fee=fee, ttl=ttl,
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert cluster.get_address_balance(src_address) == src_init_balance - fee - amount * len(
            dst_addresses
        ), f"Incorrect balance for source address `{src_address}`"

        for addr in dst_addresses:
            assert (
                cluster.get_address_balance(addr) == dst_init_balances[addr] + amount
            ), f"Incorrect balance for destination address `{addr}`"


class TestNotBalanced:
    @pytest.fixture(scope="class")
    def payment_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        addrs = helpers.create_payment_addr_records(
            "addr_not_balanced0", "addr_not_balanced1", cluster_obj=cluster_session
        )

        # fund source addresses
        helpers.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        return addrs

    def test_negative_change(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        temp_dir: Path,
    ):
        """Build a transaction with a negative change."""
        cluster = cluster_session
        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        fee = cluster.calculate_tx_fee(
            src_address, dst_addresses=[dst_address], tx_files=tx_files, ttl=ttl,
        )

        src_addr_highest_utxo = cluster.get_utxo_with_highest_amount(src_address)

        # use only the UTXO with highest amount
        txins = [src_addr_highest_utxo]
        # try to transfer +1 Lovelace more than available and use a negative change (-1)
        txouts = [
            clusterlib.TxOut(address=dst_address, amount=src_addr_highest_utxo.amount - fee + 1),
            clusterlib.TxOut(address=src_address, amount=-1),
        ]
        assert txins[0].amount - txouts[0].amount - fee == txouts[-1].amount

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_raw_tx_bare(
                out_file=temp_dir / "tx.body",
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
            )
        assert "option --tx-out: Failed reading" in str(excinfo.value)

    @hypothesis.given(transfer_add=st.integers(), change_amount=st.integers(min_value=0))
    @hypothesis.settings(deadline=None)
    def test_wrong_balance(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        temp_dir: Path,
        transfer_add: int,
        change_amount: int,
    ):
        """Build a transaction with unbalanced change."""
        # we want to test only unbalanced transactions
        hypothesis.assume((transfer_add + change_amount) != 0)

        cluster = cluster_session

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_addr_highest_utxo = cluster.get_utxo_with_highest_amount(src_address)
        fee = 200_000

        # add to `transferred_amount` the value from test's parameter to unbalance the transaction
        transferred_amount = src_addr_highest_utxo.amount - fee + transfer_add
        # make sure the change amount is valid
        hypothesis.assume(0 <= transferred_amount <= src_addr_highest_utxo.amount)

        out_file_tx = temp_dir / f"{clusterlib.get_timestamped_rand_str()}_tx.body"
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        ttl = cluster.calculate_tx_ttl()

        # use only the UTXO with highest amount
        txins = [src_addr_highest_utxo]
        txouts = [
            clusterlib.TxOut(address=dst_address, amount=transferred_amount),
            # Add the value from test's parameter to unbalance the transaction. Since the correct
            # change amount here is 0, the value from test's parameter can be used directly.
            clusterlib.TxOut(address=src_address, amount=change_amount),
        ]

        # it should be possible to build and sign an unbalanced transaction
        cluster.build_raw_tx_bare(
            out_file=out_file_tx, txins=txins, txouts=txouts, tx_files=tx_files, fee=fee, ttl=ttl,
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=out_file_tx, signing_key_files=tx_files.signing_key_files,
        )

        # it should NOT be possible to submit an unbalanced transaction
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(out_file_signed)
        assert "ValueNotConservedUTxO" in str(excinfo.value)


class TestFee:
    @pytest.fixture(scope="class")
    def payment_addrs(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        addrs = helpers.create_payment_addr_records(
            "addr_test_fee0", "addr_test_fee1", cluster_obj=cluster_session
        )

        # fund source addresses
        helpers.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        return addrs

    @hypothesis.given(fee=st.integers(max_value=-1))
    @hypothesis.settings(deadline=None)
    def test_negative_fee(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fee: int,
    ):
        """Send a transaction with negative fee."""
        cluster = cluster_session
        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=10)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_funds(
                src_address=src_address, destinations=destinations, tx_files=tx_files, fee=fee,
            )
        assert "option --fee: cannot parse value" in str(excinfo.value)

    @pytest.mark.parametrize("fee_change", [0, 1.1, 1.5, 2])
    def test_smaller_fee(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fee_change: float,
    ):
        """Send a transaction with smaller-than-expected fee."""
        cluster = cluster_session
        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=10)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        fee = 0.0
        if fee_change:
            fee = (
                cluster.calculate_tx_fee(src_address, txouts=destinations, tx_files=tx_files)
                / fee_change
            )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_funds(
                src_address=src_address, destinations=destinations, tx_files=tx_files, fee=int(fee),
            )
        assert "FeeTooSmallUTxO" in str(excinfo.value)

    @pytest.mark.parametrize("fee_add", [0, 1000, 100_000, 1_000_000])
    def test_expected_or_higher_fee(
        self,
        cluster_session: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fee_add: int,
    ):
        """Send a transaction fee that is same or higher than expected."""
        cluster = cluster_session
        amount = 100

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(dst_address)

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        fee = (
            cluster.calculate_tx_fee(src_address, txouts=destinations, tx_files=tx_files) + fee_add
        )

        tx_raw_data = cluster.send_funds(
            src_address=src_address, destinations=destinations, tx_files=tx_files, fee=fee,
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert tx_raw_data.fee == fee, "The actual fee doesn't match the specified fee"

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_data.fee - len(destinations) * amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_address}`"


class TestExpectedFees:
    @pytest.fixture(scope="class")
    def pool_owners(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ) -> List[clusterlib.PoolOwner]:
        """Create pool owners."""
        pool_owners = common.create_pool_owners(
            cluster_obj=cluster_session, temp_template="test_expected_fees", no_of_addr=10,
        )

        # fund source addresses
        helpers.fund_from_faucet(
            pool_owners[0].payment,
            cluster_obj=cluster_session,
            faucet_data=addrs_data_session["user1"],
            request=request,
        )

        return pool_owners

    def _create_pool_certificates(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_owners: List[clusterlib.PoolOwner],
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
                addr_name=f"addr{i}_{temp_template}", stake_vkey_file=p.stake.vkey_file
            )
            for i, p in enumerate(pool_owners)
        ]

        # create stake address delegation cert
        stake_addr_deleg_cert_files = [
            cluster_obj.gen_stake_addr_delegation_cert(
                addr_name=f"addr{i}_{temp_template}",
                stake_vkey_file=p.stake.vkey_file,
                node_cold_vkey_file=node_cold.vkey_file,
            )
            for i, p in enumerate(pool_owners)
        ]

        # create stake pool registration cert
        pool_reg_cert_file = cluster_obj.gen_pool_registration_cert(
            pool_data=pool_data,
            node_vrf_vkey_file=node_vrf.vkey_file,
            node_cold_vkey_file=node_cold.vkey_file,
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

    @pytest.mark.parametrize("addr_fee", [(1, 197929), (3, 234185), (5, 270441), (10, 361081)])
    def test_pool_fees(
        self,
        cluster_session: clusterlib.ClusterLib,
        temp_dir: Path,
        pool_owners: List[clusterlib.PoolOwner],
        addr_fee: Tuple[int, int],
    ):
        """Test pool registration fees."""
        cluster = cluster_session
        no_of_addr, expected_fee = addr_fee
        temp_template = f"test_pool_fees_{no_of_addr}owners"

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
            pool_name=f"poolXY_{no_of_addr}",
            pool_pledge=1000,
            pool_cost=15,
            pool_margin=0.2,
            pool_metadata_url="https://www.where_metadata_file_is_located.com",
            pool_metadata_hash=cluster.gen_pool_metadata_hash(pool_metadata_file),
        )

        # create pool owners
        selected_owners = pool_owners[:no_of_addr]

        # create certificates
        src_address, tx_files = self._create_pool_certificates(
            cluster_obj=cluster,
            pool_owners=selected_owners,
            temp_template=temp_template,
            pool_data=pool_data,
        )

        # calculate TX fee
        tx_fee = cluster.calculate_tx_fee(src_address=src_address, tx_files=tx_files)
        assert tx_fee == expected_fee, "Expected fee doesn't match the actual fee"

    @pytest.mark.parametrize("addr_fee", [(1, 179141), (3, 194629), (5, 210117), (10, 248837)])
    def test_addr_deregister_using_cert(
        self,
        cluster_session: clusterlib.ClusterLib,
        pool_owners: List[clusterlib.PoolOwner],
        addr_fee: Tuple[int, int],
    ):
        """Test stake addr deregistration fees."""
        cluster = cluster_session
        no_of_addr, expected_fee = addr_fee
        temp_template = "test_addr_deregister_using_cert"
        pool_user = pool_owners[0]
        selected_users = pool_owners[:no_of_addr]

        stake_addr_dereg_certs = [
            cluster.gen_stake_addr_deregistration_cert(
                addr_name=f"addr{i}_{temp_template}", stake_vkey_file=p.stake.vkey_file
            )
            for i, p in enumerate(selected_users)
        ]

        src_address = pool_user.payment.address

        # create TX data
        tx_files = clusterlib.TxFiles(
            certificate_files=[*stake_addr_dereg_certs],
            signing_key_files=[
                pool_user.payment.skey_file,
                *[p.stake.skey_file for p in selected_users],
            ],
        )

        # calculate TX fee
        tx_fee = cluster.calculate_tx_fee(src_address=src_address, tx_files=tx_files)
        assert tx_fee == expected_fee, "Expected fee doesn't match the actual fee"


def test_past_ttl(
    cluster_session: clusterlib.ClusterLib, addrs_data_session: dict, request: FixtureRequest
):
    """Send a transaction with ttl in the past."""
    cluster = cluster_session
    payment_addrs = helpers.create_payment_addr_records(
        "addr_past_ttl0", "addr_past_ttl1", cluster_obj=cluster
    )

    # fund source addresses
    helpers.fund_from_faucet(
        payment_addrs[0],
        cluster_obj=cluster_session,
        faucet_data=addrs_data_session["user1"],
        request=request,
    )

    src_address = payment_addrs[0].address
    dst_address = payment_addrs[1].address

    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
    destinations = [clusterlib.TxOut(address=dst_address, amount=1)]
    ttl = cluster.get_last_block_slot_no() - 1
    fee = cluster.calculate_tx_fee(src_address, txouts=destinations, tx_files=tx_files, ttl=ttl)

    # it should be possible to build and sign a transaction with ttl in the past
    tx_raw_data = cluster.build_raw_tx(
        src_address=src_address, txouts=destinations, tx_files=tx_files, fee=fee, ttl=ttl,
    )
    out_file_signed = cluster.sign_tx(
        tx_body_file=tx_raw_data.out_file, signing_key_files=tx_files.signing_key_files,
    )

    # it should NOT be possible to submit a transaction with ttl in the past
    with pytest.raises(clusterlib.CLIError) as excinfo:
        cluster.submit_tx(out_file_signed)
    assert "ExpiredUTxO" in str(excinfo.value)


def test_send_funds_to_reward_address(
    cluster_session: clusterlib.ClusterLib, addrs_data_session: dict, request: FixtureRequest
):
    """Send funds from payment address to stake address."""
    cluster = cluster_session

    stake_addr_rec = helpers.create_stake_addr_records(
        "addr_send_funds_to_reward_address0", cluster_obj=cluster
    )[0]
    payment_addr_rec = helpers.create_payment_addr_records(
        "addr_send_funds_to_reward_address0",
        cluster_obj=cluster,
        stake_vkey_file=stake_addr_rec.vkey_file,
    )[0]

    # fund source address
    helpers.fund_from_faucet(
        payment_addr_rec,
        cluster_obj=cluster,
        faucet_data=addrs_data_session["user1"],
        request=request,
    )

    tx_files = clusterlib.TxFiles(signing_key_files=[stake_addr_rec.skey_file])
    destinations = [clusterlib.TxOut(address=stake_addr_rec.address, amount=1000)]

    # it should NOT be possible to build a transaction using a stake address
    with pytest.raises(clusterlib.CLIError) as excinfo:
        cluster.build_raw_tx(
            src_address=payment_addr_rec.address, txouts=destinations, tx_files=tx_files, fee=0,
        )
    assert "invalid address" in str(excinfo.value)
