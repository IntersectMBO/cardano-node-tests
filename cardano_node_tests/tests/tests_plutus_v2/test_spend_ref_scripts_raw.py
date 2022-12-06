"""Tests for reference scripts while spending with Plutus V2 using `transaction build-raw`."""
import logging
from typing import Any
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
class TestReferenceScripts:
    """Tests for Tx output locking using Plutus smart contracts with reference scripts."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_same_script", (True, False), ids=("same_script", "multiple_script")
    )
    def test_reference_multiple_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_same_script: bool,
        request: FixtureRequest,
    ):
        """Test locking two Tx output with a V2 reference script and spending it.

        * create the Tx outputs with an inline datum at the script address
        * create the Tx outputs with the reference scripts
        * spend the locked UTxOs using the reference UTxOs
        * check that the UTxOs were correctly spent
        """
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"

        plutus_op1 = spend_raw.PLUTUS_OP_ALWAYS_SUCCEEDS

        if use_same_script:
            plutus_op2 = spend_raw.PLUTUS_OP_GUESSING_GAME_UNTYPED
        else:
            plutus_op2 = spend_raw.PLUTUS_OP_ALWAYS_SUCCEEDS

        amount = 2_000_000

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_address_1 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost_1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        redeem_cost_2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=amount + redeem_cost_1.fee,
                inline_datum_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=amount + redeem_cost_2.fee + spend_raw.FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op1.script_file,
            ),
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_2.collateral),
        ]

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
        script_utxos1 = cluster.g_query.get_utxo(txin=f"{txid}#0")
        script_utxos2 = cluster.g_query.get_utxo(txin=f"{txid}#1")
        reference_utxo1 = cluster.g_query.get_utxo(txin=f"{txid}#2")[0]
        reference_utxo2 = cluster.g_query.get_utxo(txin=f"{txid}#3")[0]
        collateral_utxos1 = cluster.g_query.get_utxo(txin=f"{txid}#4")
        collateral_utxos2 = cluster.g_query.get_utxo(txin=f"{txid}#5")

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos1,
                reference_txin=reference_utxo1,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos1,
                execution_units=(
                    plutus_op1.execution_cost.per_time,
                    plutus_op1.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op1.redeemer_cbor_file,
                inline_datum_present=True,
            ),
            clusterlib.ScriptTxIn(
                txins=script_utxos2,
                reference_txin=reference_utxo2,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op2.execution_cost.per_time,
                    plutus_op2.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op2.redeemer_cbor_file,
                inline_datum_present=True,
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
            fee=redeem_cost_1.fee + redeem_cost_2.fee + spend_raw.FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.g_transaction.submit_tx(
            tx_file=tx_signed_redeem,
            txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
        )

        # check that script address UTxOs were spent
        assert not (
            cluster.g_query.get_utxo(utxo=script_utxos1[0])
            or cluster.g_query.get_utxo(utxo=script_utxos2[0])
        ), f"Script address UTxOs were NOT spent - `{script_utxos1}` and `{script_utxos2}`"

    @allure.link(helpers.get_vcs_link())
    def test_reference_same_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking two Tx output with the same V2 reference script and spending it.

        * create the Tx outputs with an inline datum at the script address
        * create the Tx output with the reference script
        * spend the locked UTxOs using the reference UTxO
        * check that the UTxOs were correctly spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = spend_raw.PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_address_1 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op.script_file
        )

        script_address_2 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op.script_file
        )

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=amount + redeem_cost.fee,
                inline_datum_file=plutus_op.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=amount + redeem_cost.fee + spend_raw.FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_op.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost.collateral),
        ]

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
        script_utxos1 = cluster.g_query.get_utxo(txin=f"{txid}#0")
        script_utxos2 = cluster.g_query.get_utxo(txin=f"{txid}#1")
        reference_utxo = cluster.g_query.get_utxo(txin=f"{txid}#2")[0]
        collateral_utxos1 = cluster.g_query.get_utxo(txin=f"{txid}#3")
        collateral_utxos2 = cluster.g_query.get_utxo(txin=f"{txid}#4")

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos1,
                reference_txin=reference_utxo,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos1,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                inline_datum_present=True,
            ),
            clusterlib.ScriptTxIn(
                txins=script_utxos2,
                reference_txin=reference_utxo,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                inline_datum_present=True,
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
            fee=redeem_cost.fee * 2 + spend_raw.FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.g_transaction.submit_tx(
            tx_file=tx_signed_redeem,
            txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
        )

        # check that script address UTxOs were spent
        assert not (
            cluster.g_query.get_utxo(utxo=script_utxos1[0])
            or cluster.g_query.get_utxo(utxo=script_utxos2[0])
        ), f"Script address UTxOs were NOT spent - `{script_utxos1}` and `{script_utxos2}`"

    @allure.link(helpers.get_vcs_link())
    def test_mix_reference_attached_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with an attached V2 script and one using reference V2 script.

        * create the Tx output with an attached script
        * create the Tx output with the reference script
        * spend the locked UTxOs
        * check that the UTxOs were correctly spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op1 = spend_raw.PLUTUS_OP_ALWAYS_SUCCEEDS
        plutus_op2 = spend_raw.PLUTUS_OP_GUESSING_GAME_UNTYPED

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_address_1 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost_1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        redeem_cost_2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=amount + redeem_cost_1.fee,
                datum_hash_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=amount + redeem_cost_2.fee + spend_raw.FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_2.collateral),
        ]

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
        script_utxos1 = cluster.g_query.get_utxo(txin=f"{txid}#0")
        script_utxos2 = cluster.g_query.get_utxo(txin=f"{txid}#1")
        reference_utxo = cluster.g_query.get_utxo(txin=f"{txid}#2")[0]
        collateral_utxos1 = cluster.g_query.get_utxo(txin=f"{txid}#3")
        collateral_utxos2 = cluster.g_query.get_utxo(txin=f"{txid}#4")

        #  spend the "locked" UTxO

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
                reference_txin=reference_utxo,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op2.execution_cost.per_time,
                    plutus_op2.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op2.redeemer_cbor_file,
                inline_datum_present=True,
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
            fee=redeem_cost_1.fee + redeem_cost_2.fee + spend_raw.FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.g_transaction.submit_tx(
            tx_file=tx_signed_redeem,
            txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
        )

        # check that script address UTxOs were spent
        assert not (
            cluster.g_query.get_utxo(utxo=script_utxos1[0])
            or cluster.g_query.get_utxo(utxo=script_utxos2[0])
        ), f"Script address UTxOs were NOT spent - `{script_utxos1}` and `{script_utxos2}`"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("plutus_version", ("v1", "v2"), ids=("plutus_v1", "plutus_v2"))
    @pytest.mark.parametrize("address_type", ("shelley", "byron"))
    def test_spend_reference_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
        address_type: str,
    ):
        """Test spending a UTxO that holds a reference script.

        * create a Tx output with reference script (reference script UTxO)
        * check that the expected amount was transferred
        * spend the UTxO
        * check that the UTxO was spent
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{address_type}"
        amount = 2_000_000

        script_file = plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file
        payment_addr = payment_addrs[0]

        reference_addr = payment_addrs[1]
        if address_type == "byron":
            # create reference UTxO on Byron address
            reference_addr = clusterlib_utils.gen_byron_addr(
                cluster_obj=cluster, name_template=temp_template
            )

        # create a Tx output with the reference script
        reference_utxo, __ = clusterlib_utils.create_reference_utxo(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=reference_addr,
            script_file=script_file,
            amount=amount,
        )
        assert reference_utxo.reference_script, "Reference script is missing"
        assert reference_utxo.amount == amount, "Incorrect amount transferred"

        # spend the Tx output with the reference script
        txouts = [clusterlib.TxOut(address=payment_addr.address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[reference_addr.skey_file])

        cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_spend",
            txins=[reference_utxo],
            txouts=txouts,
            tx_files=tx_files,
        )

        # check that reference script UTxO was spent
        assert not cluster.g_query.get_utxo(
            utxo=reference_utxo
        ), f"Reference script UTxO was NOT spent: '{reference_utxo}`"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("plutus_version", ("v1", "v2"), ids=("plutus_v1", "plutus_v2"))
    def test_spend_regular_utxo_and_reference_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
        request: FixtureRequest,
    ):
        """Test spend an UTxO and use a reference a script on the same transaction.

        * create the reference script UTxO with the 'ALWAYS_FAILS' script to have confidence that
          the script was not being executed
        * spend a regular UTxO and reference the script at the same transaction
        * check that the destination UTxO have the right balance
        """
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_FAILS[plutus_version].execution_cost,
        )

        # Step 1: create the reference script UTxO

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )

        txouts_step1 = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount,
                reference_script_file=plutus_op.script_file,
            )
        ]

        tx_output_step1 = cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step1,
            tx_files=tx_files,
        )

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_step1.out_file)
        reference_script = cluster.g_query.get_utxo(txin=f"{txid}#0")
        assert reference_script[0].reference_script, "No reference script UTxO"

        #  Step 2: spend an UTxO and reference the script

        txouts_step2 = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount,
            )
        ]

        tx_output_step2 = cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step2,
            tx_files=tx_files,
        )

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_step2.out_file)
        new_utxo = cluster.g_query.get_utxo(txin=f"{txid}#0")
        utxo_balance = clusterlib.calculate_utxos_balance(utxos=new_utxo)
        assert utxo_balance == amount, f"Incorrect balance for destination UTxO `{new_utxo}`"

    @allure.link(helpers.get_vcs_link())
    def test_reference_script_byron_address(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test creating reference script UTxO on Byron address.

        * create a Byron address
        * create a reference script UTxO on Byron address with the 'ALWAYS_FAILS' script to have
          confidence that the script was not being executed
        """
        temp_template = common.get_test_id(cluster)
        script_file = plutus_common.ALWAYS_FAILS_PLUTUS_V2

        byron_addr = clusterlib_utils.gen_byron_addr(
            cluster_obj=cluster, name_template=temp_template
        )

        # create reference UTxO
        reference_utxo, __ = clusterlib_utils.create_reference_utxo(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=byron_addr,
            script_file=script_file,
            amount=2_000_000,
        )

        assert reference_utxo.address == byron_addr.address, "Incorrect address for reference UTxO"
        assert reference_utxo.reference_script, "Reference script is missing"


@pytest.mark.testnets
class TestNegativeReferenceScripts:
    """Tests for Tx output with reference scripts that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    def test_not_a_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with an invalid reference script.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.DATUM_42_TYPED,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        assert plutus_op.execution_cost

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # create a Tx output with an inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
                amount=amount,
                redeem_cost=redeem_cost,
            )
        err_str = str(excinfo.value)
        assert "Syntax error in script" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    def test_two_scripts_one_fail(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking two Tx with different Plutus reference scripts in single Tx, one fails.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op1 = spend_raw.PLUTUS_OP_ALWAYS_SUCCEEDS
        plutus_op2 = spend_raw.PLUTUS_OP_ALWAYS_FAILS

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_address_1 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost_1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        redeem_cost_2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=amount + redeem_cost_1.fee,
                inline_datum_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=amount + redeem_cost_2.fee + spend_raw.FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op1.script_file,
            ),
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_2.collateral),
        ]

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
        script_utxos1 = cluster.g_query.get_utxo(txin=f"{txid}#0")
        script_utxos2 = cluster.g_query.get_utxo(txin=f"{txid}#1")
        reference_utxo1 = cluster.g_query.get_utxo(txin=f"{txid}#2")[0]
        reference_utxo2 = cluster.g_query.get_utxo(txin=f"{txid}#3")[0]
        collateral_utxos1 = cluster.g_query.get_utxo(txin=f"{txid}#4")
        collateral_utxos2 = cluster.g_query.get_utxo(txin=f"{txid}#5")

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos1,
                reference_txin=reference_utxo1,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos1,
                execution_units=(
                    plutus_op1.execution_cost.per_time,
                    plutus_op1.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op1.redeemer_cbor_file,
                inline_datum_present=True,
            ),
            clusterlib.ScriptTxIn(
                txins=script_utxos2,
                reference_txin=reference_utxo2,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op2.execution_cost.per_time,
                    plutus_op2.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op2.redeemer_cbor_file,
                inline_datum_present=True,
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
            fee=redeem_cost_1.fee + redeem_cost_2.fee + spend_raw.FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx(
                tx_file=tx_signed_redeem,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        err_str = str(excinfo.value)
        script2_hash = helpers.decode_bech32(bech32=script_address_2)[2:]
        assert rf"ScriptHash \"{script2_hash}\") fails" in str(excinfo.value) in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_lock_tx_v1_reference_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a Plutus V1 reference script.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v1"].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v1"].execution_cost,
        )

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
            use_reference_script=True,
            use_inline_datum=True,
        )

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
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                inline_datum_present=True,
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

        # create a Tx output with an inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx(
                tx_file=tx_signed_redeem,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        err_str = str(excinfo.value)
        assert "ReferenceInputsNotSupported" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    def test_v1_attached_v2_reference(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with an attached V1 script and one using reference V2 script.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v1"].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v1"].execution_cost,
        )

        plutus_op2 = spend_raw.PLUTUS_OP_GUESSING_GAME_UNTYPED

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_address_1 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost_1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        redeem_cost_2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=amount + redeem_cost_1.fee,
                datum_hash_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=amount + redeem_cost_2.fee + spend_raw.FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_2.collateral),
        ]

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
        script_utxos1 = cluster.g_query.get_utxo(txin=f"{txid}#0")
        script_utxos2 = cluster.g_query.get_utxo(txin=f"{txid}#1")
        reference_utxo = cluster.g_query.get_utxo(txin=f"{txid}#2")[0]
        collateral_utxos1 = cluster.g_query.get_utxo(txin=f"{txid}#3")
        collateral_utxos2 = cluster.g_query.get_utxo(txin=f"{txid}#4")

        #  spend the "locked" UTxO

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
                reference_txin=reference_utxo,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op2.execution_cost.per_time,
                    plutus_op2.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op2.redeemer_cbor_file,
                inline_datum_present=True,
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
            fee=redeem_cost_1.fee + redeem_cost_2.fee + spend_raw.FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx(
                tx_file=tx_signed_redeem,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        err_str = str(excinfo.value)
        assert "ReferenceInputsNotSupported" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_lock_byron_reference_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a Plutus V2 reference script on Byron address.

        Expect failure.
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = spend_raw.PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the Plutus script

        script_utxos, collateral_utxos, *__ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            use_reference_script=False,
            use_inline_datum=True,
        )

        # create reference UTxO on Byron address
        byron_addr = clusterlib_utils.gen_byron_addr(
            cluster_obj=cluster, name_template=temp_template
        )
        reference_utxo, __ = clusterlib_utils.create_reference_utxo(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=byron_addr,
            script_file=plutus_op.script_file,
            amount=2_000_000,
        )
        assert reference_utxo.address == byron_addr.address, "Incorrect address for reference UTxO"
        assert reference_utxo.reference_script, "Reference script is missing"

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
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                inline_datum_present=True,
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

        # create a Tx output with an inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx(
                tx_file=tx_signed_redeem,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        err_str = str(excinfo.value)
        assert "ByronTxOutInContext" in err_str, err_str
