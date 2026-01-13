"""Compatibility tests for spending with Plutus V2 using `transaction build-raw`."""

import logging
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus_v2 import spend_raw
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = [
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
        amount=3_000_000_000,
    )
    return addrs


class TestCompatibility:
    """Tests for checking compatibility with previous Tx eras."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era >= VERSIONS.BABBAGE,
        reason="runs only with Tx era < Babbage",
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_inline_datum_old_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with an inline datum using old Tx era.

        Expect failure with Alonzo-era Tx.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )

        # For mypy
        assert plutus_op.execution_cost

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Create a Tx output with an inline datum at the script address
        try:
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
        except clusterlib.CLIError as exc:
            if "Inline datums cannot be used" not in str(exc):
                raise
            return

        # Attempt to use Babbage features in older era transactions should fail. If we are here,
        # it was not the case.

        assert script_utxos and not script_utxos[0].inline_datum, "Inline datum was NOT ignored"

        issues.node_4424.finish_test()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era >= VERSIONS.BABBAGE,
        reason="runs only with Tx era < Babbage",
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_reference_script_old_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a reference script using old Tx era."""
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        assert plutus_op.execution_cost

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Create a Tx output with an inline datum at the script address
        try:
            __, __, reference_utxo, *__ = spend_raw._fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
                amount=amount,
                redeem_cost=redeem_cost,
                use_reference_script=True,
                use_inline_datum=False,
            )
        except clusterlib.CLIError as exc:
            if "Reference scripts cannot be used" not in str(exc):
                raise
            return

        # Attempt to use Babbage features in older era transactions should fail. If we are here,
        # it was not the case.

        assert reference_utxo and not reference_utxo.reference_script, (
            "Reference script was NOT ignored"
        )

        issues.node_4424.finish_test()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era >= VERSIONS.BABBAGE,
        reason="runs only with Tx era < Babbage",
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_ro_reference_old_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test building Tx with read-only reference input using old Tx era.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        reference_input = spend_raw._build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=amount,
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        txouts = [clusterlib.TxOut(address=payment_addrs[1].address, amount=amount)]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=payment_addrs[0].address,
                tx_name=temp_template,
                txouts=txouts,
                readonly_reference_txins=reference_input,
                tx_files=tx_files,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "Reference inputs cannot be used" in exc_value, exc_value
