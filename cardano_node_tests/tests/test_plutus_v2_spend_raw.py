"""Tests for spending with Plutus V2 using `transaction build-raw`."""
import logging
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.BABBAGE,
        reason="runs only with Babbage+ TX",
    ),
    pytest.mark.smoke,
]

# approx. fee for Tx size
FEE_REDEEM_TXSIZE = 400_000


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


class TestLockingV2:
    """Tests for Tx output locking using Plutus V2 smart contracts."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_reference_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        * create a Tx output with an inline datum at the script address
        * create a Tx output with a reference script
        * check that the expected amount was locked at the script address
        * spend the locked UTxO using the reference UTxO
        * check that the expected amount was spent
        * check that the reference UTxO was not spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        protocol_params = cluster.get_protocol_params()

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        # Step 1: fund the Plutus script

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file

        script_address = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr", payment_script_file=plutus_op.script_file
        )
        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=protocol_params
        )

        # create a Tx output with a datum hash at the script address

        tx_files_fund = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )
        txouts_fund = [
            clusterlib.TxOut(
                address=script_address,
                amount=amount + redeem_cost.fee + FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_op.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=2_000_000,
                reference_script_file=plutus_op.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost.collateral),
        ]

        tx_output_fund = cluster.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_fund,
            tx_files=tx_files_fund,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid_fund = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid_fund}#0", coins=[clusterlib.DEFAULT_COIN])
        reference_utxos = cluster.get_utxo(txin=f"{txid_fund}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid_fund}#2")

        assert script_utxos, "No script UTxO"
        assert reference_utxos, "No reference UTxO"
        assert collateral_utxos, "No collateral UTxO"

        assert (
            script_utxos[0].amount == amount + redeem_cost.fee + FEE_REDEEM_TXSIZE
        ), f"Incorrect balance for script address `{script_utxos[0].address}`"

        assert (
            script_utxos[0].inline_datum_hash and script_utxos[0].inline_datum
        ), "Inline datum not present on script UTxO"
        assert reference_utxos[0].reference_script, "Reference script not present on reference UTxO"

        # Step 2: spend the "locked" UTxO

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        reference_utxo = reference_utxos[0]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                reference_txin=reference_utxo,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                inline_datum_present=True,
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
            ),
        ]
        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]
        tx_output_redeem = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        dst_init_balance = cluster.get_address_balance(payment_addrs[1].address)

        cluster.submit_tx(
            tx_file=tx_signed_redeem,
            txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
        )

        assert (
            cluster.get_address_balance(payment_addrs[1].address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{payment_addrs[1].address}`"

        script_utxos_lovelace = [u for u in script_utxos if u.coin == clusterlib.DEFAULT_COIN]
        for u in script_utxos_lovelace:
            assert not cluster.get_utxo(
                txin=f"{u.utxo_hash}#{u.utxo_ix}", coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were NOT spent for `{u.address}`"

        assert cluster.get_utxo(
            txin=f"{reference_utxo.utxo_hash}#{reference_utxo.utxo_ix}",
            coins=[clusterlib.DEFAULT_COIN],
        ), "Reference input was spent"
