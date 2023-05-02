"""Tests for ro reference inputs while spending with Plutus V2 using `transaction build`."""
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
from cardano_node_tests.tests.tests_plutus_v2 import spend_build
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view

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
class TestReadonlyReferenceInputs:
    """Tests for Tx with readonly reference inputs."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
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
        * (optional) check transactions in db-sync
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_op = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS

        reference_input_amount = 2_000_000

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_inline_datum=False,
        )

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=reference_input_amount,
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

        if reference_input_scenario == "single":
            readonly_reference_txins = reference_input
        else:
            readonly_reference_txins = reference_input * 2

        tx_output_redeem = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            readonly_reference_txins=readonly_reference_txins,
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

        # check that the reference input was not spent
        assert cluster.g_query.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was spent `{reference_input[0]}`"

        expected_redeem_fee = 172_578
        assert helpers.is_in_interval(
            tx_output_redeem.fee, expected_redeem_fee, frac=0.15
        ), "Expected fee doesn't match the actual fee"

        # check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

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
        * check that input was spent
        * check "transaction view"
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_op = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS

        reference_input_amount = 2_000_000

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_inline_datum=False,
        )

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            amount=reference_input_amount,
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

        tx_output_redeem = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            txins=reference_input,
            tx_files=tx_files_redeem,
            readonly_reference_txins=reference_input,
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

        # check that the input used also as reference was spent
        assert not cluster.g_query.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was NOT spent `{reference_input[0]}`"

        # check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

    @allure.link(helpers.get_vcs_link())
    def test_use_same_reference_input_multiple_times(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test 2 transactions using the same reference input in the same block.

        * create the UTxO that will be used as readonly reference input
        * create the transactions using the same readonly reference input
        * submit both transactions
        * check that the readonly reference input was not spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        # fund payment address
        clusterlib_utils.fund_from_faucet(
            payment_addrs[1],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        # create the reference input

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=amount,
        )

        #  build 2 tx using the same readonly reference input

        tx_address_combinations = [
            {"payment_addr": payment_addrs[0], "dst_addr": payment_addrs[1]},
            {"payment_addr": payment_addrs[1], "dst_addr": payment_addrs[0]},
        ]

        tx_to_submit = []
        txins = []

        for addr in tx_address_combinations:
            txouts = [clusterlib.TxOut(address=addr["dst_addr"].address, amount=amount)]
            tx_files = clusterlib.TxFiles(signing_key_files=[addr["payment_addr"].skey_file])

            tx_output = cluster.g_transaction.build_tx(
                src_address=addr["payment_addr"].address,
                tx_name=f"{temp_template}_{addr['payment_addr'].address}_tx",
                tx_files=tx_files,
                txouts=txouts,
                readonly_reference_txins=reference_input,
            )

            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_{addr['payment_addr'].address}_signed",
            )

            tx_to_submit.append(tx_signed)

            txins += tx_output.txins

        for tx_signed in tx_to_submit:
            cluster.g_transaction.submit_tx_bare(tx_file=tx_signed)

        clusterlib_utils.check_txins_spent(cluster_obj=cluster, txins=txins)

        # check that the reference input was not spent
        assert cluster.g_query.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was spent `{reference_input[0]}`"

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

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=src_addr,
            amount=amount,
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]

        tx_output = cluster.g_transaction.build_tx(
            src_address=src_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            readonly_reference_txins=reference_input,
            tx_files=tx_files,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        # check that the reference input was not spent
        assert cluster.g_query.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was spent `{reference_input[0]}`"

        # check expected balances
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        # check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)


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

        plutus_op = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS

        reference_input_amount = 2_000_000

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_inline_datum=False,
        )

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            amount=reference_input_amount,
        )

        #  spend the output that will be used as reference input

        tx_output_spend_reference_input = cluster.g_transaction.build_tx(
            src_address=payment_addrs[1].address,
            tx_name=f"{temp_template}_step2",
            txins=reference_input,
            txouts=[clusterlib.TxOut(address=payment_addrs[0].address, amount=-1)],
        )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_spend_reference_input.out_file,
            signing_key_files=[payment_addrs[1].skey_file],
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(
            tx_file=tx_signed, txins=tx_output_spend_reference_input.txins
        )

        # check that the input used also as reference was spent
        assert not cluster.g_query.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was NOT spent `{reference_input[0]}`"

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
            cluster.g_transaction.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                readonly_reference_txins=reference_input,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
            )
        err_str = str(excinfo.value)
        assert (
            # TODO: in 1.35.3 and older - cardano-node issue #4012
            "following tx input(s) were not present in the UTxO: \n"
            f"{reference_input[0].utxo_hash}" in err_str
            or re.search(
                "TranslationLogicMissingInput .*unTxId = SafeHash "
                f'"{reference_input[0].utxo_hash}"',
                err_str,
            )
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_v1_script_with_reference_input(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test use a reference input with a v1 Plutus script.

        Expect failure
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

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

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_inline_datum=False,
        )

        # create the reference input

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=2_000_000,
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
            cluster.g_transaction.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                readonly_reference_txins=reference_input,
                txouts=txouts_redeem,
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

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=reference_input_amount,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build",
                    "--read-only-tx-in-reference",
                    f"{reference_input[0].utxo_hash}#{reference_input[0].utxo_ix}",
                    "--change-address",
                    payment_addrs[0].address,
                    "--tx-out",
                    f"{payment_addrs[1].address}+{2_000_000}",
                    "--out-file",
                    f"{temp_template}_tx.body",
                    "--testnet-magic",
                    str(cluster.network_magic),
                    *cluster.g_transaction.tx_era_arg,
                ]
            )
        err_str = str(excinfo.value)
        assert "Missing: (--tx-in TX-IN)" in err_str, err_str
