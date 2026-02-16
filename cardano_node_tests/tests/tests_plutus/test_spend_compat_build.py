"""Compatibility tests for spending with Plutus using `transaction build`."""

import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import spend_build
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_BUILD_UNUSABLE,
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
        amount=1_000_000_000,
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

        * Try to spend the locked UTxO
        * Check that the expected error was raised
        * (optional) Check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v2"].script_file,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v2"].execution_cost,
        )

        script_utxos, collateral_utxos, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=100_000,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=-1,
                submit_tx=False,
            )

        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "PlutusScriptV2 is not supported" in exc_value, exc_value
