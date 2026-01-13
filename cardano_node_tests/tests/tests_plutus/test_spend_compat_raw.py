"""Compatibility tests for spending with Plutus using `transaction build-raw`."""

import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import spend_raw
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
        num=3,
        fund_idx=[0],
        amount=3_000_000_000,
    )
    return addrs


class TestCompatibility:
    """Tests for checking compatibility with previous Tx eras."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era > VERSIONS.ALONZO,
        reason="doesn't run with Tx era > Alonzo",
    )
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_plutusv2_old_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test spending a UTxO locked with PlutusV2 script using old Tx era.

        Expect failure.

        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 1_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v2"].script_file,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v2"].execution_cost,
        )

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "PlutusScriptV2 is not supported" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era >= VERSIONS.ALONZO,
        reason="runs only with Tx era < Alonzo",
    )
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_plutusv1_old_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test spending a UTxO locked with PlutusV1 script using old Tx era.

        Expect failure.

        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 1_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v1"].script_file,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v1"].execution_cost,
        )

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "PlutusScriptV1 is not supported" in exc_value, exc_value
