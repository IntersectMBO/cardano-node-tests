"""Tests for ro reference inputs while spending with Plutus V2 using `transaction build-raw`."""
import logging
import re
from typing import Any
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus_v2 import spend_raw
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view

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
class TestReadonlyReferenceInputs:
    """Tests for Tx with readonly reference inputs."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("reference_input_scenario", ("single", "duplicated"))
    @pytest.mark.dbsync
    def test_use_reference_input(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        reference_input_scenario: str,
    ):
        """Test use a reference input when unlock some funds.

        * create the necessary Tx outputs
        * use a reference input and spend the locked UTxO
        * check that the reference input was not spent
        """
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{reference_input_scenario}"

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

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=2_000_000,
            redeem_cost=redeem_cost,
        )

        reference_input = spend_raw._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=amount,
        )

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                datum_file=plutus_op.datum_file,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        if reference_input_scenario == "single":
            readonly_reference_txins = reference_input
        else:
            readonly_reference_txins = reference_input * 2

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            txouts=txouts_redeem,
            readonly_reference_txins=readonly_reference_txins,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + spend_raw.FEE_REDEEM_TXSIZE,
            join_txouts=False,
            script_txins=plutus_txins,
        )

        # check that the reference input was not spent
        assert cluster.g_query.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was spent `{reference_input[0]}`"

        # check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_same_input_as_reference_input(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test use a reference input that is also a regular input of the same transaction.

        * create the necessary Tx outputs
        * use a reference input that is also a regular input and spend the locked UTxO
        * check that the input was spent
        # check "transaction view"
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

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=2_000_000,
            redeem_cost=redeem_cost,
        )

        reference_input = spend_raw._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            amount=amount,
        )

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                datum_file=plutus_op.datum_file,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        tx_output_redeem = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            txins=reference_input,
            txouts=txouts_redeem,
            readonly_reference_txins=reference_input,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + spend_raw.FEE_REDEEM_TXSIZE,
            join_txouts=False,
            script_txins=plutus_txins,
        )

        # check that the reference input was spent
        assert not cluster.g_query.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was NOT spent `{reference_input[0]}`"

        # check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_reference_input_non_plutus(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test using a read-only reference input in non-Plutus transaction.

        * use a reference input in normal non-Plutus transaction
        * check that the reference input was not spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        reference_input = spend_raw._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=src_addr,
            amount=amount,
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=src_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            readonly_reference_txins=reference_input,
            tx_files=tx_files,
        )

        # check that the reference input was not spent
        assert cluster.g_query.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was spent `{reference_input[0]}`"

        # check expected balances
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        # check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)


@pytest.mark.testnets
class TestNegativeReadonlyReferenceInputs:
    """Tests for Tx with readonly reference inputs that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_reference_spent_output(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test use a reference input that was already spent.

        Expect failure
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

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
        )

        reference_input = spend_raw._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            amount=amount,
        )

        reference_utxo = f"{reference_input[0].utxo_hash}#{reference_input[0].utxo_ix}"

        #  spend the output that will be used as reference input

        cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_spend_reference_input",
            txins=reference_input,
            txouts=[clusterlib.TxOut(address=payment_addrs[0].address, amount=-1)],
            tx_files=clusterlib.TxFiles(signing_key_files=[payment_addrs[1].skey_file]),
        )

        # check that the input used also as reference was spent
        reference_input_utxo = cluster.g_query.get_utxo(txin=reference_utxo)

        assert not reference_input_utxo, f"The reference input was NOT spent `{reference_utxo}`"

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                datum_file=plutus_op.datum_file,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                txouts=txouts_redeem,
                readonly_reference_txins=reference_input,
                tx_files=tx_files_redeem,
                fee=redeem_cost.fee + spend_raw.FEE_REDEEM_TXSIZE,
                join_txouts=False,
                script_txins=plutus_txins,
            )
        err_str = str(excinfo.value)
        assert re.search(
            f'TranslationLogicMissingInput .*unTxId = SafeHash "{reference_input[0].utxo_hash}"',
            err_str,
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_v1_script_with_reference_input(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test use a reference input with a v1 Plutus script.

        Expect failure
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
        )

        # create the reference input
        reference_input = spend_raw._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=amount,
        )

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                datum_file=plutus_op.datum_file,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                txouts=txouts_redeem,
                tx_files=tx_files_redeem,
                fee=redeem_cost.fee + spend_raw.FEE_REDEEM_TXSIZE,
                readonly_reference_txins=reference_input,
                join_txouts=False,
                script_txins=plutus_txins,
            )
        err_str = str(excinfo.value)
        assert "ReferenceInputsNotSupported" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    def test_reference_input_without_spend_anything(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test using a read-only reference input without spending any UTxO.

        Expect failure
        """
        temp_template = common.get_test_id(cluster)
        reference_input_amount = 2_000_000

        # create the necessary Tx outputs

        reference_input = spend_raw._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=reference_input_amount,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--babbage-era",
                    "--fee",
                    f"{200_000}",
                    "--read-only-tx-in-reference",
                    f"{reference_input[0].utxo_hash}#{reference_input[0].utxo_ix}",
                    "--tx-out",
                    f"{payment_addrs[1].address}+{2_000_000}",
                    "--out-file",
                    f"{temp_template}_tx.body",
                ]
            )
        err_str = str(excinfo.value)
        assert "Missing: (--tx-in TX-IN)" in err_str, err_str
