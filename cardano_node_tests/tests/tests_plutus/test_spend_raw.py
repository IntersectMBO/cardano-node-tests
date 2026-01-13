"""Tests for spending with Plutus using `transaction build-raw`."""

import dataclasses
import itertools
import logging
import pathlib as pl
import shutil
import time
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import spend_raw
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = [
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
        amount=3_000_000_000,
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


def _check_pretty_utxo(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput
) -> str:
    """Check that pretty printed `query utxo` output looks as expected."""
    err = ""
    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

    utxo_out = (
        cluster_obj.cli(
            [
                "query",
                "utxo",
                "--tx-in",
                f"{txid}#0",
                "--output-text",
                *cluster_obj.magic_args,
            ]
        )
        .stdout.decode("utf-8")
        .split()
    )

    cluster_era = VERSIONS.cluster_era_name.title()
    datum_hash = clusterlib_utils.datum_hash_from_txout(
        cluster_obj=cluster_obj, txout=tx_raw_output.txouts[0]
    )
    expected_out = [
        "TxHash",
        "TxIx",
        "Amount",
        "--------------------------------------------------------------------------------------",
        txid,
        "0",
        str(tx_raw_output.txouts[0].amount),
        tx_raw_output.txouts[0].coin,
        "+",
        "TxOutDatumHash",
        f"AlonzoEraOnwards{cluster_era}"
        if VERSIONS.node > version.parse("8.1.2")
        else f"ScriptDataIn{cluster_era}Era",
        f'"{datum_hash}"',
    ]

    if utxo_out != expected_out:
        err = f"Pretty UTxO output doesn't match expected output:\n{utxo_out}\nvs\n{expected_out}"

    return err


class TestLocking:
    """Tests for Tx output locking using Plutus smart contracts."""

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

        Corresponds to Exercise 3 for Alonzo Blue.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 1_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, tx_output_fund = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )

        utxo_err = _check_pretty_utxo(cluster_obj=cluster, tx_raw_output=tx_output_fund)

        spend_raw._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
        )

        if utxo_err:
            pytest.fail(utxo_err)

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
        amount = 1_000_000
        deposit_amount = cluster.g_query.get_address_deposit()

        # Create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
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
        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
            plutus_op=plutus_op_dummy,
            amount=amount,
        )

        invalid_hereafter = cluster.g_query.get_slot_no() + 200

        __, tx_output_dummy = spend_raw._spend_locked_txin(
            temp_template=f"{temp_template}_dummy",
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op_dummy,
            amount=amount,
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

        spend_raw._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            tx_files=tx_files,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("embed_datum", (True, False), ids=("embedded_datum", "datum"))
    @pytest.mark.parametrize(
        "variant",
        (
            "typed_json",
            "typed_cbor",
            "untyped_value",
            "untyped_json",
            "untyped_cbor",
        ),
    )
    @common.PARAM_PLUTUS_VERSION
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_guessing_game(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        embed_datum: bool,
        variant: str,
        plutus_version: str,
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Test with "guessing game" scripts that expect specific datum and redeemer value.
        Test both typed and untyped redeemer and datum.
        Test passing datum and redeemer to `cardano-cli` as value, json file and cbor file.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 1_000_000

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

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            embed_datum=embed_datum,
        )
        spend_raw._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
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

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 1_000_000

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

        if plutus_version == "plutus_v1":
            script_file1 = script_file1_v1
            execution_cost1 = execution_cost1_v1
            script_file2 = script_file2_v1
            execution_cost2 = execution_cost2_v1
        elif plutus_version == "mix_v1_v2":
            script_file1 = script_file1_v1
            execution_cost1 = execution_cost1_v1
            script_file2 = script_file2_v2
            execution_cost2 = execution_cost2_v2
        elif plutus_version == "mix_v2_v1":
            script_file1 = script_file1_v2
            execution_cost1 = execution_cost1_v2
            script_file2 = script_file2_v1
            execution_cost2 = execution_cost2_v1
        elif plutus_version == "plutus_v2":
            script_file1 = script_file1_v2
            execution_cost1 = execution_cost1_v2
            script_file2 = script_file2_v2
            execution_cost2 = execution_cost2_v2
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
                amount=amount + redeem_cost1.fee,
                datum_hash=datum_hash1,
            ),
            clusterlib.TxOut(
                address=script_address2,
                amount=amount + redeem_cost2.fee + spend_raw.FEE_REDEEM_TXSIZE,
                datum_hash=datum_hash2,
            ),
            # For collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost2.collateral),
        ]

        tx_output_fund = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_fund,
            tx_files=tx_files_fund,
            # TODO: workaround for https://github.com/IntersectMBO/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid_fund = cluster.g_transaction.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos1 = cluster.g_query.get_utxo(
            txin=f"{txid_fund}#0", coins=[clusterlib.DEFAULT_COIN]
        )
        script_utxos2 = cluster.g_query.get_utxo(
            txin=f"{txid_fund}#1", coins=[clusterlib.DEFAULT_COIN]
        )
        collateral_utxos1 = cluster.g_query.get_utxo(txin=f"{txid_fund}#2")
        collateral_utxos2 = cluster.g_query.get_utxo(txin=f"{txid_fund}#3")

        assert script_utxos1 and script_utxos2, "No script UTxOs"
        assert collateral_utxos1 and collateral_utxos2, "No collateral UTxOs"

        assert script_utxos1[0].amount == amount + redeem_cost1.fee, (
            f"Incorrect balance for script address `{script_utxos1[0].address}`"
        )
        assert script_utxos2[0].amount == amount + redeem_cost2.fee + spend_raw.FEE_REDEEM_TXSIZE, (
            f"Incorrect balance for script address `{script_utxos2[0].address}`"
        )

        # Step 2: spend the "locked" UTxOs

        # For mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos1,
                script_file=plutus_op1.script_file,
                collaterals=collateral_utxos1,
                execution_units=(
                    plutus_op1.execution_cost.per_time,
                    plutus_op1.execution_cost.per_space,
                ),
                datum_file=plutus_op1.datum_file,
                redeemer_cbor_file=plutus_op1.redeemer_cbor_file,
            ),
            clusterlib.ScriptTxIn(
                txins=script_utxos2,
                script_file=plutus_op2.script_file,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op2.execution_cost.per_time,
                    plutus_op2.execution_cost.per_space,
                ),
                datum_file=plutus_op2.datum_file,
                redeemer_cbor_file=plutus_op2.redeemer_cbor_file,
            ),
        ]
        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount * 2),
        ]
        tx_output_redeem = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost1.fee + redeem_cost2.fee + spend_raw.FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
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
            == dst_init_balance + amount * 2
        ), f"Incorrect balance for destination address `{payment_addrs[1].address}`"

        script_utxos_lovelace = [
            u for u in [*script_utxos1, *script_utxos2] if u.coin == clusterlib.DEFAULT_COIN
        ]
        for u in script_utxos_lovelace:
            assert not cluster.g_query.get_utxo(utxo=u, coins=[clusterlib.DEFAULT_COIN]), (
                f"Inputs were NOT spent for `{u.address}`"
            )

        # Check tx view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

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

        Test with "always fails" script that fails for all datum / redeemer values.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred, collateral UTxO was not spent
          and the expected error was raised
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)
        amount = 1_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_FAILS[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )

        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="ValidationTagMismatch",
            ignore_file_id="plutus_always_fails",
            # Ignore errors for next 20 seconds
            skip_after=time.time() + 20,
        )

        err, __ = spend_raw._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            expect_failure=True,
        )
        assert err, "The script spending was expected to fail."
        with common.allow_unstable_error_messages():
            assert "PlutusFailure" in err, err

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

        Test with "always fails" script that fails for all datum / redeemer values.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 1_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_FAILS[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )

        try:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
                script_valid=False,
            )
        except clusterlib.CLIError as exc:
            if "(ValidationTagMismatch" not in str(exc):
                raise
            issues.consensus_947.finish_test()

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
        """Test locking a Tx output with native tokens and spending the locked UTxO.

        * create a Tx output that contains native tokens with a datum hash at the script address
        * check that expected amounts of Lovelace and native tokens were locked at the script
          address
        * spend the locked UTxO
        * check that the expected amounts of Lovelace and native tokens were spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        token_rand = clusterlib.get_rand_str(5)

        amount = 2_000_000
        token_amount = 100

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
            amount=token_amount,
        )
        tokens_rec = [clusterlib_utils.Token(coin=t.token, amount=t.amount) for t in tokens]

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            tokens=tokens_rec,
        )
        spend_raw._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            tokens=tokens_rec,
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

        * create a Tx output that contains native tokens with a datum hash at the script address
        * check that expected amounts of Lovelace and native tokens were locked at the script
          address
        * spend the locked UTxO and create new locked UTxO with change
        * check that the expected amounts of Lovelace and native tokens were spent
        * (optional) check transactions in db-sync
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        token_rand = clusterlib.get_rand_str(5)

        amount_fund = 6_000_000
        amount_spend = 2_000_000
        token_amount_fund = 100
        token_amount_spend = 20

        # Add extra fee for tokens
        fee_redeem_txsize = spend_raw.FEE_REDEEM_TXSIZE + 5_000

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

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount_fund,
            tokens=tokens_fund_rec,
        )

        tokens_spend_rec = [
            clusterlib_utils.Token(coin=t.token, amount=token_amount_spend) for t in tokens
        ]

        __, tx_output_spend = spend_raw._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount_spend,
            fee_txsize=fee_redeem_txsize,
            tokens=tokens_spend_rec,
        )

        txid_spend = cluster.g_transaction.get_txid(tx_body_file=tx_output_spend.out_file)
        change_utxos = cluster.g_query.get_utxo(txin=f"{txid_spend}#1")

        # Check that the expected amounts of Lovelace and native tokens were spent and change UTxOs
        # with appropriate datum hash were created
        token_amount_exp = token_amount_fund - token_amount_spend
        assert len(change_utxos) == len(tokens_spend_rec) + 1
        for u in change_utxos:
            if u.coin == clusterlib.DEFAULT_COIN:
                assert u.amount == amount_fund - amount_spend
            else:
                assert u.amount == token_amount_exp
            assert u.datum_hash == script_utxos[0].datum_hash

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("scenario", ("max", "max+1", "none"))
    @common.PARAM_PLUTUS3_VERSION
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_collaterals(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        scenario: str,
        plutus_version: str,
    ):
        """Test dividing required collateral amount into multiple collateral UTxOs.

        Test 3 scenarios:
        1. maximum allowed number of collateral inputs
        2. more collateral inputs than what is allowed
        3. no collateral input

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * create multiple UTxOs for collateral
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * OR check that the amount was not transferred and collateral UTxO was not spent
          when failure is expected
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 1_000_000

        max_collateral_ins = cluster.g_query.get_protocol_params()["maxCollateralInputs"]
        collateral_utxos = []

        if scenario == "max":
            collateral_num = max_collateral_ins
            exp_errors: tuple[str, ...] = ()
            collateral_fraction_offset = 250_000.0
        elif scenario == "max+1":
            collateral_num = max_collateral_ins + 1
            exp_errors = ("TooManyCollateralInputs",)
            collateral_fraction_offset = 250_000.0
        else:
            collateral_num = 0
            exp_errors = (
                "Transaction body has no collateral inputs",
                "NoCollateralInputs",  # In cardano-node 9.2.0+
            )
            collateral_fraction_offset = 1.0

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, fund_collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
            amount=amount,
            collateral_fraction_offset=collateral_fraction_offset,
        )

        if collateral_num:
            # Instead of using the collateral UTxO created by `_fund_script`, create multiple new
            # collateral UTxOs with the combined amount matching the original UTxO
            collateral_amount_part = int(fund_collateral_utxos[0].amount // collateral_num) + 1
            txouts_collaterals = [
                clusterlib.TxOut(address=dst_addr.address, amount=collateral_amount_part)
                for __ in range(collateral_num)
            ]
            tx_files_collaterals = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])
            tx_output_collaterals = cluster.g_transaction.send_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_collaterals",
                txouts=txouts_collaterals,
                tx_files=tx_files_collaterals,
                join_txouts=False,
            )
            txid_collaterals = cluster.g_transaction.get_txid(
                tx_body_file=tx_output_collaterals.out_file
            )
            _utxos_nested = [
                cluster.g_query.get_utxo(txin=f"{txid_collaterals}#{i}")
                for i in range(collateral_num)
            ]
            collateral_utxos = list(itertools.chain.from_iterable(_utxos_nested))

        if exp_errors:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                spend_raw._spend_locked_txin(
                    temp_template=temp_template,
                    cluster_obj=cluster,
                    payment_addr=payment_addr,
                    dst_addr=dst_addr,
                    script_utxos=script_utxos,
                    collateral_utxos=collateral_utxos,
                    plutus_op=plutus_op,
                    amount=amount,
                )
            exc_value = str(excinfo.value)
            assert any(e in exc_value for e in exp_errors), exc_value
        else:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )
