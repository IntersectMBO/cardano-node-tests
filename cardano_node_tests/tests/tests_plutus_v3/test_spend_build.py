"""Tests for spending with Plutus using `transaction build`."""

import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import spend_build
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_PLUTUSV3_UNUSABLE,
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


class TestBuildLocking:
    """Tests for Tx output locking using Plutus smart contracts and `transaction build`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_txout_locking_no_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        No datum is provided. Datum for spending scripts is optional in PlutusV3.

        Uses `cardano-cli transaction build` command for building the transactions.

        * Create a Tx output without a datum hash at the script address
        * Check that the expected amount was locked at the script address
        * Spend the locked UTxO without providing a datum
        * Check that the expected amount was spent
        * Check expected fees
        * Check expected Plutus cost
        * (optional) Check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        # No datum is provided
        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v3"].script_file,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v3"].execution_cost,
        )

        script_utxos, collateral_utxos, tx_output_fund = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=1_000_000,
        )

        __, tx_output, plutus_costs = spend_build._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=-1,
        )

        # Check expected fees
        expected_fee_fund = 168_845
        assert common.is_fee_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        expected_fee = 170_782
        assert tx_output and common.is_fee_in_interval(tx_output.fee, expected_fee, frac=0.15)

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[plutus_common.ALWAYS_SUCCEEDS["v3"].execution_cost],
        )
