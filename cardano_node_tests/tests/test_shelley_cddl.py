"""Test for Shelley Tx with CDDL format.

Tests presence of bug https://github.com/input-output-hk/cardano-node/issues/3688.

The test is meant to run in non-CDDL Shelley Tx testing job. It doesn't make sense to run full
CDDL Shelley Tx testing job until the bug above is fixed.

Once the bug above is fixed, this whole test file can be deleted.
"""
import logging
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS


LOGGER = logging.getLogger(__name__)


@pytest.mark.skipif(
    VERSIONS.transaction_era != VERSIONS.SHELLEY,
    reason="runs only with Shelley TX",
)
class TestShelleyCDDL:
    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        if cluster.use_cddl:
            pytest.skip("runs only when `cluster.use_cddl == False`")

        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                f"addr_shelley_cddl_ci{cluster_manager.cluster_instance_num}_0",
                f"addr_shelley_cddl_ci{cluster_manager.cluster_instance_num}_1",
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_shelley_cddl(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Check expected failure when Shelley Tx is used with CDDL format."""
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        # amount value -1 means all available funds
        destinations = [clusterlib.TxOut(address=dst_address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[1].skey_file])

        fee = cluster.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )

        orig_cddl_value = cluster.use_cddl
        try:
            cluster.use_cddl = True
            tx_raw_output = cluster.build_raw_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
                fee=fee,
            )
        finally:
            cluster.use_cddl = orig_cddl_value

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
        if "TextEnvelope error" in str(excinfo.value):
            pytest.xfail("TextEnvelope error")
        else:
            pytest.fail(f"Unexpected error:\n{excinfo.value}")
