"""Tests for datum while spending with Plutus using `transaction build`."""
import json
import logging
import string
from pathlib import Path
from typing import Any
from typing import List

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
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
class TestInlineDatum:
    """Tests for Tx output with inline datum."""

    @allure.link(helpers.get_vcs_link())
    def test_check_inline_datum_cost(self, cluster: clusterlib.ClusterLib):
        """Check that the min UTxO value with an inline datum depends on the size of the datum.

        * calculate the min UTxO value with a small datum, using both inline and hash
        * calculate the min UTxO value with a big datum, using both inline and hash
        * check that the min UTxO value with an inline datum depends on datum size
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = spend_build.PLUTUS_OP_ALWAYS_SUCCEEDS
        assert plutus_op.datum_file

        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=plutus_op.script_file
        )

        # small datum

        txouts_with_small_inline_datum = [
            clusterlib.TxOut(
                address=script_address,
                amount=2_000_000,
                inline_datum_file=plutus_op.datum_file,
            )
        ]

        min_utxo_small_inline_datum = cluster.g_transaction.calculate_min_req_utxo(
            txouts=txouts_with_small_inline_datum
        ).value

        expected_min_small_inline_datum = 892_170
        assert helpers.is_in_interval(
            min_utxo_small_inline_datum, expected_min_small_inline_datum, frac=0.15
        )

        small_datum_hash = cluster.g_transaction.get_hash_script_data(
            script_data_file=plutus_op.datum_file
        )

        txouts_with_small_datum_hash = [
            clusterlib.TxOut(
                address=script_address,
                amount=2_000_000,
                datum_hash=small_datum_hash,
            )
        ]

        min_utxo_small_datum_hash = cluster.g_transaction.calculate_min_req_utxo(
            txouts=txouts_with_small_datum_hash
        ).value

        expected_min_small_datum_hash = 1_017_160
        assert helpers.is_in_interval(
            min_utxo_small_datum_hash, expected_min_small_datum_hash, frac=0.15
        )

        # big datum

        txouts_with_big_inline_datum = [
            clusterlib.TxOut(
                address=script_address,
                amount=2_000_000,
                inline_datum_file=plutus_common.DATUM_BIG,
            )
        ]

        min_utxo_big_inline_datum = cluster.g_transaction.calculate_min_req_utxo(
            txouts=txouts_with_big_inline_datum
        ).value

        expected_min_big_inline_datum = 1_193_870
        assert helpers.is_in_interval(
            min_utxo_big_inline_datum, expected_min_big_inline_datum, frac=0.15
        )

        big_datum_hash = cluster.g_transaction.get_hash_script_data(
            script_data_file=plutus_common.DATUM_BIG
        )

        txouts_with_big_datum_hash = [
            clusterlib.TxOut(
                address=script_address,
                amount=2_000_000,
                datum_hash=big_datum_hash,
            )
        ]

        min_utxo_big_datum_hash = cluster.g_transaction.calculate_min_req_utxo(
            txouts=txouts_with_big_datum_hash
        ).value

        expected_min_big_datum_hash = 1_017_160
        assert helpers.is_in_interval(
            min_utxo_big_datum_hash, expected_min_big_datum_hash, frac=0.15
        )

        # check that the min UTxO value with an inline datum depends on the size of the datum

        assert (
            min_utxo_small_inline_datum < min_utxo_small_datum_hash
            and min_utxo_big_inline_datum > min_utxo_big_datum_hash
            and min_utxo_big_inline_datum > min_utxo_small_inline_datum
        ), "The min UTxO value doesn't correspond to the inline datum size"


@pytest.mark.testnets
class TestNegativeInlineDatum:
    """Tests for Tx output with inline datum that are expected to fail."""

    @pytest.fixture
    def pbt_script_address(
        self,
        cluster: clusterlib.ClusterLib,
    ) -> str:
        """Get Plutus script address.

        Meant for property-based tests, so this expensive operation gets executed only once.
        """
        temp_template = common.get_test_id(cluster)

        script_address_v2 = cluster.g_address.gen_payment_addr(
            addr_name=temp_template,
            payment_script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
        )
        return script_address_v2

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(datum_value=st.text())
    @common.hypothesis_settings()
    def test_lock_tx_invalid_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        datum_value: str,
    ):
        """Test locking a Tx output with an invalid datum.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump(f'{{"{datum_value}"}}', outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_file=Path(datum_file),
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        # create a Tx output with an invalid inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
            )
        err_str = str(excinfo.value)
        assert "JSON object expected. Unexpected value" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_lock_tx_v1_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with an inline datum and a v1 script.

        Expect failure.
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
        assert plutus_op.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address
        script_utxos, collateral_utxos, __, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
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
        assert (
            "Error translating the transaction context: InlineDatumsNotSupported" in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(datum_content=st.text(alphabet=string.ascii_letters, min_size=65))
    @common.hypothesis_settings(max_examples=100)
    def test_lock_tx_big_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_script_address: str,
        datum_content: str,
    ):
        """Test locking a Tx output with a datum bigger than the allowed size.

        Expect failure on node version < 1.36.0.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_value=f'"{datum_content}"',
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )
        assert plutus_op.execution_cost  # for mypy

        # Create a Tx output with a datum hash at the script address

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        script_txout = plutus_common.txout_factory(
            address=pbt_script_address,
            amount=8_000_000,
            plutus_op=plutus_op,
            inline_datum=True,
        )

        err_str = ""
        try:
            cluster.g_transaction.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step1",
                tx_files=tx_files,
                txouts=[script_txout],
                fee_buffer=2_000_000,
            )
        except clusterlib.CLIError as exc:
            err_str = str(exc)

        assert (
            not err_str or "must consist of at most 64 bytes" in err_str  # in node version < 1.36.0
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_lock_tx_datum_as_witness(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test unlock a Tx output with a datum as witness.

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
        script_utxos, collateral_utxos, __, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

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
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx(
                tx_file=tx_signed,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        err_str = str(excinfo.value)
        assert "NonOutputSupplimentaryDatums" in err_str, err_str
