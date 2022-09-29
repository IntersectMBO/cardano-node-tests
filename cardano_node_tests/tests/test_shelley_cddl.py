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


@pytest.mark.smoke
@pytest.mark.skipif(
    VERSIONS.transaction_era != VERSIONS.SHELLEY,
    reason="runs only with Shelley TX",
)
class TestShelleyCDDL:
    @pytest.fixture(scope="class")
    def skip_cddl(self):
        if not clusterlib_utils.cli_has("transaction build-raw --cddl-format"):
            pytest.skip("The `--cddl-format` option is no longer available.")

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create 2 new payment addresses."""
        if cluster.use_cddl:
            pytest.skip("runs only when `cluster.use_cddl == False`")

        addrs = clusterlib_utils.create_payment_addr_records(
            f"addr_shelley_cddl_ci{cluster_manager.cluster_instance_num}_0",
            f"addr_shelley_cddl_ci{cluster_manager.cluster_instance_num}_1",
            cluster_obj=cluster,
        )

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    def test_shelley_cddl(
        self,
        skip_cddl: None,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Check expected failure when Shelley Tx is used with CDDL format."""
        # pylint: disable=unused-argument
        temp_template = common.get_test_id(cluster)

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        # amount value -1 means all available funds
        destinations = [clusterlib.TxOut(address=dst_address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

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

        err = ""
        try:
            cluster.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
        except clusterlib.CLIError as exc:
            err = str(exc)

        if "TextEnvelope error" in err:
            pytest.xfail("TextEnvelope error")
        elif err:
            pytest.fail(f"Unexpected error:\n{err}")

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
