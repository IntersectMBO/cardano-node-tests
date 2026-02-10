"""Tests for spending with Plutus using `transaction build`."""

import dataclasses
import logging
import pathlib as pl
import shutil
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import spend_build
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_BUILD_UNUSABLE,
    common.SKIPIF_PLUTUS_UNUSABLE,
    pytest.mark.plutus,
]


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    addrs = common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=3,
        fund_idx=[0],
        amount=1_000_000_000,
    )
    return addrs


@pytest.fixture
def pool_users(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.PoolUser]:
    """Create new pool users."""
    created_users = common.get_pool_users(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=2,
        fund_idx=[0],
        amount=3_000_000_000,
    )
    return created_users


class TestBuildLocking:
    """Tests for Tx output locking using Plutus smart contracts and `transaction build`."""

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS3_VERSION
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_txout_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Corresponds to Exercise 3 for Alonzo Blue.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, tx_output_fund = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=1_000_000,
        )

        __, tx_output, plutus_costs = spend_build._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=-1,
        )

        # Check expected fees
        expected_fee_fund = 168_845
        assert common.is_fee_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        expected_fee = 170_782
        assert tx_output and common.is_fee_in_interval(tx_output.fee, expected_fee, frac=0.15)

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost],
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not shutil.which("create-script-context"),
        reason="cannot find `create-script-context` on the PATH",
    )
    @common.SKIPIF_MISMATCHED_ERAS
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_context_equivalence(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Test context equivalence while spending a locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * generate a dummy redeemer and a dummy Tx
        * derive the correct redeemer from the dummy Tx
        * spend the locked UTxO using the derived redeemer
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)
        deposit_amount = cluster.g_query.get_address_deposit()

        # Create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr2",
            deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster),
            stake_vkey_file=pool_users[0].stake.vkey_file,
        )

        tx_files = clusterlib.TxFiles(certificate_files=[stake_addr_reg_cert_file])

        # Generate a dummy redeemer in order to create a txbody from which
        # we can generate a tx and then derive the correct redeemer
        redeemer_file_dummy = pl.Path(f"{temp_template}_dummy_script_context.redeemer")
        clusterlib_utils.create_script_context(
            cluster_obj=cluster, plutus_version=1, redeemer_file=redeemer_file_dummy
        )

        plutus_op_dummy = plutus_common.PlutusOp(
            script_file=plutus_common.CONTEXT_EQUIVALENCE_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=redeemer_file_dummy,
            execution_cost=plutus_common.CONTEXT_EQUIVALENCE_COST,
        )

        # Fund the script address
        script_utxos, collateral_utxos, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
            plutus_op=plutus_op_dummy,
            amount=1_000_000,
        )

        invalid_hereafter = cluster.g_query.get_slot_no() + 200

        __, tx_output_dummy, __ = spend_build._build_spend_locked_txin(
            temp_template=f"{temp_template}_dummy",
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op_dummy,
            amount=-1,
            deposit_amount=deposit_amount,
            tx_files=tx_files,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
            script_valid=False,
            submit_tx=False,
        )
        assert tx_output_dummy

        # Generate the "real" redeemer
        redeemer_file = pl.Path(f"{temp_template}_script_context.redeemer")

        plutus_common.create_script_context_w_blockers(
            cluster_obj=cluster,
            plutus_version=1,
            redeemer_file=redeemer_file,
            tx_file=tx_output_dummy.out_file,
        )

        plutus_op = dataclasses.replace(plutus_op_dummy, redeemer_file=redeemer_file)

        __, tx_output, __ = spend_build._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=-1,
            deposit_amount=deposit_amount,
            tx_files=tx_files,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
        )

        # Check expected fees
        if tx_output:
            expected_fee = 372_438
            assert common.is_fee_in_interval(tx_output.fee, expected_fee, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("embed_datum", (True, False), ids=("embedded_datum", "datum"))
    @pytest.mark.parametrize(
        "variant",
        ("typed_json", "typed_cbor", "untyped_value", "untyped_json", "untyped_cbor"),
    )
    @common.PARAM_PLUTUS_VERSION
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_guessing_game(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        variant: str,
        plutus_version: str,
        embed_datum: bool,
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with "guessing game" scripts that expect specific datum and redeemer value.
        Test both typed and untyped redeemer and datum.
        Test passing datum and redeemer to `cardano-cli` as value, json file and cbor file.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        datum_file: pl.Path | None = None
        datum_cbor_file: pl.Path | None = None
        datum_value: str | None = None
        redeemer_file: pl.Path | None = None
        redeemer_cbor_file: pl.Path | None = None
        redeemer_value: str | None = None

        if variant == "typed_json":
            script_file = plutus_common.GUESSING_GAME[plutus_version].script_file
            datum_file = plutus_common.DATUM_42_TYPED
            redeemer_file = plutus_common.REDEEMER_42_TYPED
        elif variant == "typed_cbor":
            script_file = plutus_common.GUESSING_GAME[plutus_version].script_file
            datum_cbor_file = plutus_common.DATUM_42_TYPED_CBOR
            redeemer_cbor_file = plutus_common.REDEEMER_42_TYPED_CBOR
        elif variant == "untyped_value":
            script_file = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file
            datum_value = "42"
            redeemer_value = "42"
        elif variant == "untyped_json":
            script_file = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file
            datum_file = plutus_common.DATUM_42
            redeemer_file = plutus_common.REDEEMER_42
        elif variant == "untyped_cbor":
            script_file = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file
            datum_cbor_file = plutus_common.DATUM_42_CBOR
            redeemer_cbor_file = plutus_common.REDEEMER_42_CBOR
        else:
            msg = "Unknown test variant."
            raise AssertionError(msg)

        execution_cost = plutus_common.GUESSING_GAME[plutus_version].execution_cost
        if script_file == plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file:
            execution_cost = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost

        plutus_op = plutus_common.PlutusOp(
            script_file=script_file,
            datum_file=datum_file,
            datum_cbor_file=datum_cbor_file,
            datum_value=datum_value,
            redeemer_file=redeemer_file,
            redeemer_cbor_file=redeemer_cbor_file,
            redeemer_value=redeemer_value,
            execution_cost=execution_cost,
        )

        script_utxos, collateral_utxos, tx_output_fund = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            embed_datum=embed_datum,
            amount=1_000_000,
        )

        __, __, plutus_costs = spend_build._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=-1,
        )

        # Check expected fees
        expected_fee_fund = 168_845
        assert common.is_fee_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[execution_cost],
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "plutus_version",
        (
            "plutus_v1",
            pytest.param("mix_v1_v2", marks=common.SKIPIF_PLUTUSV2_UNUSABLE),
            pytest.param("mix_v2_v1", marks=common.SKIPIF_PLUTUSV2_UNUSABLE),
            pytest.param("plutus_v2", marks=common.SKIPIF_PLUTUSV2_UNUSABLE),
        ),
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_two_scripts_spending(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking two Tx outputs with two different Plutus scripts in single Tx.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        script_fund = 1_000_000

        protocol_params = cluster.g_query.get_protocol_params()

        script_file1_v1 = plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V1
        execution_cost1_v1 = plutus_common.ALWAYS_SUCCEEDS_COST
        script_file2_v1 = plutus_common.GUESSING_GAME_PLUTUS_V1
        # This is higher than `plutus_common.GUESSING_GAME_COST`, because the script
        # context has changed to include more stuff
        execution_cost2_v1 = plutus_common.ExecutionCost(
            per_time=325_969_144, per_space=1_194_986, fixed_cost=92_454
        )

        script_file1_v2 = plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2
        execution_cost1_v2 = plutus_common.ALWAYS_SUCCEEDS_V2_COST
        script_file2_v2 = plutus_common.GUESSING_GAME_PLUTUS_V2
        execution_cost2_v2 = plutus_common.ExecutionCost(
            per_time=239_699_145, per_space=662_274, fixed_cost=53_233
        )

        expected_fee_fund = 174_389
        if plutus_version == "plutus_v1":
            script_file1 = script_file1_v1
            execution_cost1 = execution_cost1_v1
            script_file2 = script_file2_v1
            execution_cost2 = execution_cost2_v1
            expected_fee_redeem = 378_768
        elif plutus_version == "mix_v1_v2":
            script_file1 = script_file1_v1
            execution_cost1 = execution_cost1_v1
            script_file2 = script_file2_v2
            execution_cost2 = execution_cost2_v2
            expected_fee_redeem = 321_739
        elif plutus_version == "mix_v2_v1":
            script_file1 = script_file1_v2
            execution_cost1 = execution_cost1_v2
            script_file2 = script_file2_v1
            execution_cost2 = execution_cost2_v1
            expected_fee_redeem = 378_584
        elif plutus_version == "plutus_v2":
            script_file1 = script_file1_v2
            execution_cost1 = execution_cost1_v2
            script_file2 = script_file2_v2
            execution_cost2 = execution_cost2_v2
            expected_fee_redeem = 321_378
        else:
            msg = "Unknown test variant."
            raise AssertionError(msg)

        plutus_op1 = plutus_common.PlutusOp(
            script_file=script_file1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=execution_cost1,
        )
        plutus_op2 = plutus_common.PlutusOp(
            script_file=script_file2,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_TYPED_CBOR,
            execution_cost=execution_cost2,
        )

        # Step 1: fund the Plutus scripts

        assert plutus_op1.execution_cost and plutus_op2.execution_cost  # for mypy

        script_address1 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )
        redeem_cost1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost, protocol_params=protocol_params
        )
        datum_hash1 = cluster.g_transaction.get_hash_script_data(
            script_data_file=plutus_op1.datum_file
        )

        script_address2 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )
        redeem_cost2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost, protocol_params=protocol_params
        )
        datum_hash2 = cluster.g_transaction.get_hash_script_data(
            script_data_file=plutus_op2.datum_file
        )

        # Create a Tx output with a datum hash at the script address

        tx_files_fund = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )
        txouts_fund = [
            clusterlib.TxOut(
                address=script_address1,
                amount=script_fund,
                datum_hash=datum_hash1,
            ),
            clusterlib.TxOut(
                address=script_address2,
                amount=script_fund,
                datum_hash=datum_hash2,
            ),
            # For collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost2.collateral),
        ]
        tx_output_fund = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_fund,
            txouts=txouts_fund,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed_fund = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_fund.out_file,
            signing_key_files=tx_files_fund.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )

        cluster.g_transaction.submit_tx(tx_file=tx_signed_fund, txins=tx_output_fund.txins)

        fund_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_fund)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output_fund.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)

        assert script_utxos1 and script_utxos2, "No script UTxOs"
        assert collateral_utxos1 and collateral_utxos2, "No collateral UTxOs"

        assert script_utxos1[0].amount == script_fund, (
            f"Incorrect balance for script address `{script_utxos1[0].address}`"
        )
        assert script_utxos2[0].amount == script_fund, (
            f"Incorrect balance for script address `{script_utxos2[0].address}`"
        )

        # Step 2: spend the "locked" UTxOs

        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos1,
                script_file=plutus_op1.script_file,
                collaterals=collateral_utxos1,
                datum_file=plutus_op1.datum_file,
                redeemer_cbor_file=plutus_op1.redeemer_cbor_file,
            ),
            clusterlib.ScriptTxIn(
                txins=script_utxos2,
                script_file=plutus_op2.script_file,
                collaterals=collateral_utxos2,
                datum_file=plutus_op2.datum_file,
                redeemer_cbor_file=plutus_op2.redeemer_cbor_file,
            ),
        ]
        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file, payment_addrs[1].skey_file],
        )
        fee_txin_redeem = next(
            r
            for r in clusterlib_utils.get_just_lovelace_utxos(
                address_utxos=cluster.g_query.get_utxo(address=payment_addrs[0].address)
            )
            if r.amount >= 100_000_000
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=script_fund * 2),
        ]
        tx_output_redeem = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            txins=[fee_txin_redeem],
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
            change_address=payment_addrs[0].address,
        )

        # Calculate cost of Plutus script
        plutus_costs = cluster.g_transaction.calculate_plutus_script_cost(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            txins=[fee_txin_redeem],
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
            change_address=payment_addrs[0].address,
        )

        tx_signed_redeem = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        dst_init_balance = cluster.g_query.get_address_balance(payment_addrs[1].address)

        cluster.g_transaction.submit_tx(
            tx_file=tx_signed_redeem,
            txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
        )

        assert (
            cluster.g_query.get_address_balance(payment_addrs[1].address)
            == dst_init_balance + script_fund * 2
        ), f"Incorrect balance for destination address `{payment_addrs[1].address}`"

        script_utxos_lovelace = [
            u for u in [*script_utxos1, *script_utxos2] if u.coin == clusterlib.DEFAULT_COIN
        ]
        for u in script_utxos_lovelace:
            assert not cluster.g_query.get_utxo(utxo=u, coins=[clusterlib.DEFAULT_COIN]), (
                f"Inputs were NOT spent for `{u.address}`"
            )

        # Check expected fees
        assert common.is_fee_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)
        assert common.is_fee_in_interval(tx_output_redeem.fee, expected_fee_redeem, frac=0.15)

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[execution_cost1, execution_cost2],
        )

        # Check tx view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

        # Check transactions in db-sync
        tx_redeem_record = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_output_redeem
        )
        if tx_redeem_record:
            dbsync_utils.check_plutus_costs(
                redeemer_records=tx_redeem_record.redeemers, cost_records=plutus_costs
            )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS3_VERSION
    @pytest.mark.smoke
    def test_always_fails(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with "always fails" script that fails for all datum / redeemer values.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the expected error was raised
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_v_record = plutus_common.ALWAYS_FAILS[plutus_version]

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_v_record.script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_v_record.execution_cost,
        )

        script_utxos, collateral_utxos, tx_output_fund = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=1_000_000,
        )

        err, __, __ = spend_build._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=-1,
            expect_failure=True,
        )
        assert (
            "following scripts have execution failures" in err  # In cli 10.1.1.0+
            or "The Plutus script evaluation failed" in err
        ), err

        # Check expected fees
        expected_fee_fund = 168_845
        assert common.is_fee_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS3_VERSION
    @pytest.mark.smoke
    def test_script_invalid(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test failing script together with the `--script-invalid` argument - collateral is taken.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with "always fails" script that fails for all datum / redeemer values.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was spent
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_v_record = plutus_common.ALWAYS_FAILS[plutus_version]

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_v_record.script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_v_record.execution_cost,
        )

        script_utxos, collateral_utxos, tx_output_fund = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=1_000_000,
        )

        try:
            __, tx_output, __ = spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=-1,
                script_valid=False,
            )
        except clusterlib.CLIError as exc:
            if "(ValidationTagMismatch" in str(exc):
                issues.consensus_947.finish_test()
            raise

        # Check expected fees
        expected_fee_fund = 168_845
        assert common.is_fee_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        if tx_output:
            expected_fee = 171_309
            assert common.is_fee_in_interval(tx_output.fee, expected_fee, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS3_VERSION
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_txout_token_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output that contains native tokens with a datum hash at the script address
        * check that expected amounts of Lovelace and native tokens were locked at the script
          address
        * spend the locked UTxO
        * check that the expected amounts of Lovelace and native tokens were spent
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        token_rand = clusterlib.get_rand_str(5)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode().hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addrs[0],
            issuer_addr=payment_addrs[0],
            amount=100,
        )
        tokens_rec = [clusterlib_utils.Token(coin=t.token, amount=t.amount) for t in tokens]

        script_utxos, collateral_utxos, tx_output_fund = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            tokens=tokens_rec,
            amount=5_000_000,
        )

        __, tx_output_spend, plutus_costs = spend_build._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=-1,
            tokens=tokens_rec,
        )

        # Check expected fees
        expected_fee_fund = 173_597
        assert common.is_fee_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        expected_fee = 175_710
        assert tx_output_spend and common.is_fee_in_interval(
            tx_output_spend.fee, expected_fee, frac=0.15
        )

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost],
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS3_VERSION
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_partial_spending(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test spending part of funds (Lovelace and native tokens) on a locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output that contains native tokens with a datum hash at the script address
        * check that expected amounts of Lovelace and native tokens were locked at the script
          address
        * spend the locked UTxO and create new locked UTxO with change
        * check that the expected amounts of Lovelace and native tokens were spent
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        token_rand = clusterlib.get_rand_str(5)

        amount = 5_000_000
        amount_spend = 2_000_000
        token_amount_fund = 100
        token_amount_spend = 20

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode().hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addrs[0],
            issuer_addr=payment_addrs[0],
            amount=token_amount_fund,
        )
        tokens_fund_rec = [clusterlib_utils.Token(coin=t.token, amount=t.amount) for t in tokens]

        script_utxos, collateral_utxos, tx_output_fund = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            tokens=tokens_fund_rec,
        )

        tokens_spend_rec = [
            clusterlib_utils.Token(coin=t.token, amount=token_amount_spend) for t in tokens
        ]

        __, tx_output_spend, plutus_costs = spend_build._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount_spend,
            tokens=tokens_spend_rec,
        )

        # Check that the expected amounts of Lovelace and native tokens were spent and change UTxOs
        # with appropriate datum hash were created

        assert tx_output_spend

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_spend)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=out_utxos, txouts=tx_output_spend.txouts
        )

        # UTxO we created for tokens and minimum required Lovelace
        change_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 1)
        # UTxO that was created by `build` command for rest of the Lovelace change (this will not
        # have the script's datum)
        # TODO: change UTxO used to be first, now it's last
        build_change_utxo = out_utxos[0] if utxo_ix_offset else out_utxos[-1]

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        # Lovelace balance on original script UTxOs
        script_lovelace_balance = clusterlib.calculate_utxos_balance(utxos=script_utxos)
        # Lovelace balance on additional txins
        fee_lovelace_balance = clusterlib.calculate_utxos_balance(utxos=tx_output_spend.txins)
        # Lovelace balance on change UTxOs
        change_lovelace_balance = clusterlib.calculate_utxos_balance(
            utxos=[*change_utxos, build_change_utxo]
        )

        assert (
            change_lovelace_balance
            == script_lovelace_balance + fee_lovelace_balance - tx_output_spend.fee - amount_spend
        )

        token_amount_exp = token_amount_fund - token_amount_spend
        assert len(change_utxos) == len(tokens_spend_rec) + 1
        for u in change_utxos:
            if u.coin != clusterlib.DEFAULT_COIN:
                assert u.amount == token_amount_exp
            assert u.datum_hash == script_utxos[0].datum_hash

        # Check expected fees
        expected_fee_fund = 173_597
        assert common.is_fee_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        expected_fee = 183_366
        assert tx_output_spend and common.is_fee_in_interval(
            tx_output_spend.fee, expected_fee, frac=0.15
        )

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost],
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS3_VERSION
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_collateral_is_txin(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test spending the locked UTxO while using single UTxO for both collateral and Tx input.

        Uses `cardano-cli transaction build` command for building the transactions.

        Tests bug https://github.com/IntersectMBO/cardano-db-sync/issues/750

        * create a Tx output with a datum hash at the script address and a collateral UTxO
        * check that the expected amount was locked at the script address
        * spend the locked UTxO while using the collateral UTxO both as collateral and as
          normal Tx input
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        # Step 1: fund the script address

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, tx_output_step1 = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
            amount=amount,
        )

        # Step 2: spend the "locked" UTxO

        script_address = script_utxos[0].address

        dst_step1_balance = cluster.g_query.get_address_balance(dst_addr.address)

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                datum_file=plutus_op.datum_file or "",
                datum_cbor_file=plutus_op.datum_cbor_file or "",
                redeemer_file=plutus_op.redeemer_file or "",
                redeemer_cbor_file=plutus_op.redeemer_cbor_file or "",
            )
        ]
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file, dst_addr.skey_file],
        )
        fee_txin = next(
            r
            for r in clusterlib_utils.get_just_lovelace_utxos(
                address_utxos=cluster.g_query.get_utxo(address=payment_addr.address)
            )
            if r.amount >= 100_000_000
        )
        txouts = [
            clusterlib.TxOut(address=dst_addr.address, amount=amount),
        ]

        tx_output_step2 = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files,
            # `collateral_utxos` is used both as collateral and as normal Tx input
            txins=[*collateral_utxos, fee_txin],
            txouts=txouts,
            script_txins=plutus_txins,
            change_address=payment_addr.address,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.g_transaction.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_step2.script_txins if t.txins]
        )

        assert (
            cluster.g_query.get_address_balance(dst_addr.address)
            == dst_step1_balance + amount - collateral_utxos[0].amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        for u in script_utxos:
            assert not cluster.g_query.get_utxo(utxo=u, coins=[clusterlib.DEFAULT_COIN]), (
                f"Inputs were NOT spent for `{script_address}`"
            )

        # Check expected fees
        expected_fee_step1 = 168_845
        assert common.is_fee_in_interval(tx_output_step1.fee, expected_fee_step1, frac=0.15)

        expected_fee_step2 = 176_986
        assert common.is_fee_in_interval(tx_output_step2.fee, expected_fee_step2, frac=0.15)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)
