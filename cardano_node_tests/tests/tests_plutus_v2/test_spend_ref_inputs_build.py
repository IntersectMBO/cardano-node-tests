"""Tests for ro reference inputs while spending with Plutus V2 using `transaction build`."""

import logging
import re
import typing as tp

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
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_BUILD_UNUSABLE,
    common.SKIPIF_PLUTUSV2_UNUSABLE,
    pytest.mark.plutus,
]


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    addrs = common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=2,
        fund_idx=[0],
        amount=1_000_000_000,
    )
    return addrs


class TestReadonlyReferenceInputs:
    """Tests for Tx with readonly reference inputs."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("reference_input_scenario", ("single", "duplicated"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_use_reference_input(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        reference_input_scenario: str,
    ):
        """Test use a reference input when unlock some funds.

        * create the necessary Tx outputs
        * use a reference input and spend the locked UTxO
        * check that the reference input was not spent
        * (optional) check transactions in db-sync
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_op = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS

        script_fund = 1_000_000
        reference_input_amount = 2_000_000

        # For mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # Create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=script_fund,
            use_inline_datum=False,
        )

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=reference_input_amount,
        )

        #  Spend the "locked" UTxO

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
            signing_key_files=[payment_addrs[0].skey_file, payment_addrs[1].skey_file],
        )
        fee_txin_redeem = next(
            r
            for r in clusterlib_utils.get_just_lovelace_utxos(
                address_utxos=cluster.g_query.get_utxo(address=payment_addrs[0].address)
            )
            if r.amount >= 100_000_000
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=script_fund),
        ]

        if reference_input_scenario == "single":
            readonly_reference_txins = reference_input
        else:
            readonly_reference_txins = reference_input * 2

        tx_output_redeem = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            txins=[fee_txin_redeem],
            tx_files=tx_files_redeem,
            readonly_reference_txins=readonly_reference_txins,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
            change_address=payment_addrs[0].address,
        )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
        )

        # Check that the reference input was not spent
        assert cluster.g_query.get_utxo(utxo=reference_input[0]), (
            f"The reference input was spent `{reference_input[0]}`"
        )

        expected_redeem_fee = 172_578
        assert common.is_fee_in_interval(tx_output_redeem.fee, expected_redeem_fee, frac=0.15), (
            "Expected fee doesn't match the actual fee"
        )

        # Check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_same_input_as_reference_input(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test use a reference input that is also a regular input of the same transaction.

        * create the necessary Tx outputs
        * use a reference input that is also a regular input and spend the locked UTxO
        * check that input was spent
        * check "transaction view"
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_op = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS

        script_fund = 1_000_000
        reference_input_amount = 2_000_000

        # For mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # Create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=script_fund,
            use_inline_datum=False,
        )

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            amount=reference_input_amount,
        )

        #  Spend the "locked" UTxO

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
            signing_key_files=[payment_addrs[0].skey_file, payment_addrs[1].skey_file],
        )
        fee_txin_redeem = next(
            r
            for r in clusterlib_utils.get_just_lovelace_utxos(
                address_utxos=cluster.g_query.get_utxo(address=payment_addrs[0].address)
            )
            if r.amount >= 100_000_000
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=script_fund),
        ]

        tx_output_redeem = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            txins=[*reference_input, fee_txin_redeem],
            tx_files=tx_files_redeem,
            readonly_reference_txins=reference_input,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
            change_address=payment_addrs[0].address,
        )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        try:
            cluster.g_transaction.submit_tx(
                tx_file=tx_signed,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        except clusterlib.CLIError as exc:
            if VERSIONS.transaction_era < VERSIONS.CONWAY:
                raise
            if "BabbageNonDisjointRefInputs" not in str(exc):
                raise
            return

        # Check that the input used also as reference was spent
        assert not cluster.g_query.get_utxo(utxo=reference_input[0]), (
            f"The reference input was NOT spent `{reference_input[0]}`"
        )

        # Check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_use_same_reference_input_multiple_times(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test 2 transactions using the same reference input in the same block.

        * create the UTxO that will be used as readonly reference input
        * create the transactions using the same readonly reference input
        * submit both transactions
        * check that the readonly reference input was not spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        # Fund payment address
        clusterlib_utils.fund_from_faucet(
            payment_addrs[1],
            cluster_obj=cluster,
            all_faucets=cluster_manager.cache.addrs_data,
            amount=10_000_000,
        )

        # Create the reference input

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=amount,
        )

        #  Build 2 tx using the same readonly reference input

        tx_address_combinations = [
            {"payment_addr": payment_addrs[0], "dst_addr": payment_addrs[1]},
            {"payment_addr": payment_addrs[1], "dst_addr": payment_addrs[0]},
        ]

        tx_to_submit = []
        txins = []

        for i, addr in enumerate(tx_address_combinations):
            txouts = [clusterlib.TxOut(address=addr["dst_addr"].address, amount=amount)]
            tx_files = clusterlib.TxFiles(signing_key_files=[addr["payment_addr"].skey_file])

            tx_output = cluster.g_transaction.build_tx(
                src_address=addr["payment_addr"].address,
                tx_name=f"{temp_template}_{i}_tx",
                tx_files=tx_files,
                txouts=txouts,
                readonly_reference_txins=reference_input,
            )

            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_{i}_signed",
            )

            tx_to_submit.append(tx_signed)

            txins += tx_output.txins

        for tx_signed in tx_to_submit:
            cluster.g_transaction.submit_tx_bare(tx_file=tx_signed)

        clusterlib_utils.check_txins_spent(cluster_obj=cluster, txins=txins)

        # Check that the reference input was not spent
        assert cluster.g_query.get_utxo(utxo=reference_input[0]), (
            f"The reference input was spent `{reference_input[0]}`"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_reference_input_non_plutus(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
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

        # Check that the reference input was not spent
        assert cluster.g_query.get_utxo(utxo=reference_input[0]), (
            f"The reference input was spent `{reference_input[0]}`"
        )

        # Check expected balances
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        # Check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)


class TestNegativeReadonlyReferenceInputs:
    """Tests for Tx with readonly reference inputs that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_reference_spent_output(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test use a reference input that was already spent.

        Expect failure
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_op = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS

        script_fund = 1_000_000
        reference_input_amount = 2_000_000

        # For mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # Create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=script_fund,
            use_inline_datum=False,
        )

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            amount=reference_input_amount,
        )

        #  Spend the output that will be used as reference input

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

        # Check that the input used also as reference was spent
        assert not cluster.g_query.get_utxo(utxo=reference_input[0]), (
            f"The reference input was NOT spent `{reference_input[0]}`"
        )

        #  Spend the "locked" UTxO

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
            signing_key_files=[payment_addrs[0].skey_file, payment_addrs[1].skey_file],
        )
        fee_txin_redeem = next(
            r
            for r in clusterlib_utils.get_just_lovelace_utxos(
                address_utxos=cluster.g_query.get_utxo(address=payment_addrs[0].address)
            )
            if r.amount >= 100_000_000
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=script_fund),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                txins=[fee_txin_redeem],
                tx_files=tx_files_redeem,
                readonly_reference_txins=reference_input,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
                change_address=payment_addrs[0].address,
            )
        err_str = str(excinfo.value)
        assert (
            # TODO: in 1.35.3 and older - cardano-node issue #4012
            f"following tx input(s) were not present in the UTxO: \n{reference_input[0].utxo_hash}"
            in err_str
            or re.search(
                "TranslationLogicMissingInput .*unTxId = SafeHash "
                f'"{reference_input[0].utxo_hash}"',
                err_str,
            )
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_v1_script_with_reference_input(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test use a reference input with a v1 Plutus script.

        Expect failure
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)
        script_fund = 1_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )

        # For mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # Create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=script_fund,
            use_inline_datum=False,
        )

        # Create the reference input

        reference_input = spend_build._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=2_000_000,
        )

        #  Spend the "locked" UTxO

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
            signing_key_files=[payment_addrs[0].skey_file, payment_addrs[1].skey_file],
        )
        fee_txin_redeem = next(
            r
            for r in clusterlib_utils.get_just_lovelace_utxos(
                address_utxos=cluster.g_query.get_utxo(address=payment_addrs[0].address)
            )
            if r.amount >= 100_000_000
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=script_fund),
        ]

        try:
            cluster.g_transaction.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                txins=[fee_txin_redeem],
                tx_files=tx_files_redeem,
                readonly_reference_txins=reference_input,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
                change_address=payment_addrs[0].address,
            )
        except clusterlib.CLIError as exc:
            if VERSIONS.transaction_era >= VERSIONS.CONWAY:
                raise
            if "ReferenceInputsNotSupported" not in str(exc):
                raise

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_reference_input_without_spend_anything(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
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
                ]
            )
        err_str = str(excinfo.value)
        assert "Missing: (--tx-in TX-IN)" in err_str, err_str
