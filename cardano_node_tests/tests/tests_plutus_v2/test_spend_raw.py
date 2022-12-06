"""Tests for spending with Plutus V2 using `transaction build-raw`."""
import binascii
import logging
from typing import List

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus_v2 import spend_raw
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_queries
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
class TestLockingV2:
    """Tests for Tx output locking using Plutus V2 smart contracts."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("use_inline_datum", (True, False), ids=("inline_datum", "datum_file"))
    @pytest.mark.parametrize(
        "use_reference_script", (True, False), ids=("reference_script", "script_file")
    )
    @pytest.mark.dbsync
    def test_txout_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_inline_datum: bool,
        use_reference_script: bool,
        request: FixtureRequest,
    ):
        """Test combinations of inline datum and datum file + reference script and script file.

        * create the necessary Tx outputs
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected UTxOs were correctly spent
        """
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        amount = 2_000_000

        plutus_op = spend_raw.PLUTUS_OP_GUESSING_GAME_UNTYPED

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the Plutus script

        script_utxos, collateral_utxos, reference_utxo, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            use_inline_datum=use_inline_datum,
            use_reference_script=use_reference_script,
        )
        assert reference_utxo or not use_reference_script, "No reference script UTxO"

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file if not use_reference_script else "",
                reference_txin=reference_utxo if use_reference_script else None,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2 if use_reference_script else "",
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                inline_datum_present=use_inline_datum,
                datum_file=plutus_op.datum_file if not use_inline_datum else "",
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        tx_output_redeem = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + spend_raw.FEE_REDEEM_TXSIZE,
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
            == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{payment_addrs[1].address}`"

        # check that script address UTxO was spent
        assert not cluster.g_query.get_utxo(
            utxo=script_utxos[0]
        ), f"Script address UTxO was NOT spent `{script_utxos[0]}`"

        # check that reference UTxO was NOT spent
        assert not reference_utxo or cluster.g_query.get_utxo(
            utxo=reference_utxo
        ), "Reference input was spent"

        # check expected fees
        expected_fee_redeem = 176_024 if use_reference_script else 179_764

        fee = (
            # for tx size
            cluster.g_transaction.estimate_fee(
                txbody_file=tx_output_redeem.out_file,
                txin_count=len(tx_output_redeem.txins),
                txout_count=len(tx_output_redeem.txouts),
                witness_count=len(tx_files_redeem.signing_key_files),
            )
            # for script execution
            + redeem_cost.fee
        )

        assert helpers.is_in_interval(fee, expected_fee_redeem, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.needs_dbsync
    def test_datum_bytes_in_dbsync(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test that datum bytes in db-sync corresponds to original datum.

        * create a Tx output with an inline datum at the script address
        * double-check that the UTxO datum hash corresponds to the datum CBOR file
        * check that datum from db-sync produces the original datum hash
        * check that datum bytes in db-sync corresponds to the original datum
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_cbor_file=plutus_common.DATUM_FINITE_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.DATUM_FINITE_TYPED_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )
        assert plutus_op.execution_cost  # for mypy

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # create a Tx output with an inline datum at the script address
        script_utxos, *__ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            use_inline_datum=True,
        )
        script_utxo = script_utxos[0]

        # double-check that the UTxO datum hash corresponds to the datum CBOR file
        datum_hash = cluster.g_transaction.get_hash_script_data(
            script_data_cbor_file=plutus_common.DATUM_FINITE_TYPED_CBOR
        )
        assert datum_hash == script_utxo.inline_datum_hash, "Unexpected datum hash"

        datum_db_response = list(
            dbsync_queries.query_datum(datum_hash=script_utxo.inline_datum_hash)
        )

        # check that datum from db-sync produces the original datum hash
        db_cbor_hex = datum_db_response[0].bytes.hex()
        db_cbor_bin = binascii.unhexlify(db_cbor_hex)
        db_cbor_file = f"{temp_template}_db_datum.cbor"
        with open(db_cbor_file, "wb") as out_fp:
            out_fp.write(db_cbor_bin)
        db_datum_hash = cluster.g_transaction.get_hash_script_data(
            script_data_cbor_file=db_cbor_file
        )
        assert (
            db_datum_hash == datum_hash
        ), "Datum hash of bytes in db-sync doesn't correspond to the original datum hash"

        # check that datum bytes in db-sync corresponds to the original datum
        with open(plutus_common.DATUM_FINITE_TYPED_CBOR, "rb") as in_fp:
            orig_cbor_bin = in_fp.read()
            orig_cbor_hex = orig_cbor_bin.hex()

        # see https://github.com/input-output-hk/cardano-db-sync/issues/1214
        # and https://github.com/input-output-hk/cardano-node/issues/4433
        if db_cbor_hex != orig_cbor_hex:
            pytest.xfail(
                "See cardano-node-issue #4433 - "
                "datum bytes in db-sync doesn't correspond to the original datum"
            )
