"""SECP256k1 tests for spending with Plutus V2 using `transaction build-raw`."""
import logging
from typing import List
from typing import Tuple

import allure
import pytest
from _pytest.fixtures import SubRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus_v2 import spend_raw
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
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
        amount=3_000_000_000,
    )

    return addrs


@pytest.mark.testnets
class TestSECP256k1:
    @pytest.fixture
    def fund_script_secp(
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

        amount = 2_000_000

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
                amount=amount + redeem_cost.fee + spend_raw.FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_common.DATUM_42_TYPED,
            ),
            # for collateral
            clusterlib.TxOut(address=dst_addr.address, amount=redeem_cost.collateral),
        ]

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
        )

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

        script_utxos = cluster.g_query.get_utxo(txin=f"{txid}#0")
        assert script_utxos, "No script UTxO"

        collateral_utxos = cluster.g_query.get_utxo(txin=f"{txid}#1")
        assert collateral_utxos, "No collateral UTxO"

        return algorithm, script_utxos, collateral_utxos

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("fund_script_secp", ("ecdsa", "schnorr"), indirect=True)
    def test_use_secp_builtin_functions(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_secp: Tuple[str, List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
    ):
        """Test that it is possible to spend a locked UTxO by a script that uses a SECP function.

        * create the necessary Tx outputs
        * spend the locked UTxO
        * check that script address UTxO was spent
        """
        amount = 2_000_000

        # create the necessary Tx outputs

        algorithm, script_utxos, collateral_utxos = fund_script_secp
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

        execution_units = (
            plutus_common.SECP256K1_ECDSA_LOOP_COST
            if algorithm == "ecdsa"
            else plutus_common.SECP256K1_SCHNORR_LOOP_COST
        )

        plutus_op = plutus_common.PlutusOp(
            script_file=script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=redeemer_file,
            execution_cost=execution_units,
        )

        # for mypy
        assert plutus_op.script_file
        assert plutus_op.redeemer_file
        assert plutus_op.execution_cost

        # spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                redeemer_file=plutus_op.redeemer_file,
                inline_datum_present=True,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[0].address, amount=amount),
        ]

        try:
            cluster.g_transaction.send_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step1",
                txouts=txouts_redeem,
                tx_files=tx_files_redeem,
                script_txins=plutus_txins,
                fee=300_000,
            )
        except clusterlib.CLIError as err:
            plutus_common.check_secp_expected_error_msg(
                cluster_obj=cluster, algorithm=algorithm, err_msg=str(err)
            )

        # check that script address UTxO was spent
        assert not cluster.g_query.get_utxo(
            utxo=script_utxos[0]
        ), f"Script address UTxO was NOT spent `{script_utxos}`"
