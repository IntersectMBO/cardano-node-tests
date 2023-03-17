"""Negative tests for spending with Plutus using `transaction build`."""
import json
import logging
from pathlib import Path
from typing import List
from typing import Tuple

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import spend_build
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_BUILD_UNUSABLE,
    common.SKIPIF_PLUTUS_UNUSABLE,
    pytest.mark.smoke,
    pytest.mark.plutus,
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
        amount=1_000_000_000,
    )

    return addrs


@pytest.mark.testnets
class TestNegative:
    """Tests for Tx output locking using Plutus smart contracts that are expected to fail."""

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

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a collateral UTxO with native tokens
        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs[0]

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
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=100,
        )
        tokens_rec = [plutus_common.Token(coin=t.token, amount=t.amount) for t in tokens]

        script_utxos, collateral_utxos, tx_output_fund = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            tokens_collateral=tokens_rec,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=2_000_000,
            )

        err_str = str(excinfo.value)
        assert "CollateralContainsNonADA" in err_str, err_str

        # check expected fees
        expected_fee_fund = 173597
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

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

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO while using the same UTxO as collateral
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, __, tx_output_fund = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=script_utxos,
                plutus_op=plutus_op,
                amount=2_000_000,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert (
            "expected to be key witnessed but are actually script witnessed: "
            f'["{script_utxos[0].utxo_hash}#{script_utxos[0].utxo_ix}"]' in err_str
            # in 1.35.3 and older
            or "Expected key witnessed collateral" in err_str
        ), err_str

        # check expected fees
        expected_fee_fund = 168_845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
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

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with "guessing game" script that expects specific datum and redeemer value.
        Test negative scenarios where datum or redeemer value is different than expected.
        Expect failure.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was not spent
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{variant}"

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

        script_utxos, collateral_utxos, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=2_000_000,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert "The Plutus script evaluation failed" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    def test_two_scripts_spending_one_fail(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking two Tx outputs with two different Plutus scripts in single Tx, one fails.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output with a datum hash at the script addresses
        * try to spend the locked UTxOs
        * check that the expected error was raised
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 50_000_000

        script_fund = 200_000_000

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
                amount=script_fund,
                datum_hash=datum_hash1,
            ),
            clusterlib.TxOut(
                address=script_address2,
                amount=script_fund,
                datum_hash=datum_hash2,
            ),
            # for collateral
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

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_fund)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=out_utxos, txouts=tx_output_fund.txouts
        )

        script_utxos1 = clusterlib.filter_utxos(
            utxos=out_utxos, utxo_ix=utxo_ix_offset, coin=clusterlib.DEFAULT_COIN
        )
        script_utxos2 = clusterlib.filter_utxos(
            utxos=out_utxos, utxo_ix=utxo_ix_offset + 1, coin=clusterlib.DEFAULT_COIN
        )
        collateral_utxos1 = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 2)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 3)

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
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount * 2),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
                change_address=payment_addrs[0].address,
            )

        err_str = str(excinfo.value)
        assert "The Plutus script evaluation failed" in err_str, err_str


@pytest.mark.testnets
class TestNegativeRedeemer:
    """Tests for Tx output locking using Plutus smart contracts with wrong redeemer."""

    MIN_INT_VAL = -common.MAX_UINT64
    AMOUNT = 2_000_000

    @pytest.fixture
    def fund_script_guessing_game_v1(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]]:
        """Fund a PlutusV1 script and create the locked UTxO and collateral UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42,
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
        )

        script_utxos, collateral_utxos, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        return script_utxos, collateral_utxos

    @pytest.fixture
    def fund_script_guessing_game_v2(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]]:
        """Fund a PlutusV2 script and create the locked UTxO and collateral UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS_V2,
            datum_file=plutus_common.DATUM_42,
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_V2_COST,
        )

        script_utxos, collateral_utxos, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        return script_utxos, collateral_utxos

    def _int_out_of_range(
        self,
        cluster: clusterlib.ClusterLib,
        temp_template: str,
        script_utxos: List[clusterlib.UTXOData],
        collateral_utxos: List[clusterlib.UTXOData],
        payment_addr: clusterlib.AddressRecord,
        dst_addr: clusterlib.AddressRecord,
        redeemer_value: int,
        plutus_version: str,
    ):
        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        redeemer_file = f"{temp_template}.redeemer"
        if redeemer_content:
            with open(redeemer_file, "w", encoding="utf-8") as outfile:
                json.dump(redeemer_content, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file) if redeemer_content else None,
            redeemer_value=None if redeemer_content else str(redeemer_value),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert (
            # On node version < 1.36.0
            "Value out of range within the script data" in err_str
            # See node commit 2efdd2c173bee8f2463937cebb20614adf6180f0
            or "Incorrect datum" in err_str
        ), err_str

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
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a wrong redeemer value that is in the valid range.

        Expect failure.
        """
        hypothesis.assume(redeemer_value != 42)

        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"

        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump(redeemer_content, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file) if redeemer_content else None,
            redeemer_value=None if redeemer_content else str(redeemer_value),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert "The Plutus script evaluation failed" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers(min_value=common.MAX_UINT64 + 1))
    @hypothesis.example(redeemer_value=common.MAX_UINT64 + 1)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_value_above_range(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a wrong redeemer value, above max value allowed.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game
        self._int_out_of_range(
            cluster=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_version=plutus_version,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers(max_value=MIN_INT_VAL - 1))
    @hypothesis.example(redeemer_value=MIN_INT_VAL - 1)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_value_bellow_range(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a wrong redeemer value, bellow min value allowed.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game
        self._int_out_of_range(
            cluster=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_version=plutus_version,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_type(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to spend a locked UTxO with a wrong redeemer type, try to use bytes.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"bytes": redeemer_value.hex()}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert "Script debugging logs: Incorrect datum. Expected 42." in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(min_size=65))
    @common.hypothesis_settings(max_examples=100)
    @common.PARAM_PLUTUS_VERSION
    def test_too_big(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to spend a locked UTxO using redeemer that is too big.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"bytes": redeemer_value.hex()}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert (
            "must consist of at most 64 bytes" in err_str  # on node version < 1.36.0
            or "Incorrect datum" in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in typed format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"int": redeemer_value.hex()}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert (
            'The value in the field "int" does not have the type required by the schema.' in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in untyped format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"int": redeemer_value.hex()}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert (
            'The value in the field "int" does not have the type required by the schema.' in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in typed format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"bytes": redeemer_value}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert (
            'The value in the field "bytes" does not have the type required by the schema.'
            in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in untyped format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"bytes": redeemer_value}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert (
            'The value in the field "bytes" does not have the type required by the schema.'
            in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_invalid_json(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
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

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump(f'{{"{redeemer_value}"}}', outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert "JSON object expected. Unexpected value" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
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

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{redeemer_type: 42}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert (
            # On node version < 1.36.0
            'Expected a single field named "int", "bytes", "string", "list" or "map".' in err_str
            # See node commit ac662d8e46554c1ed02d485bfdd69e7ec04d8613
            or 'Expected a single field named "int", "bytes", "list" or "map".' in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
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

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({redeemer_type: 42}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert (
            # On node version < 1.36.0
            'Expected a single field named "int", "bytes", "string", "list" or "map".' in err_str
            # See node commit ac662d8e46554c1ed02d485bfdd69e7ec04d8613
            or 'Expected a single field named "int", "bytes", "list" or "map".' in err_str
        ), err_str
