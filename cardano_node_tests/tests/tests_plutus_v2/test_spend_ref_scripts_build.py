"""Tests for reference scripts while spending with Plutus V2 using `transaction build`."""
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
from cardano_node_tests.tests.tests_plutus_v2 import spend_build
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
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"

        plutus_op1 = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS

        if use_same_script:
            plutus_op2 = spend_build.PLUTUS_OP_GUESSING_GAME_UNTYPED
        else:
            plutus_op2 = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_fund = 100_000_000

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
                amount=script_fund,
                inline_datum_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=script_fund,
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

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        fund_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        reference_utxo1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)[0]
        reference_utxo2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)[0]
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 4)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 5)

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        tx_output_redeem = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
        )

        # check that script address UTxOs were spent

        assert not (
            cluster.g_query.get_utxo(utxo=script_utxos1[0])
            or cluster.g_query.get_utxo(utxo=script_utxos2[0])
        ), f"Script address UTxOs were NOT spent - `{script_utxos1}` and `{script_utxos2}`"

        # check min required UTxO with reference script

        min_required_utxo = cluster.g_transaction.calculate_min_req_utxo(txouts=[txouts[2]]).value

        expected_min_required_utxo = 926_650
        assert helpers.is_in_interval(min_required_utxo, expected_min_required_utxo, frac=0.15)

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

        plutus_op = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_fund = 200_000_000

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
                address=script_address_1, amount=script_fund, inline_datum_file=plutus_op.datum_file
            ),
            clusterlib.TxOut(
                address=script_address_2, amount=script_fund, inline_datum_file=plutus_op.datum_file
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=2_000_000,
                reference_script_file=plutus_op.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost.collateral),
        ]

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        fund_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        reference_utxo = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)[0]
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 4)

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        tx_output_redeem = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
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

        plutus_op1 = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS
        plutus_op2 = spend_build.PLUTUS_OP_GUESSING_GAME_UNTYPED

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create the necessary UTxOs

        script_fund = 100_000_000

        script_address_1 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        redeem_cost2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=script_fund,
                datum_hash_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=script_fund,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=2_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost2.collateral),
        ]

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        fund_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        reference_utxo = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)[0]
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 4)

        #  spend the "locked" UTxOs

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        tx_output_redeem = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        plutus_costs = cluster.g_transaction.calculate_plutus_script_cost(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
        )

        # check that script address UTxOs were spent
        assert not (
            cluster.g_query.get_utxo(utxo=script_utxos1[0])
            or cluster.g_query.get_utxo(utxo=script_utxos2[0])
        ), f"Script address UTxOs were NOT spent - `{script_utxos1}` and `{script_utxos2}`"

        # check that the script hash is included for all scripts
        for script in plutus_costs:
            assert script.get(
                "scriptHash"
            ), "Missing script hash on calculate-plutus-script-cost result"

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

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            txins=[reference_utxo],
            tx_files=tx_files,
            txouts=txouts,
            witness_override=2,
        )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        # check that reference script utxo was spent
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

        tx_output_step1 = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts_step1,
        )
        tx_signed_step1 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        out_utxos_step1 = cluster.g_query.get_utxo(tx_raw_output=tx_output_step1)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=out_utxos_step1, txouts=tx_output_step1.txouts
        )
        reference_script = clusterlib.filter_utxos(utxos=out_utxos_step1, utxo_ix=utxo_ix_offset)
        assert reference_script[0].reference_script, "No reference script UTxO"

        #  Step 2: spend an UTxO and reference the script

        txouts_step2 = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount,
            )
        ]

        tx_output_step2 = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files,
            txouts=txouts_step2,
            readonly_reference_txins=reference_script,
        )
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=tx_output_step2.txins)

        out_utxos_step2 = cluster.g_query.get_utxo(tx_raw_output=tx_output_step2)
        new_utxo = clusterlib.filter_utxos(utxos=out_utxos_step2, utxo_ix=utxo_ix_offset)
        utxo_balance = clusterlib.calculate_utxos_balance(utxos=new_utxo)
        assert utxo_balance == amount, f"Incorrect balance for destination UTxO `{new_utxo}`"


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

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.DATUM_42_TYPED,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        # create a Tx output with an inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
                use_reference_script=True,
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

        plutus_op1 = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS
        plutus_op2 = spend_build.PLUTUS_OP_ALWAYS_FAILS

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_fund = 100_000_000

        script_address_1 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        redeem_cost2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=script_fund,
                inline_datum_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=script_fund,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=2_000_000,
                reference_script_file=plutus_op1.script_file,
            ),
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost2.collateral),
        ]

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        fund_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        reference_utxo1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)[0]
        reference_utxo2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)[0]
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 4)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 5)

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
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
        assert "The Plutus script evaluation failed" in err_str, err_str

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

        # create a Tx output with an inline datum at the script address

        script_utxos, collateral_utxos, reference_utxo, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_reference_script=True,
        )
        assert reference_utxo, "No reference script UTxO"

        #  spend the "locked" UTxO

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
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
        assert "ReferenceInputsNotSupported" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    def test_v1_attached_v2_reference(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with an attached V1 script and one using reference V2 script.

        * create the Tx output with an attached V1 script
        * create the Tx output with the reference V2 script
        * spend the locked UTxOs
        * check that the UTxOs were correctly spent
        """
        temp_template = common.get_test_id(cluster)

        plutus_op1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v1"].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v1"].execution_cost,
        )

        plutus_op2 = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_fund = 200_000_000

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
                address=script_address_1, amount=script_fund, datum_hash_file=plutus_op1.datum_file
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=script_fund,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=2_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_2.collateral),
        ]

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        fund_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        reference_utxo = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)[0]
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 4)

        #  spend the "locked" UTxO

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
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

        plutus_op = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_utxos, collateral_utxos, *__ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_reference_script=False,
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

        #  spend the "locked" UTxO

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
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
        assert "ByronTxOutInContext" in err_str, err_str
