"""Negative tests for spending with Plutus using `transaction build-raw`."""
import json
import logging
from pathlib import Path
from typing import Any
from typing import List
from typing import Tuple

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.fixtures import SubRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import spend_raw
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_PLUTUS_UNUSABLE,
    pytest.mark.smoke,
    pytest.mark.plutus,
]


FundTupleT = Tuple[
    List[clusterlib.UTXOData], List[clusterlib.UTXOData], List[clusterlib.AddressRecord]
]


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    test_id = common.get_test_id(cluster)
    addrs = clusterlib_utils.create_payment_addr_records(
        *[f"{test_id}_payment_addr_{i}" for i in range(3)],
        cluster_obj=cluster,
    )

    # fund source address
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=3_000_000_000,
    )

    return addrs


@pytest.mark.testnets
class TestNegative:
    """Tests for Tx output locking using Plutus smart contracts that are expected to fail."""

    @pytest.fixture
    def pparams(self, cluster: clusterlib.ClusterLib) -> dict:
        return cluster.g_query.get_protocol_params()

    @pytest.fixture
    def fund_execution_units_above_limit(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        request: SubRequest,
    ) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData], plutus_common.PlutusOp]:
        plutus_version = request.param
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=2_000_000,
        )

        return script_utxos, collateral_utxos, plutus_op

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.parametrize(
        "variant",
        (
            "42_43",  # correct datum, wrong redeemer
            "43_42",  # wrong datum, correct redeemer
            "43_43",  # wrong datum and redeemer
        ),
    )
    @common.PARAM_PLUTUS_VERSION
    def test_invalid_guessing_game(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        variant: str,
        plutus_version: str,
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Test with "guessing game" script that expects specific datum and redeemer value.
        Test negative scenarios where datum or redeemer value is different than expected.
        Expect failure.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was not spent
        """
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{variant}"
        amount = 2_000_000

        if variant == "42_43":
            datum_file = plutus_common.DATUM_42_TYPED
            redeemer_file = plutus_common.REDEEMER_43_TYPED
        elif variant == "43_42":
            datum_file = plutus_common.DATUM_43_TYPED
            redeemer_file = plutus_common.REDEEMER_42_TYPED
        elif variant == "43_43":
            datum_file = plutus_common.DATUM_43_TYPED
            redeemer_file = plutus_common.REDEEMER_43_TYPED
        else:
            raise AssertionError("Unknown test variant.")

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME[plutus_version].script_file,
            datum_file=datum_file,
            redeemer_file=redeemer_file,
            execution_cost=plutus_common.GUESSING_GAME[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )
        err, __ = spend_raw._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            expect_failure=True,
        )

        assert "ValidationTagMismatch (IsValid True)" in err, err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.PARAM_PLUTUS_VERSION
    def test_collateral_w_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test spending the locked UTxO while collateral contains native tokens.

        Expect failure.

        * create a collateral UTxO with native tokens
        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        token_rand = clusterlib.get_rand_str(5)

        amount = 2_000_000
        token_amount = 100

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
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
        tokens_rec = [plutus_common.Token(coin=t.token, amount=t.amount) for t in tokens]

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            tokens_collateral=tokens_rec,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )

        err_str = str(excinfo.value)
        assert "CollateralContainsNonADA" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.PARAM_PLUTUS_VERSION
    def test_same_collateral_txin(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test spending the locked UTxO while using the same UTxO as collateral.

        Expect failure.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO while using the same UTxO as collateral
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_cbor_file=plutus_common.DATUM_42_CBOR,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, *__ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=script_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )
        err_str = str(excinfo.value)
        assert "cardano-cli transaction submit" in err_str, err_str
        assert "ScriptsNotPaidUTxO" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.PARAM_PLUTUS_VERSION
    def test_collateral_percent(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Try to spend locked UTxO while collateral is less than required.

        Expect failure.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * create a collateral UTxO with amount of ADA less than required by `collateralPercentage`
        * try to spend the UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 2_000_000

        # increase fixed cost so the required collateral is higher than minimum collateral of 2 ADA
        execution_cost = plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost
        execution_cost_increased = execution_cost._replace(fixed_cost=2_000_000)
        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=execution_cost_increased,
        )

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            collateral_fraction_offset=0.9,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )

        err_str = str(excinfo.value)
        assert "InsufficientCollateral" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    def test_two_scripts_spending_one_fail(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking two Tx outputs with two different Plutus scripts in single Tx, one fails.

        Expect failure.

        * create a Tx output with a datum hash at the script addresses
        * try to spend the locked UTxOs
        * check that the expected error was raised
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 2_000_000

        protocol_params = cluster.g_query.get_protocol_params()

        plutus_op1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        plutus_op2 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_FAILS_COST,
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
        script2_hash = helpers.decode_bech32(bech32=script_address2)[2:]
        redeem_cost2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost, protocol_params=protocol_params
        )
        datum_hash2 = cluster.g_transaction.get_hash_script_data(
            script_data_file=plutus_op2.datum_file
        )

        # create a Tx output with a datum hash at the script address

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
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost2.collateral),
        ]

        tx_output_fund = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_fund,
            tx_files=tx_files_fund,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
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

        # Step 2: spend the "locked" UTxOs

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

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(tx_file=tx_signed_redeem)

        err_str = str(excinfo.value)
        assert rf"ScriptHash \"{script2_hash}\") fails" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(data=st.data())
    @common.hypothesis_settings(100)
    @pytest.mark.parametrize(
        "fund_execution_units_above_limit",
        ("v1", pytest.param("v2", marks=common.SKIPIF_PLUTUSV2_UNUSABLE)),
        ids=("plutus_v1", "plutus_v2"),
        indirect=True,
    )
    def test_execution_units_above_limit(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_execution_units_above_limit: Tuple[
            List[clusterlib.UTXOData], List[clusterlib.UTXOData], plutus_common.PlutusOp
        ],
        pparams: dict,
        data: st.DataObject,
        request: FixtureRequest,
    ):
        """Test spending a locked UTxO with a Plutus script with execution units above the limit.

        Expect failure.

        * fund the script address and create a UTxO for collateral
        * try to spend the locked UTxO when execution units are set above the limits
        * check that failed because the execution units were too big
        """
        temp_template = (
            f"{common.get_test_id(cluster)}_{request.node.callspec.id}_{common.unique_time_str()}"
        )

        amount = 2_000_000

        script_utxos, collateral_utxos, plutus_op = fund_execution_units_above_limit

        per_time = data.draw(
            st.integers(
                min_value=pparams["maxTxExecutionUnits"]["steps"] + 1, max_value=common.MAX_INT64
            )
        )
        assert per_time > pparams["maxTxExecutionUnits"]["steps"]

        per_space = data.draw(
            st.integers(
                min_value=pparams["maxTxExecutionUnits"]["memory"] + 1, max_value=common.MAX_INT64
            )
        )
        assert per_space > pparams["maxTxExecutionUnits"]["memory"]

        fixed_cost = pparams["txFeeFixed"]

        high_execution_cost = plutus_common.ExecutionCost(
            per_time=per_time, per_space=per_space, fixed_cost=fixed_cost
        )

        plutus_op = plutus_op._replace(execution_cost=high_execution_cost)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )
        err_str = str(excinfo.value)
        assert "ExUnitsTooBigUTxO" in err_str, err_str


@pytest.mark.testnets
class TestNegativeRedeemer:
    """Tests for Tx output locking using Plutus smart contracts with wrong redeemer."""

    MIN_INT_VAL = -common.MAX_UINT64
    AMOUNT = 2_000_000

    def _fund_script_guessing_game(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        plutus_version: str,
    ) -> FundTupleT:
        """Fund a Plutus script and create the locked UTxO and collateral UTxO."""
        payment_addrs = clusterlib_utils.create_payment_addr_records(
            *[f"{temp_template}_payment_addr_{i}" for i in range(2)],
            cluster_obj=cluster_obj,
        )

        # fund source address
        clusterlib_utils.fund_from_faucet(
            payment_addrs[0],
            cluster_obj=cluster_obj,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=3_000_000_000,
        )

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster_obj,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=self.AMOUNT,
        )

        return script_utxos, collateral_utxos, payment_addrs

    @pytest.fixture
    def fund_script_guessing_game_v1(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> FundTupleT:
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            temp_template = common.get_test_id(cluster)

            script_utxos, collateral_utxos, payment_addrs = self._fund_script_guessing_game(
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                temp_template=temp_template,
                plutus_version="v1",
            )
            fixture_cache.value = script_utxos, collateral_utxos, payment_addrs

        return script_utxos, collateral_utxos, payment_addrs

    @pytest.fixture
    def fund_script_guessing_game_v2(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> FundTupleT:
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            temp_template = common.get_test_id(cluster)

            script_utxos, collateral_utxos, payment_addrs = self._fund_script_guessing_game(
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                temp_template=temp_template,
                plutus_version="v2",
            )
            fixture_cache.value = script_utxos, collateral_utxos, payment_addrs

        return script_utxos, collateral_utxos, payment_addrs

    @pytest.fixture
    def cost_per_unit(
        self,
        cluster: clusterlib.ClusterLib,
    ) -> plutus_common.ExecutionCost:
        return plutus_common.get_cost_per_unit(
            protocol_params=cluster.g_query.get_protocol_params()
        )

    def _failed_tx_build(
        self,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        script_utxos: List[clusterlib.UTXOData],
        collateral_utxos: List[clusterlib.UTXOData],
        redeemer_content: str,
        dst_addr: clusterlib.AddressRecord,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
    ) -> str:
        """Try to build a Tx and expect failure."""
        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            outfile.write(redeemer_content)

        per_time = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_time
        per_space = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_space

        fee_redeem = (
            round(per_time * cost_per_unit.per_time + per_space * cost_per_unit.per_space)
            + plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.fixed_cost
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    per_time,
                    per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=Path(redeemer_file),
            )
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.g_transaction.build_raw_tx_bare(
                out_file=f"{temp_template}_step2_tx.body",
                txouts=txouts,
                tx_files=tx_files,
                fee=fee_redeem + spend_raw.FEE_REDEEM_TXSIZE,
                script_txins=plutus_txins,
            )
        return str(excinfo.value)

    def _int_out_of_range(
        self,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        script_utxos: List[clusterlib.UTXOData],
        collateral_utxos: List[clusterlib.UTXOData],
        redeemer_value: int,
        dst_addr: clusterlib.AddressRecord,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
    ) -> str:
        """Try to spend a locked UTxO with redeemer int value that is not in allowed range."""
        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        redeemer_file = f"{temp_template}.redeemer"
        if redeemer_content:
            with open(redeemer_file, "w", encoding="utf-8") as outfile:
                json.dump(redeemer_content, outfile)

        per_time = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_time
        per_space = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_space

        fee_redeem = (
            round(per_time * cost_per_unit.per_time + per_space * cost_per_unit.per_space)
            + plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.fixed_cost
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    per_time,
                    per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=redeemer_file if redeemer_content else "",
                redeemer_value=str(redeemer_value) if not redeemer_content else "",
            )
        ]

        err_str = ""
        try:
            cluster_obj.g_transaction.build_raw_tx_bare(
                out_file=f"{temp_template}_step2_tx.body",
                txouts=txouts,
                tx_files=tx_files,
                fee=fee_redeem + spend_raw.FEE_REDEEM_TXSIZE,
                script_txins=plutus_txins,
            )
        except clusterlib.CLIError as exc:
            err_str = str(exc)

        return err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        redeemer_value=st.integers(min_value=MIN_INT_VAL, max_value=common.MAX_UINT64)
    )
    @hypothesis.example(redeemer_value=MIN_INT_VAL)
    @hypothesis.example(redeemer_value=common.MAX_UINT64)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_value_inside_range(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with an unexpected redeemer value.

        Expect failure.
        """
        hypothesis.assume(redeemer_value != 42)

        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game

        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        redeemer_file = f"{temp_template}.redeemer"
        if redeemer_content:
            with open(redeemer_file, "w", encoding="utf-8") as outfile:
                json.dump(redeemer_content, outfile)

        per_time = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_time
        per_space = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_space

        # try to spend the "locked" UTxO

        fee_redeem = (
            round(per_time * cost_per_unit.per_time + per_space * cost_per_unit.per_space)
            + plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.fixed_cost
        )

        dst_addr = payment_addrs[1]

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    per_time,
                    per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=redeemer_file if redeemer_content else "",
                redeemer_value=str(redeemer_value) if not redeemer_content else "",
            )
        ]
        tx_raw_output = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts,
            tx_files=tx_files,
            fee=fee_redeem + spend_raw.FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(tx_file=tx_signed)

        err_str = str(excinfo.value)
        assert "ValidationTagMismatch (IsValid True)" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers(max_value=MIN_INT_VAL - 1))
    @hypothesis.example(redeemer_value=MIN_INT_VAL - 1)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_value_bellow_range(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a redeemer int value < minimum allowed value.

        Expect failure on node version < 1.36.0.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        err_str = self._int_out_of_range(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )

        assert (
            # See node commit 2efdd2c173bee8f2463937cebb20614adf6180f0
            not err_str
            # On node version < 1.36.0
            or "Value out of range within the script data" in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers(min_value=common.MAX_UINT64 + 1))
    @hypothesis.example(redeemer_value=common.MAX_UINT64 + 1)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_value_above_range(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a redeemer int value > maximum allowed value.

        Expect failure on node version < 1.36.0.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        err_str = self._int_out_of_range(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )

        assert (
            # See node commit 2efdd2c173bee8f2463937cebb20614adf6180f0
            not err_str
            # On node version < 1.36.0
            or "Value out of range within the script data" in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_type(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to spend a locked UTxO with an invalid redeemer type.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"bytes": redeemer_value.hex()}, outfile)

        # try to spend the "locked" UTxO

        per_time = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_time
        per_space = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_space

        fee_redeem = (
            round(per_time * cost_per_unit.per_time + per_space * cost_per_unit.per_space)
            + plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.fixed_cost
        )

        dst_addr = payment_addrs[1]

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    per_time,
                    per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=Path(redeemer_file),
            )
        ]

        tx_raw_output = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts,
            tx_files=tx_files,
            fee=fee_redeem + spend_raw.FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(tx_file=tx_signed)

        err_str = str(excinfo.value)
        assert "ValidationTagMismatch (IsValid True)" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(min_size=65))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_too_big(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to spend a locked UTxO using redeemer that is too big.

        Expect failure on node version < 1.36.0.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game

        redeemer_content = json.dumps(
            {"constructor": 0, "fields": [{"bytes": redeemer_value.hex()}]}
        )

        # try to build a Tx for spending the "locked" UTxO

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            outfile.write(redeemer_content)

        per_time = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_time
        per_space = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_space

        fee_redeem = (
            round(per_time * cost_per_unit.per_time + per_space * cost_per_unit.per_space)
            + plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.fixed_cost
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[1].skey_file])
        txouts = [clusterlib.TxOut(address=payment_addrs[1].address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    per_time,
                    per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=Path(redeemer_file),
            )
        ]

        err_msg = ""
        try:
            cluster.g_transaction.build_raw_tx_bare(
                out_file=f"{temp_template}_step2_tx.body",
                txouts=txouts,
                tx_files=tx_files,
                fee=fee_redeem + spend_raw.FEE_REDEEM_TXSIZE,
                script_txins=plutus_txins,
            )
        except clusterlib.CLIError as exc:
            err_msg = str(exc)

        assert (
            not err_msg or "must consist of at most 64 bytes" in err_msg  # on node version < 1.36.0
        ), err_msg

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in typed format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game

        redeemer_content = json.dumps({"constructor": 0, "fields": [{"int": redeemer_value.hex()}]})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )
        assert 'field "int" does not have the type required by the schema' in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in untyped format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = json.dumps({"int": redeemer_value.hex()})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )
        assert 'field "int" does not have the type required by the schema' in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in typed format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{redeemer_value}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = json.dumps({"constructor": 0, "fields": [{"bytes": redeemer_value}]})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )
        assert 'field "bytes" does not have the type required by the schema' in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in untyped format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = json.dumps({"bytes": redeemer_value})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )
        assert 'field "bytes" does not have the type required by the schema' in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_invalid_json(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: str,
    ):
        """Try to build a Tx using a redeemer value that is invalid JSON.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = f'{{"{redeemer_value}"}}'

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )
        assert "Invalid JSON format" in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_type: str,
    ):
        """Try to build a Tx using a JSON typed schema that specifies an invalid type.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = json.dumps({redeemer_type: 42})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )

        assert (
            # On node version < 1.36.0
            'Expected a single field named "int", "bytes", "string", "list" or "map".' in err
            # See node commit ac662d8e46554c1ed02d485bfdd69e7ec04d8613
            or 'Expected a single field named "int", "bytes", "list" or "map".' in err
        ), err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_type: str,
    ):
        """Try to build a Tx using a JSON untyped schema that specifies an invalid type.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = json.dumps({redeemer_type: 42})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )

        assert (
            # On node version < 1.36.0
            'Expected a single field named "int", "bytes", "string", "list" or "map".' in err
            # See node commit ac662d8e46554c1ed02d485bfdd69e7ec04d8613
            or 'Expected a single field named "int", "bytes", "list" or "map".' in err
        ), err
