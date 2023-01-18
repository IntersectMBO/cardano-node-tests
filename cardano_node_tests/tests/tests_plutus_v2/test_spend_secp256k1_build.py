"""SECP256k1 tests for spending with Plutus V2 using `transaction build`."""
import json
import logging
from pathlib import Path
from typing import List
from typing import Tuple

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from _pytest.fixtures import SubRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_BUILD_UNUSABLE,
    common.SKIPIF_PLUTUSV2_UNUSABLE,
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
        *[f"{test_id}_payment_addr_{i}" for i in range(2)],
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
class TestSECP256k1:
    @pytest.fixture
    def build_fund_script_secp(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        request: SubRequest,
    ) -> Tuple[str, List[clusterlib.UTXOData], List[clusterlib.UTXOData]]:
        """Fund a Plutus script and create the necessary Tx outputs."""
        algorithm = request.param
        temp_template = f"{common.get_test_id(cluster)}_{algorithm}"

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        script_fund = 200_000_000

        script_file = (
            plutus_common.SECP256K1_LOOP_ECDSA_PLUTUS_V2
            if algorithm == "ecdsa"
            else plutus_common.SECP256K1_LOOP_SCHNORR_PLUTUS_V2
        )

        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=script_file
        )

        execution_units = (
            plutus_common.SECP256K1_ECDSA_LOOP_COST
            if algorithm == "ecdsa"
            else plutus_common.SECP256K1_SCHNORR_LOOP_COST
        )

        redeem_cost = plutus_common.compute_cost(
            execution_cost=execution_units,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address,
                amount=script_fund,
                inline_datum_file=plutus_common.DATUM_42_TYPED,
            ),
            # for collateral
            clusterlib.TxOut(address=dst_addr.address, amount=redeem_cost.collateral),
        ]

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=out_utxos, txouts=tx_output.txouts
        )

        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)
        assert script_utxos, "No script UTxO"

        collateral_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 1)
        assert collateral_utxos, "No collateral UTxO"

        return algorithm, script_utxos, collateral_utxos

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("build_fund_script_secp", ("ecdsa", "schnorr"), indirect=True)
    def test_use_secp_builtin_functions(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        build_fund_script_secp: Tuple[str, List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
    ):
        """Test that it is possible to spend a locked UTxO by a script that uses a SECP function.

        * create the necessary Tx outputs
        * spend the locked UTxO
        * check that script address UTxO was spent
        """
        # create the necessary Tx outputs

        algorithm, script_utxos, collateral_utxos = build_fund_script_secp
        temp_template = f"{common.get_test_id(cluster)}_{algorithm}"

        script_file = (
            plutus_common.SECP256K1_LOOP_ECDSA_PLUTUS_V2
            if algorithm == "ecdsa"
            else plutus_common.SECP256K1_LOOP_SCHNORR_PLUTUS_V2
        )

        redeemer_dir = (
            plutus_common.SEPC256K1_ECDSA_DIR
            if algorithm == "ecdsa"
            else plutus_common.SEPC256K1_SCHNORR_DIR
        )
        redeemer_file = redeemer_dir / "loop_script.redeemer"

        plutus_op = plutus_common.PlutusOp(
            script_file=script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=redeemer_file,
        )

        # for mypy
        assert plutus_op.script_file
        assert plutus_op.datum_file
        assert plutus_op.redeemer_file

        # spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                redeemer_file=plutus_op.redeemer_file,
                inline_datum_present=True,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[0].address, amount=-1),
        ]

        try:
            tx_output_redeem = cluster.g_transaction.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
            )
        except clusterlib.CLIError as err:
            plutus_common.check_secp_expected_error_msg(
                cluster_obj=cluster, algorithm=algorithm, err_msg=str(err)
            )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
        )

        # check that script address UTxO was spent
        assert not cluster.g_query.get_utxo(
            utxo=script_utxos[0]
        ), f"Script address UTxO was NOT spent `{script_utxos}`"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("build_fund_script_secp", ("ecdsa", "schnorr"), indirect=True)
    @hypothesis.given(
        number_of_iterations=st.integers(min_value=1000300, max_value=common.MAX_UINT64)
    )
    @common.hypothesis_settings(max_examples=200)
    def test_overspending_execution_budget(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        number_of_iterations: int,
        build_fund_script_secp: Tuple[str, List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
    ):
        """Try to build a transaction with a plutus script that overspend the execution budget.

        * Expect failure.
        """
        # create the necessary Tx outputs

        algorithm, script_utxos, collateral_utxos = build_fund_script_secp
        temp_template = f"{common.get_test_id(cluster)}_{algorithm}_{common.unique_time_str()}"

        # the redeemer file will define the number of loops on the script
        redeemer_dir = (
            plutus_common.SEPC256K1_ECDSA_DIR
            if algorithm == "ecdsa"
            else plutus_common.SEPC256K1_SCHNORR_DIR
        )

        redeemer_file = redeemer_dir / "loop_script.redeemer"

        # generate a dummy redeemer with a number of loops big enough
        # to make the script to overspending the budget
        redeemer_file_dummy = Path(f"{temp_template}_dummy_script_context.redeemer")

        with open(redeemer_file, encoding="utf-8") as f:
            redeemer = json.load(f)
            redeemer["fields"][0]["int"] = number_of_iterations

        with open(redeemer_file_dummy, "w", encoding="utf-8") as outfile:
            json.dump(redeemer, outfile)

        script_file = (
            plutus_common.SECP256K1_LOOP_ECDSA_PLUTUS_V2
            if algorithm == "ecdsa"
            else plutus_common.SECP256K1_LOOP_SCHNORR_PLUTUS_V2
        )

        plutus_op = plutus_common.PlutusOp(
            script_file=script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=Path(redeemer_file_dummy),
        )

        # for mypy
        assert plutus_op.script_file
        assert plutus_op.datum_file
        assert plutus_op.redeemer_file

        # spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                redeemer_file=plutus_op.redeemer_file,
                inline_datum_present=True,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[0].address, amount=-1),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
            )

        err_str = str(excinfo.value)

        try:
            assert "Negative numbers indicate the overspent budget" in err_str, err_str
        except AssertionError as err:
            plutus_common.check_secp_expected_error_msg(
                cluster_obj=cluster, algorithm=algorithm, err_msg=str(err)
            )
