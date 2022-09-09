"""Tests for delegation of Plutus script stake address.

* stake address registration
* stake address delegation
* rewards withdrawal
* stake address deregistration
"""
import logging
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import pytest_utils
from cardano_node_tests.utils import tx_view

LOGGER = logging.getLogger(__name__)

# skip all tests if Tx era < alonzo
pytestmark = common.SKIPIF_PLUTUS_UNUSABLE


@pytest.fixture
def cluster_lock_42stake(
    cluster_manager: cluster_management.ClusterManager,
) -> Tuple[clusterlib.ClusterLib, str]:
    """Make sure just one staking Plutus test run at a time.

    Plutus script always has the same address. When one script is used in multiple
    tests that are running in parallel, the balances etc. don't add up.
    """
    plutus_script = (
        plutus_common.STAKE_PLUTUS_V2
        if "plutus_v2" in pytest_utils.get_current_test().test_params
        else plutus_common.STAKE_GUESS_42_PLUTUS_V1
    )

    cluster_obj = cluster_manager.get(
        lock_resources=[str(plutus_script.stem)],
        use_resources=[cluster_management.Resources.POOL3],
    )
    pool_id = delegation.get_pool_id(
        cluster_obj=cluster_obj,
        addrs_data=cluster_manager.cache.addrs_data,
        pool_name=cluster_management.Resources.POOL3,
    )
    return cluster_obj, pool_id


@pytest.fixture
def pool_user(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_42stake: Tuple[clusterlib.ClusterLib, str],
) -> delegation.PoolUserScript:
    """Create pool user."""
    cluster, *__ = cluster_lock_42stake
    test_id = common.get_test_id(cluster)

    plutus_script = (
        plutus_common.STAKE_PLUTUS_V2
        if "plutus_v2" in pytest_utils.get_current_test().test_params
        else plutus_common.STAKE_GUESS_42_PLUTUS_V1
    )

    script_stake_address = cluster.gen_stake_addr(
        addr_name=f"{test_id}_pool_user",
        stake_script_file=plutus_script,
    )
    payment_addr_rec = cluster.gen_payment_addr_and_keys(
        name=f"{test_id}_pool_user",
        stake_script_file=plutus_script,
    )
    pool_user = delegation.PoolUserScript(
        payment=payment_addr_rec,
        stake=delegation.AddressRecordScript(
            address=script_stake_address,
            script_file=plutus_script,
        ),
    )

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        payment_addr_rec,
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=10_000_000_000,
    )

    return pool_user


def delegate_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    txins: List[clusterlib.UTXOData],
    collaterals: List[clusterlib.UTXOData],
    pool_user: delegation.PoolUserScript,
    pool_id: str,
    redeemer_file: Path,
    reference_script_utxos: Optional[List[clusterlib.UTXOData]],
) -> Tuple[clusterlib.TxRawOutput, List[dict]]:
    """Submit registration certificate and delegate to pool."""
    # create stake address registration cert
    stake_addr_reg_cert_file = cluster_obj.gen_stake_addr_registration_cert(
        addr_name=f"{temp_template}_addr0", stake_script_file=pool_user.stake.script_file
    )

    # create stake address delegation cert
    stake_addr_deleg_cert_file = cluster_obj.gen_stake_addr_delegation_cert(
        addr_name=f"{temp_template}_addr0",
        stake_script_file=pool_user.stake.script_file,
        stake_pool_id=pool_id,
    )

    src_init_balance = cluster_obj.get_address_balance(pool_user.payment.address)

    # register stake address and delegate it to pool
    reg_cert_script = clusterlib.ComplexCert(
        certificate_file=stake_addr_reg_cert_file,
    )
    deleg_cert_script = clusterlib.ComplexCert(
        certificate_file=stake_addr_deleg_cert_file,
        script_file=pool_user.stake.script_file if not reference_script_utxos else "",
        reference_txin=reference_script_utxos[0] if reference_script_utxos else None,
        collaterals=collaterals,
        redeemer_file=redeemer_file,
    )

    tx_files = clusterlib.TxFiles(signing_key_files=[pool_user.payment.skey_file])

    tx_raw_output = cluster_obj.build_tx(
        src_address=pool_user.payment.address,
        tx_name=f"{temp_template}_reg_deleg",
        txins=txins,
        tx_files=tx_files,
        complex_certs=[reg_cert_script, deleg_cert_script],
        fee_buffer=2_000_000,
        witness_override=len(tx_files.signing_key_files),
    )
    # calculate cost of Plutus script
    plutus_costs = cluster_obj.calculate_plutus_script_cost(
        src_address=pool_user.payment.address,
        tx_name=f"{temp_template}_reg_deleg",
        txins=txins,
        tx_files=tx_files,
        complex_certs=[reg_cert_script, deleg_cert_script],
        fee_buffer=2_000_000,
        witness_override=len(tx_files.signing_key_files),
    )
    tx_signed = cluster_obj.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_reg_deleg",
    )
    cluster_obj.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)

    # check that the balance for source address was correctly updated
    deposit = cluster_obj.get_address_deposit()
    assert (
        cluster_obj.get_address_balance(pool_user.payment.address)
        == src_init_balance - deposit - tx_raw_output.fee
    ), f"Incorrect balance for source address `{pool_user.payment.address}`"

    # check that the stake address was delegated
    stake_addr_info = cluster_obj.get_stake_addr_info(pool_user.stake.address)
    assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"
    assert pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

    return tx_raw_output, plutus_costs


def deregister_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    txins: List[clusterlib.UTXOData],
    collaterals: List[clusterlib.UTXOData],
    pool_user: delegation.PoolUserScript,
    redeemer_file: Path,
    reference_script_utxos: Optional[List[clusterlib.UTXOData]],
) -> clusterlib.TxRawOutput:
    """Deregister stake address."""
    src_payment_balance = cluster_obj.get_address_balance(pool_user.payment.address)
    reward_balance = cluster_obj.get_stake_addr_info(pool_user.stake.address).reward_account_balance

    # create stake address deregistration cert
    stake_addr_dereg_cert = cluster_obj.gen_stake_addr_deregistration_cert(
        addr_name=f"{temp_template}_addr0",
        stake_script_file=pool_user.stake.script_file,
    )

    # withdraw rewards to payment address, deregister stake address
    withdrawal_script = clusterlib.ScriptWithdrawal(
        txout=clusterlib.TxOut(address=pool_user.stake.address, amount=-1),
        script_file=pool_user.stake.script_file if not reference_script_utxos else "",
        reference_txin=reference_script_utxos[0] if reference_script_utxos else None,
        collaterals=[collaterals[0]],
        redeemer_file=redeemer_file,
    )
    dereg_cert_script = clusterlib.ComplexCert(
        certificate_file=stake_addr_dereg_cert,
        script_file=pool_user.stake.script_file if not reference_script_utxos else "",
        reference_txin=reference_script_utxos[0] if reference_script_utxos else None,
        collaterals=[collaterals[1]],
        redeemer_file=redeemer_file,
    )

    tx_files = clusterlib.TxFiles(signing_key_files=[pool_user.payment.skey_file])

    tx_raw_output = cluster_obj.build_tx(
        src_address=pool_user.payment.address,
        tx_name=f"{temp_template}_dereg_withdraw",
        txins=txins,
        tx_files=tx_files,
        complex_certs=[dereg_cert_script],
        fee_buffer=2_000_000,
        script_withdrawals=[withdrawal_script],
        witness_override=len(tx_files.signing_key_files),
    )
    # calculate cost of Plutus script
    plutus_costs = cluster_obj.calculate_plutus_script_cost(
        src_address=pool_user.payment.address,
        tx_name=f"{temp_template}_dereg_withdraw",
        txins=txins,
        tx_files=tx_files,
        complex_certs=[dereg_cert_script],
        fee_buffer=2_000_000,
        script_withdrawals=[withdrawal_script],
        witness_override=len(tx_files.signing_key_files),
    )
    tx_signed = cluster_obj.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_reg_deleg",
    )

    cluster_obj.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)

    # check that the key deposit was returned and rewards withdrawn
    assert (
        cluster_obj.get_address_balance(pool_user.payment.address)
        == src_payment_balance
        - tx_raw_output.fee
        + reward_balance
        + cluster_obj.get_address_deposit()
    ), f"Incorrect balance for source address `{pool_user.payment.address}`"

    # check that the stake address is no longer delegated
    stake_addr_info = cluster_obj.get_stake_addr_info(pool_user.stake.address)
    assert not stake_addr_info.delegation, f"Stake address is still delegated: {stake_addr_info}"

    tx_db_dereg = dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)
    if tx_db_dereg:
        assert pool_user.stake.address in tx_db_dereg.stake_deregistration

        # compare cost of Plutus script with data from db-sync

        dbsync_utils.check_plutus_costs(
            redeemer_records=tx_db_dereg.redeemers, cost_records=plutus_costs
        )

    return tx_raw_output


# don't run these tests on testnets as a stake address corresponding to the Plutus script
# might be already in use
@pytest.mark.order(8)
@common.SKIPIF_BUILD_UNUSABLE
@common.PARAM_PLUTUS_VERSION
class TestDelegateAddr:
    """Tests for address delegation to stake pools."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_delegate_deregister(
        self,
        cluster_lock_42stake: Tuple[clusterlib.ClusterLib, str],
        pool_user: delegation.PoolUserScript,
        plutus_version: str,
    ):
        """Delegate and deregister Plutus script stake address.

        * submit registration certificate and delegate stake address to pool
        * check that the stake address was delegated
        * withdraw rewards to payment address and deregister stake address
        * check that the key deposit was returned and rewards withdrawn
        * check that the stake address is no longer delegated
        * (optional) check records in db-sync
        """
        cluster, pool_id = cluster_lock_42stake
        temp_template = common.get_test_id(cluster)

        collateral_fund_deleg = 1_500_000_000
        collateral_fund_withdraw = 1_500_000_000
        collateral_fund_dereg = 1_500_000_000
        deleg_fund = 1_500_000_000
        dereg_fund = 1_500_000_000

        if cluster.get_stake_addr_info(pool_user.stake.address):
            pytest.skip(
                f"The Plutus script stake address '{pool_user.stake.address}' is already "
                "registered, cannot continue."
            )

        # Step 1: create Tx inputs for step 2 and step 3
        txouts_step1 = [
            # for collateral
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_deleg),
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_withdraw),
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_dereg),
            # for delegation
            clusterlib.TxOut(address=pool_user.payment.address, amount=deleg_fund),
            # for deregistration
            clusterlib.TxOut(address=pool_user.payment.address, amount=dereg_fund),
        ]

        if plutus_version == "v2":
            txouts_step1.append(
                clusterlib.TxOut(
                    address=pool_user.payment.address,
                    amount=10_000_000,
                    reference_script_file=plutus_common.STAKE_PLUTUS_V2,
                )
            )

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[pool_user.payment.skey_file],
        )
        tx_output_step1 = cluster.build_tx(
            src_address=pool_user.payment.address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            txouts=txouts_step1,
            fee_buffer=2_000_000,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        step1_utxos = cluster.get_utxo(tx_raw_output=tx_output_step1)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=step1_utxos, txouts=tx_output_step1.txouts
        )
        collateral_deleg = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset)
        collateral_withdraw = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 1)
        collateral_dereg = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 2)
        deleg_utxos = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 3)
        dereg_utxos = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 4)

        reference_script_utxos = (
            clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 5)
            if plutus_version == "v2"
            else None
        )

        # Step 2: register and delegate

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.get_epoch()

        # submit registration certificate and delegate to pool
        tx_raw_delegation_out, plutus_cost_deleg = delegate_stake_addr(
            cluster_obj=cluster,
            temp_template=temp_template,
            txins=deleg_utxos,
            collaterals=collateral_deleg,
            pool_user=pool_user,
            pool_id=pool_id,
            redeemer_file=plutus_common.REDEEMER_42,
            reference_script_utxos=reference_script_utxos,
        )

        assert (
            cluster.get_epoch() == init_epoch
        ), "Delegation took longer than expected and would affect other checks"

        tx_db_record = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_raw_delegation_out
        )
        delegation.db_check_delegation(
            pool_user=pool_user,
            db_record=tx_db_record,
            deleg_epoch=init_epoch,
            pool_id=pool_id,
        )

        # Step 3: withdraw rewards and deregister

        reward_error = ""

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_new_epoch(new_epochs=4, padding_seconds=10)
        if not cluster.get_stake_addr_info(pool_user.stake.address).reward_account_balance:
            reward_error = f"User of pool '{pool_id}' hasn't received any rewards."

        # make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # submit deregistration certificate and withdraw rewards
        tx_raw_deregister_out = deregister_stake_addr(
            cluster_obj=cluster,
            temp_template=temp_template,
            txins=dereg_utxos,
            collaterals=[*collateral_withdraw, *collateral_dereg],
            pool_user=pool_user,
            redeemer_file=plutus_common.REDEEMER_42,
            reference_script_utxos=reference_script_utxos,
        )

        if reward_error:
            raise AssertionError(reward_error)

        # check tx_view of step 2 and step 3
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_delegation_out)
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_deregister_out)

        # compare cost of Plutus script with data from db-sync
        if tx_db_record:
            dbsync_utils.check_plutus_costs(
                redeemer_records=tx_db_record.redeemers, cost_records=plutus_cost_deleg
            )
