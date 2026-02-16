"""Tests for delegation of Plutus script stake address.

* Stake address registration
* Stake address delegation
* Rewards withdrawal
* Stake address deregistration
"""

import logging
import pathlib as pl
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import pytest_utils
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

CLI_WITH_ISSUE_942 = version.parse("10.0.0.0")

pytestmark = [
    common.SKIPIF_PLUTUS_UNUSABLE,
    pytest.mark.plutus,
]


@pytest.fixture
def cluster_lock_42stake(
    cluster_manager: cluster_management.ClusterManager,
) -> tuple[clusterlib.ClusterLib, str]:
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
        lock_resources=[helpers.checksum(plutus_script)],
        use_resources=[
            resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
            cluster_management.Resources.REWARDS,
            cluster_management.Resources.PLUTUS,
        ],
    )
    pool_name = cluster_manager.get_used_resources(from_set=cluster_management.Resources.ALL_POOLS)[
        0
    ]
    pool_id = delegation.get_pool_id(
        cluster_obj=cluster_obj,
        addrs_data=cluster_manager.cache.addrs_data,
        pool_name=pool_name,
    )
    return cluster_obj, pool_id


@pytest.fixture
def pool_user(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_42stake: tuple[clusterlib.ClusterLib, str],
) -> delegation.PoolUserScript:
    """Create pool user."""
    cluster, *__ = cluster_lock_42stake
    test_id = common.get_test_id(cluster)

    plutus_script = (
        plutus_common.STAKE_PLUTUS_V2
        if "plutus_v2" in pytest_utils.get_current_test().test_params
        else plutus_common.STAKE_GUESS_42_PLUTUS_V1
    )

    script_stake_address = cluster.g_stake_address.gen_stake_addr(
        addr_name=f"{test_id}_pool_user",
        stake_script_file=plutus_script,
    )
    payment_addr_rec = cluster.g_address.gen_payment_addr_and_keys(
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

    # Fund source addresses
    clusterlib_utils.fund_from_faucet(
        payment_addr_rec,
        cluster_obj=cluster,
        all_faucets=cluster_manager.cache.addrs_data,
        amount=18_000_000_000,
    )

    return pool_user


def register_delegate_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    txins: list[clusterlib.UTXOData],
    collaterals: list[clusterlib.UTXOData],
    pool_user: delegation.PoolUserScript,
    pool_id: str,
    redeemer_file: pl.Path,
    reference_script_utxos: list[clusterlib.UTXOData] | None,
    build_method: str,
) -> tuple[clusterlib.TxRawOutput, list[dict]]:
    """Submit registration certificate and delegate to pool."""
    temp_template = f"{temp_template}_reg_deleg"

    # Create stake address registration cert
    stake_addr_reg_cert_file = cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
        addr_name=f"{temp_template}_addr0",
        deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster_obj),
        stake_script_file=pool_user.stake.script_file,
    )

    # Create stake address delegation cert
    stake_addr_deleg_cert_file = cluster_obj.g_stake_address.gen_stake_addr_delegation_cert(
        addr_name=f"{temp_template}_addr0",
        stake_script_file=pool_user.stake.script_file,
        stake_pool_id=pool_id,
    )

    src_init_balance = cluster_obj.g_query.get_address_balance(pool_user.payment.address)

    # Register stake address and delegate it to pool
    execution_units = (218855869, 686154)
    raw_fee = 400_000
    if VERSIONS.transaction_era >= VERSIONS.CONWAY:
        execution_units = (240000000, 790000)
        raw_fee = 500_000

        reg_cert_script = clusterlib.ComplexCert(
            certificate_file=stake_addr_reg_cert_file,
            script_file=pool_user.stake.script_file if not reference_script_utxos else "",
            reference_txin=reference_script_utxos[0] if reference_script_utxos else None,
            collaterals=collaterals,
            execution_units=execution_units,
            redeemer_file=redeemer_file,
        )
    else:
        reg_cert_script = clusterlib.ComplexCert(
            certificate_file=stake_addr_reg_cert_file,
        )

    deleg_cert_script = clusterlib.ComplexCert(
        certificate_file=stake_addr_deleg_cert_file,
        script_file=pool_user.stake.script_file if not reference_script_utxos else "",
        reference_txin=reference_script_utxos[0] if reference_script_utxos else None,
        collaterals=collaterals,
        execution_units=execution_units,
        redeemer_file=redeemer_file,
    )

    tx_files = clusterlib.TxFiles(signing_key_files=[pool_user.payment.skey_file])

    # Calculate cost of Plutus script
    plutus_costs = (
        cluster_obj.g_transaction.calculate_plutus_script_cost(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            txins=txins,
            tx_files=tx_files,
            complex_certs=[reg_cert_script, deleg_cert_script],
            fee_buffer=2_000_000,
            witness_override=len(tx_files.signing_key_files),
        )
        if build_method == clusterlib_utils.BuildMethods.BUILD
        else []
    )

    tx_output = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=temp_template,
        src_address=pool_user.payment.address,
        txins=txins,
        build_method=build_method,
        tx_files=tx_files,
        complex_certs=[reg_cert_script, deleg_cert_script],
        raw_fee=raw_fee,
    )

    # Check that the balance for source address was correctly updated
    deposit = cluster_obj.g_query.get_address_deposit()
    assert (
        cluster_obj.g_query.get_address_balance(pool_user.payment.address)
        == src_init_balance - deposit - tx_output.fee
    ), f"Incorrect balance for source address `{pool_user.payment.address}`"

    # Check that the stake address was delegated
    stake_addr_info = cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address)
    assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"
    assert pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

    return tx_output, plutus_costs


def register_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    txins: list[clusterlib.UTXOData],
    collaterals: list[clusterlib.UTXOData],
    pool_user: delegation.PoolUserScript,
    redeemer_file: pl.Path,
    reference_script_utxos: list[clusterlib.UTXOData] | None,
    build_method: str,
) -> tuple[clusterlib.TxRawOutput, list[dict]]:
    """Register a stake address."""
    temp_template = f"{temp_template}_reg"

    # Create stake address registration cert
    stake_addr_reg_cert_file = cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
        addr_name=f"{temp_template}_addr0",
        deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster_obj),
        stake_script_file=pool_user.stake.script_file,
    )

    src_init_balance = cluster_obj.g_query.get_address_balance(pool_user.payment.address)

    reg_cert_script = clusterlib.ComplexCert(
        certificate_file=stake_addr_reg_cert_file,
        script_file=pool_user.stake.script_file if not reference_script_utxos else "",
        reference_txin=reference_script_utxos[0] if reference_script_utxos else None,
        collaterals=collaterals,
        execution_units=(218855869, 686154),
        redeemer_file=redeemer_file,
    )

    tx_files = clusterlib.TxFiles(signing_key_files=[pool_user.payment.skey_file])
    plutus_costs_reg = []

    if build_method == clusterlib_utils.BuildMethods.BUILD:
        tx_raw_output_reg = cluster_obj.g_transaction.build_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            txins=txins,
            tx_files=tx_files,
            complex_certs=[reg_cert_script],
            fee_buffer=2_000_000,
            witness_override=len(tx_files.signing_key_files),
        )
        # Calculate cost of Plutus script
        plutus_costs_reg = cluster_obj.g_transaction.calculate_plutus_script_cost(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            txins=txins,
            tx_files=tx_files,
            complex_certs=[reg_cert_script],
            fee_buffer=2_000_000,
            witness_override=len(tx_files.signing_key_files),
        )
        tx_signed_reg = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_raw_output_reg.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster_obj.g_transaction.submit_tx(tx_file=tx_signed_reg, txins=tx_raw_output_reg.txins)
    else:
        tx_raw_output_reg = cluster_obj.g_transaction.send_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            txins=txins,
            tx_files=tx_files,
            complex_certs=[reg_cert_script],
            fee=300_000,
        )

    # Check that the balance for source address was correctly updated
    deposit = cluster_obj.g_query.get_address_deposit()
    assert (
        cluster_obj.g_query.get_address_balance(pool_user.payment.address)
        == src_init_balance - deposit - tx_raw_output_reg.fee
    ), f"Incorrect balance for source address `{pool_user.payment.address}`"

    # Check that the stake address was registered
    stake_addr_info = cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address)
    assert stake_addr_info, f"Stake address was not registered yet: {stake_addr_info}"

    return tx_raw_output_reg, plutus_costs_reg


def delegate_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    txins: list[clusterlib.UTXOData],
    collaterals: list[clusterlib.UTXOData],
    pool_user: delegation.PoolUserScript,
    pool_id: str,
    redeemer_file: pl.Path,
    reference_script_utxos: list[clusterlib.UTXOData] | None,
    build_method: str,
) -> tuple[clusterlib.TxRawOutput, list[dict]]:
    """Delegate a stake address to a pool."""
    temp_template = f"{temp_template}_deleg"

    # Create stake address delegation cert
    stake_addr_deleg_cert_file = cluster_obj.g_stake_address.gen_stake_addr_delegation_cert(
        addr_name=f"{temp_template}_addr0",
        stake_script_file=pool_user.stake.script_file,
        stake_pool_id=pool_id,
    )

    src_init_balance = cluster_obj.g_query.get_address_balance(pool_user.payment.address)

    deleg_cert_script = clusterlib.ComplexCert(
        certificate_file=stake_addr_deleg_cert_file,
        script_file=pool_user.stake.script_file if not reference_script_utxos else "",
        reference_txin=reference_script_utxos[0] if reference_script_utxos else None,
        collaterals=collaterals,
        execution_units=(218855869, 686154),
        redeemer_file=redeemer_file,
    )

    tx_files = clusterlib.TxFiles(signing_key_files=[pool_user.payment.skey_file])
    plutus_costs_deleg = []

    if build_method == clusterlib_utils.BuildMethods.BUILD:
        tx_raw_output_deleg = cluster_obj.g_transaction.build_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            txins=txins,
            tx_files=tx_files,
            complex_certs=[deleg_cert_script],
            fee_buffer=2_000_000,
            witness_override=len(tx_files.signing_key_files),
        )
        # Calculate cost of Plutus script
        plutus_costs_deleg = cluster_obj.g_transaction.calculate_plutus_script_cost(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            txins=txins,
            tx_files=tx_files,
            complex_certs=[deleg_cert_script],
            fee_buffer=2_000_000,
            witness_override=len(tx_files.signing_key_files),
        )
        tx_signed_deleg = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_raw_output_deleg.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster_obj.g_transaction.submit_tx(
            tx_file=tx_signed_deleg, txins=tx_raw_output_deleg.txins
        )
    else:
        tx_raw_output_deleg = cluster_obj.g_transaction.send_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            txins=txins,
            tx_files=tx_files,
            complex_certs=[deleg_cert_script],
            fee=300_000,
        )

    # Check that the balance for source address was correctly updated
    assert (
        cluster_obj.g_query.get_address_balance(pool_user.payment.address)
        == src_init_balance - tx_raw_output_deleg.fee
    ), f"Incorrect balance for source address `{pool_user.payment.address}`"

    # Check that the stake address was delegated
    stake_addr_info = cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address)
    assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"
    assert pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

    return tx_raw_output_deleg, plutus_costs_deleg


def deregister_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    txins: list[clusterlib.UTXOData],
    collaterals: list[clusterlib.UTXOData],
    pool_user: delegation.PoolUserScript,
    redeemer_file: pl.Path,
    reference_script_utxos: list[clusterlib.UTXOData] | None,
    build_method: str,
) -> tuple[clusterlib.TxRawOutput, list[dict]]:
    """Deregister stake address."""
    temp_template = f"{temp_template}_dereg_withdraw"

    src_payment_balance = cluster_obj.g_query.get_address_balance(pool_user.payment.address)
    reward_balance = cluster_obj.g_query.get_stake_addr_info(
        pool_user.stake.address
    ).reward_account_balance

    # Create stake address deregistration cert
    stake_addr_dereg_cert = cluster_obj.g_stake_address.gen_stake_addr_deregistration_cert(
        addr_name=f"{temp_template}_addr0",
        deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster_obj),
        stake_script_file=pool_user.stake.script_file,
    )

    # Withdraw rewards to payment address, deregister stake address
    withdrawal_script = clusterlib.ScriptWithdrawal(
        txout=clusterlib.TxOut(address=pool_user.stake.address, amount=-1),
        script_file=pool_user.stake.script_file if not reference_script_utxos else "",
        reference_txin=reference_script_utxos[0] if reference_script_utxos else None,
        collaterals=[collaterals[0]],
        execution_units=(213184888, 670528),
        redeemer_file=redeemer_file,
    )
    dereg_cert_script = clusterlib.ComplexCert(
        certificate_file=stake_addr_dereg_cert,
        script_file=pool_user.stake.script_file if not reference_script_utxos else "",
        reference_txin=reference_script_utxos[0] if reference_script_utxos else None,
        collaterals=[collaterals[1]],
        execution_units=(218855869, 686154),
        redeemer_file=redeemer_file,
    )

    tx_files = clusterlib.TxFiles(signing_key_files=[pool_user.payment.skey_file])

    plutus_costs = []

    if build_method == clusterlib_utils.BuildMethods.BUILD:
        tx_raw_output = cluster_obj.g_transaction.build_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            txins=txins,
            tx_files=tx_files,
            complex_certs=[dereg_cert_script],
            fee_buffer=2_000_000,
            script_withdrawals=[withdrawal_script],
            witness_override=len(tx_files.signing_key_files),
        )
        # Calculate cost of Plutus script
        plutus_costs = cluster_obj.g_transaction.calculate_plutus_script_cost(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            txins=txins,
            tx_files=tx_files,
            complex_certs=[dereg_cert_script],
            fee_buffer=2_000_000,
            script_withdrawals=[withdrawal_script],
            witness_override=len(tx_files.signing_key_files),
        )
        tx_signed = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        cluster_obj.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
    else:
        tx_raw_output = cluster_obj.g_transaction.send_tx(
            src_address=pool_user.payment.address,
            tx_name=temp_template,
            txins=txins,
            tx_files=tx_files,
            complex_certs=[dereg_cert_script],
            script_withdrawals=[withdrawal_script],
            fee=400_000,
        )

    # Check that the key deposit was returned and rewards withdrawn
    assert (
        cluster_obj.g_query.get_address_balance(pool_user.payment.address)
        == src_payment_balance
        - tx_raw_output.fee
        + reward_balance
        + cluster_obj.g_query.get_address_deposit()
    ), f"Incorrect balance for source address `{pool_user.payment.address}`"

    # Check that the stake address is no longer delegated
    stake_addr_info = cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address)
    assert not stake_addr_info.delegation, f"Stake address is still delegated: {stake_addr_info}"

    tx_db_dereg = dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)
    if tx_db_dereg and build_method == clusterlib_utils.BuildMethods.BUILD:
        assert pool_user.stake.address in tx_db_dereg.stake_deregistration

        # Compare cost of Plutus script with data from db-sync

        dbsync_utils.check_plutus_costs(
            redeemer_records=tx_db_dereg.redeemers, cost_records=plutus_costs
        )

    return tx_raw_output, plutus_costs


# Don't run these tests on testnets as a stake address corresponding to the Plutus script
# might be already in use.
@common.SKIPIF_BUILD_UNUSABLE
class TestRegisterAddr:
    """Tests for address registration."""

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.dbsync
    def test_register_deregister(
        self,
        cluster_lock_42stake: tuple[clusterlib.ClusterLib, str],
        pool_user: delegation.PoolUserScript,
        plutus_version: str,
        build_method: str,
    ):
        """Register and deregister Plutus script stake address.

        * Submit registration certificate for a stake address
        * Check that the stake address was registered
        * Deregister the stake address
        * Check that the key deposit was returned and rewards withdrawn
        * Check that the stake address is no longer registered
        * (optional) Check records in db-sync
        """
        # The test checks the following issues:
        #  - cardano-cli 299: Cannot de-register Plutus script stake address

        __: tp.Any  # mypy workaround
        cluster, __ = cluster_lock_42stake
        temp_template = common.get_test_id(cluster)

        collateral_fund_reg = 1_500_000_000
        collateral_fund_withdraw = 1_500_000_000
        collateral_fund_dereg = 1_500_000_000
        reg_fund = 1_500_000_000
        dereg_fund = 1_500_000_000

        if cluster.g_query.get_stake_addr_info(pool_user.stake.address):
            pytest.skip(
                f"The Plutus script stake address '{pool_user.stake.address}' is already "
                "registered, cannot continue."
            )

        # Step 1: create Tx inputs for step 2 and step 3
        txouts_step1 = [
            # For collateral
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_reg),
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_withdraw),
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_dereg),
            # For registration
            clusterlib.TxOut(address=pool_user.payment.address, amount=reg_fund),
            # For deregistration
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
        tx_output_step1 = cluster.g_transaction.build_tx(
            src_address=pool_user.payment.address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            txouts=txouts_step1,
            fee_buffer=2_000_000,
            # Don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        step1_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_step1)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=step1_utxos, txouts=tx_output_step1.txouts
        )
        collateral_reg = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset)
        collateral_withdraw = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 1)
        collateral_dereg = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 2)
        reg_utxos = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 3)
        dereg_utxos = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 4)

        reference_script_utxos = (
            clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 5)
            if plutus_version == "v2"
            else None
        )

        # Step 2: register a stake address

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        tx_raw_output_reg, plutus_costs_reg = register_stake_addr(
            cluster_obj=cluster,
            temp_template=temp_template,
            txins=reg_utxos,
            collaterals=collateral_reg,
            pool_user=pool_user,
            redeemer_file=plutus_common.REDEEMER_42,
            reference_script_utxos=reference_script_utxos,
            build_method=build_method,
        )

        tx_db_record_reg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_raw_output_reg
        )

        # Step 3: withdraw rewards and deregister

        # Make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # Submit deregistration certificate and withdraw rewards
        tx_raw_output_dereg, __ = deregister_stake_addr(
            cluster_obj=cluster,
            temp_template=temp_template,
            txins=dereg_utxos,
            collaterals=[*collateral_withdraw, *collateral_dereg],
            pool_user=pool_user,
            redeemer_file=plutus_common.REDEEMER_42,
            reference_script_utxos=reference_script_utxos,
            build_method=(
                clusterlib_utils.BuildMethods.BUILD
                if VERSIONS.cli != CLI_WITH_ISSUE_942
                else clusterlib_utils.BuildMethods.BUILD_RAW
            ),
        )

        # Check tx_view of step 2 and step 3
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_reg)
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_dereg)

        # Compare cost of Plutus script with data from db-sync
        if tx_db_record_reg and build_method == clusterlib_utils.BuildMethods.BUILD:
            dbsync_utils.check_plutus_costs(
                redeemer_records=tx_db_record_reg.redeemers, cost_records=plutus_costs_reg
            )


# Don't run these tests on testnets as a stake address corresponding to the Plutus script
# might be already in use.
class TestDelegateAddr:
    """Tests for address delegation to stake pools."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(8)
    @pytest.mark.parametrize(
        "use_reference_script", (True, False), ids=("reference_script", "script_file")
    )
    @common.PARAM_PLUTUS_VERSION
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.long
    @pytest.mark.dbsync
    def test_delegate_deregister(  # noqa: C901
        self,
        cluster_lock_42stake: tuple[clusterlib.ClusterLib, str],
        pool_user: delegation.PoolUserScript,
        plutus_version: str,
        build_method: str,
        use_reference_script: bool,
    ):
        """Register, delegate and deregister Plutus script stake address.

        The stake address registration and delegation happen in single transaction.

        * Submit registration certificate and delegate stake address to pool
        * Check that the stake address was delegated
        * Withdraw rewards to payment address and deregister stake address
        * Check that the key deposit was returned and rewards withdrawn
        * Check that the stake address is no longer delegated
        * (optional) Check records in db-sync
        """
        cluster, pool_id = cluster_lock_42stake
        temp_template = common.get_test_id(cluster)

        if plutus_version == "v1" and use_reference_script:
            pytest.skip("PlutusV1 doesn't support reference scripts")

        collateral_fund_deleg = 1_500_000_000
        collateral_fund_withdraw = 1_500_000_000
        collateral_fund_dereg = 1_500_000_000
        deleg_fund = 1_500_000_000
        dereg_fund = 1_500_000_000

        if cluster.g_query.get_stake_addr_info(pool_user.stake.address):
            pytest.skip(
                f"The Plutus script stake address '{pool_user.stake.address}' is already "
                "registered, cannot continue."
            )

        # Step 1: create Tx inputs for step 2 and step 3
        txouts_step1 = [
            # For collateral
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_deleg),
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_withdraw),
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_dereg),
            # For delegation and registration
            clusterlib.TxOut(address=pool_user.payment.address, amount=deleg_fund),
            # For deregistration
            clusterlib.TxOut(address=pool_user.payment.address, amount=dereg_fund),
        ]

        if use_reference_script:
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
        tx_output_step1 = cluster.g_transaction.build_tx(
            src_address=pool_user.payment.address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            txouts=txouts_step1,
            fee_buffer=2_000_000,
            # Don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        step1_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_step1)
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
            if use_reference_script
            else None
        )

        # Step 2: register and delegate

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # Submit registration certificate and delegate to pool
        try:
            tx_delegation_out, plutus_cost_deleg = register_delegate_stake_addr(
                cluster_obj=cluster,
                temp_template=temp_template,
                txins=deleg_utxos,
                collaterals=collateral_deleg,
                pool_user=pool_user,
                pool_id=pool_id,
                redeemer_file=plutus_common.REDEEMER_42,
                reference_script_utxos=reference_script_utxos,
                build_method=build_method,
            )
        except clusterlib.CLIError as exc:
            str_exc = str(exc)
            if (
                # Old cardano-cli 297 issue
                "(MissingScriptWitnessesUTXOW" in str_exc
                or "(MissingRedeemers" in str_exc
                # New cardano-cli 297 issue
                or "(ExtraRedeemers" in str_exc
                or "points to a script hash that is not known" in str_exc
            ):
                issues.cli_297.finish_test()

            if (
                build_method == clusterlib_utils.BuildMethods.BUILD
                and "overspent budget" in str_exc
                and VERSIONS.transaction_era >= VERSIONS.CONWAY
                and VERSIONS.cli >= version.parse("10.2.0.0")
            ):
                issues.cli_1023.finish_test()
            elif (
                build_method == clusterlib_utils.BuildMethods.BUILD
                and "overspent budget" in str_exc
                and VERSIONS.transaction_era >= VERSIONS.CONWAY
            ):
                issues.cli_650.finish_test()
            raise

        assert cluster.g_query.get_epoch() == init_epoch, (
            "Delegation took longer than expected and would affect other checks"
        )

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_delegation_out)
        delegation.db_check_delegation(
            pool_user=pool_user,
            db_record=tx_db_record,
            deleg_epoch=init_epoch,
            pool_id=pool_id,
        )

        # Step 3: withdraw rewards and deregister

        reward_error = ""

        # To speed up test run, skip waiting for rewards in selected scenarios
        skip_rewards_check = (
            use_reference_script and build_method != clusterlib_utils.BuildMethods.BUILD
        ) or (not use_reference_script and build_method == clusterlib_utils.BuildMethods.BUILD)

        if not skip_rewards_check:
            LOGGER.info("Waiting 4 epochs for first reward.")
            cluster.wait_for_epoch(epoch_no=init_epoch + 4, padding_seconds=10)
            if not cluster.g_query.get_stake_addr_info(
                pool_user.stake.address
            ).reward_account_balance:
                reward_error = f"User of pool '{pool_id}' hasn't received any rewards."

        # Make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # Submit deregistration certificate and withdraw rewards
        try:
            tx_deregister_out, __ = deregister_stake_addr(
                cluster_obj=cluster,
                temp_template=temp_template,
                txins=dereg_utxos,
                collaterals=[*collateral_withdraw, *collateral_dereg],
                pool_user=pool_user,
                redeemer_file=plutus_common.REDEEMER_42,
                reference_script_utxos=reference_script_utxos,
                build_method=(
                    clusterlib_utils.BuildMethods.BUILD
                    if VERSIONS.cli != CLI_WITH_ISSUE_942
                    else clusterlib_utils.BuildMethods.BUILD_RAW
                ),
            )
        except clusterlib.CLIError as exc:
            if "(MissingRedeemers" in str(exc):
                issues.cli_299.finish_test()
            raise

        if reward_error:
            raise AssertionError(reward_error)

        # Check tx_view of step 2 and step 3
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_delegation_out)
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_deregister_out)

        # Compare cost of Plutus script with data from db-sync
        if tx_db_record and plutus_cost_deleg:
            try:
                dbsync_utils.check_plutus_costs(
                    redeemer_records=tx_db_record.redeemers, cost_records=plutus_cost_deleg
                )
            except AssertionError as exc:
                if (
                    VERSIONS.transaction_era >= VERSIONS.CONWAY
                    and len(tx_db_record.redeemers) == 2
                    and tx_db_record.redeemers[0].unit_steps == tx_db_record.redeemers[1].unit_steps
                    and "space:" in str(exc)
                ):
                    issues.dbsync_1825.finish_test()
                raise

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(8)
    @common.PARAM_PLUTUS_VERSION
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.long
    @pytest.mark.dbsync
    def test_register_delegate_deregister(
        self,
        cluster_lock_42stake: tuple[clusterlib.ClusterLib, str],
        pool_user: delegation.PoolUserScript,
        plutus_version: str,
        build_method: str,
    ):
        """Register, delegate and deregister Plutus script stake address.

        The stake address registration and delegation happen in two separate transactions.

        * Submit registration certificate for a stake address
        * Delegate stake address to pool
        * Check that the stake address was delegated
        * Withdraw rewards to payment address and deregister stake address
        * Check that the key deposit was returned and rewards withdrawn
        * Check that the stake address is no longer delegated
        * (optional) Check records in db-sync
        """
        # The test checks the following issues:
        #  - cardano-cli 297: Cannot delegate Plutus script stake address
        #  - cardano-cli 299: Cannot de-register Plutus script stake address

        cluster, pool_id = cluster_lock_42stake
        temp_template = common.get_test_id(cluster)

        collateral_fund_reg = 1_500_000_000
        collateral_fund_deleg = 1_500_000_000
        collateral_fund_withdraw = 1_500_000_000
        collateral_fund_dereg = 1_500_000_000
        reg_fund = 1_500_000_000
        deleg_fund = 1_500_000_000
        dereg_fund = 1_500_000_000

        if cluster.g_query.get_stake_addr_info(pool_user.stake.address):
            pytest.skip(
                f"The Plutus script stake address '{pool_user.stake.address}' is already "
                "registered, cannot continue."
            )

        # Step 1: create Tx inputs for step 2 and step 3
        txouts_step1 = [
            # For collateral
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_reg),
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_deleg),
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_withdraw),
            clusterlib.TxOut(address=pool_user.payment.address, amount=collateral_fund_dereg),
            # For registration
            clusterlib.TxOut(address=pool_user.payment.address, amount=reg_fund),
            # For delegation
            clusterlib.TxOut(address=pool_user.payment.address, amount=deleg_fund),
            # For deregistration
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
        tx_output_step1 = cluster.g_transaction.build_tx(
            src_address=pool_user.payment.address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            txouts=txouts_step1,
            fee_buffer=2_000_000,
            # Don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        step1_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_step1)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=step1_utxos, txouts=tx_output_step1.txouts
        )
        collateral_reg = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset)
        collateral_deleg = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 1)
        collateral_withdraw = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 2)
        collateral_dereg = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 3)
        reg_utxos = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 4)
        deleg_utxos = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 5)
        dereg_utxos = clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 6)

        reference_script_utxos = (
            clusterlib.filter_utxos(utxos=step1_utxos, utxo_ix=utxo_ix_offset + 7)
            if plutus_version == "v2"
            else None
        )

        # Step 2: register and delegate

        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        init_epoch = cluster.g_query.get_epoch()

        # Register a stake address
        tx_raw_output_reg, plutus_costs_reg = register_stake_addr(
            cluster_obj=cluster,
            temp_template=temp_template,
            txins=reg_utxos,
            collaterals=collateral_reg,
            pool_user=pool_user,
            redeemer_file=plutus_common.REDEEMER_42,
            reference_script_utxos=reference_script_utxos,
            build_method=build_method,
        )

        # Delegate a stake address
        delegated = False
        try:
            (
                tx_raw_output_deleg,
                plutus_costs_deleg,
            ) = delegate_stake_addr(
                cluster_obj=cluster,
                temp_template=temp_template,
                txins=deleg_utxos,
                collaterals=collateral_deleg,
                pool_user=pool_user,
                pool_id=pool_id,
                redeemer_file=plutus_common.REDEEMER_42,
                reference_script_utxos=reference_script_utxos,
                build_method=build_method,
            )

            assert cluster.g_query.get_epoch() == init_epoch, (
                "Delegation took longer than expected and would affect other checks"
            )

            delegated = True
        finally:
            # Cleanup on failure: deregister stake address if it was registered
            if not delegated and cluster.g_query.get_stake_addr_info(pool_user.stake.address):
                deregister_stake_addr(
                    cluster_obj=cluster,
                    temp_template=temp_template,
                    txins=dereg_utxos,
                    collaterals=[*collateral_withdraw, *collateral_dereg],
                    pool_user=pool_user,
                    redeemer_file=plutus_common.REDEEMER_42,
                    reference_script_utxos=reference_script_utxos,
                    build_method=build_method,
                )

        tx_db_record_reg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_raw_output_reg
        )
        tx_db_record_deleg = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_raw_output_deleg
        )
        delegation.db_check_delegation(
            pool_user=pool_user,
            db_record=tx_db_record_deleg,
            deleg_epoch=init_epoch,
            pool_id=pool_id,
            check_registration=False,
        )

        # Step 3: withdraw rewards and deregister

        reward_error = ""

        LOGGER.info("Waiting 4 epochs for first reward.")
        cluster.wait_for_epoch(epoch_no=init_epoch + 4, padding_seconds=10)
        if not cluster.g_query.get_stake_addr_info(pool_user.stake.address).reward_account_balance:
            reward_error = f"User of pool '{pool_id}' hasn't received any rewards."

        # Make sure we have enough time to finish deregistration in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        # Submit deregistration certificate and withdraw rewards
        tx_raw_output_dereg, __ = deregister_stake_addr(
            cluster_obj=cluster,
            temp_template=temp_template,
            txins=dereg_utxos,
            collaterals=[*collateral_withdraw, *collateral_dereg],
            pool_user=pool_user,
            redeemer_file=plutus_common.REDEEMER_42,
            reference_script_utxos=reference_script_utxos,
            build_method=(
                clusterlib_utils.BuildMethods.BUILD
                if VERSIONS.cli != CLI_WITH_ISSUE_942
                else clusterlib_utils.BuildMethods.BUILD_RAW
            ),
        )

        if reward_error:
            raise AssertionError(reward_error)

        # Check tx_view of step 2 and step 3
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_reg)
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_deleg)
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_dereg)

        # Compare cost of Plutus script with data from db-sync
        if tx_db_record_reg and build_method == clusterlib_utils.BuildMethods.BUILD:
            dbsync_utils.check_plutus_costs(
                redeemer_records=tx_db_record_reg.redeemers, cost_records=plutus_costs_reg
            )
        if tx_db_record_deleg and build_method == clusterlib_utils.BuildMethods.BUILD:
            dbsync_utils.check_plutus_costs(
                redeemer_records=tx_db_record_deleg.redeemers, cost_records=plutus_costs_deleg
            )
