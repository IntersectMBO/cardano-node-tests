"""Tests for node upgrade."""
import json
import logging
import os
import shutil
from pathlib import Path
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import artifacts
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)

ALONZO_GENESIS_SPEC = (
    Path(__file__).parent.parent / "cluster_scripts" / "babbage" / "genesis.alonzo.spec.json"
)

UPGRADE_TESTS_STEP = int(os.environ.get("UPGRADE_TESTS_STEP") or 0)
BASE_REVISION = os.environ.get("BASE_REVISION")
UPGRADE_REVISION = os.environ.get("UPGRADE_REVISION")

pytestmark = [
    pytest.mark.skipif(not UPGRADE_TESTS_STEP, reason="not upgrade testing"),
]


@pytest.fixture
def cluster_locked(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    return cluster_manager.get(lock_resources=[cluster_management.Resources.CLUSTER])


@pytest.fixture
def payment_addrs_locked(
    cluster_manager: cluster_management.ClusterManager,
    cluster_locked: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    cluster = cluster_locked
    temp_template = common.get_test_id(cluster)

    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_payment_addr_0",
            f"{temp_template}_payment_addr_1",
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


@pytest.fixture
def payment_addrs_disposable(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new disposable payment addresses."""
    temp_template = common.get_test_id(cluster)

    addrs = clusterlib_utils.create_payment_addr_records(
        f"{temp_template}_payment_addr_disposable_0",
        f"{temp_template}_payment_addr_disposable_1",
        cluster_obj=cluster,
    )

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )

    return addrs


class TestSetup:
    """Tests for setting up cardano network before and during upgrade testing.

    Special tests that run outside of normal test run.
    """

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP < 2, reason="runs only on step >= 2 of upgrade testing")
    def test_ignore_log_errors(
        self,
        cluster_locked: clusterlib.ClusterLib,
        worker_id: str,
    ):
        """Ignore selected errors in log right after node upgrade."""
        cluster = cluster_locked
        common.get_test_id(cluster)

        # Ignore ledger replay when upgrading from node version 1.34.1.
        # The error message appears only right after the node is upgraded. This ignore rule has
        # effect only in this test.
        if BASE_REVISION == "1.34.1":
            logfiles.add_ignore_rule(
                files_glob="*.stdout",
                regex="ChainDB:Error:.* Invalid snapshot DiskSnapshot .*DeserialiseFailure 168",
                ignore_file_id=worker_id,
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP != 3, reason="runs only on step 3 of upgrade testing")
    def test_update_to_babbage(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_locked: clusterlib.ClusterLib,
        payment_addrs_locked: List[clusterlib.AddressRecord],
    ):
        """Update cluster to Babbage era."""
        cluster = cluster_locked
        temp_template = common.get_test_id(cluster)
        src_addr = payment_addrs_locked[0]

        cluster.wait_for_new_epoch()

        # update to Babbage

        update_proposal_babbage = [
            clusterlib_utils.UpdateProposal(
                arg="--protocol-major-version",
                value=7,
                name="",  # needs custom check
            ),
            clusterlib_utils.UpdateProposal(
                arg="--protocol-minor-version",
                value=0,
                name="",  # needs custom check
            ),
        ]

        clusterlib_utils.update_params(
            cluster_obj=cluster,
            src_addr_record=src_addr,
            update_proposals=update_proposal_babbage,
        )

        cluster.wait_for_new_epoch(padding_seconds=3)

        protocol_params = cluster.g_query.get_protocol_params()
        assert protocol_params["protocolVersion"]["major"] == 7
        assert protocol_params["protocolVersion"]["minor"] == 0

        # update cluster instance - we need to use Babbage-era Tx for now on

        artifacts.save_cli_coverage(
            cluster_obj=cluster, pytest_config=cluster_manager.pytest_config
        )

        configuration.CLUSTER_ERA = "babbage"
        configuration.TX_ERA = "babbage"

        cluster = cluster_nodes.get_cluster_type().get_cluster_obj(tx_era=configuration.TX_ERA)
        cluster_manager.cache.cluster_obj = cluster

        # update cost model

        with open(ALONZO_GENESIS_SPEC, encoding="utf-8") as genesis_fp:
            alonzo_genesis_spec = json.load(genesis_fp)
        cost_models = alonzo_genesis_spec["costModels"]

        cost_models_file = Path(f"{temp_template}_cost_values.json").resolve()
        with open(cost_models_file, "w", encoding="utf-8") as out_fp:
            out_fp.write(json.dumps(cost_models, indent=4))

        update_proposal_cost_model = [
            clusterlib_utils.UpdateProposal(
                arg="--cost-model-file",
                value=str(cost_models_file),
                name="",  # needs custom check
            ),
        ]

        clusterlib_utils.update_params(
            cluster_obj=cluster,
            src_addr_record=src_addr,
            update_proposals=update_proposal_cost_model,
        )

        cluster.wait_for_new_epoch(padding_seconds=3)

        protocol_params = cluster.g_query.get_protocol_params()
        assert protocol_params["costModels"]["PlutusScriptV2"]["bData-memory-arguments"] == 32


@pytest.mark.upgrade
class TestUpgrade:
    """Tests for node upgrade testing."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP > 2, reason="doesn't run on step > 2 of upgrade testing")
    @pytest.mark.order(-1)
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize(
        "for_step",
        (
            pytest.param(
                2,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP == 2, reason="doesn't run on step 2 of upgrade testing"
                ),
            ),
            pytest.param(
                3,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP == 3, reason="doesn't run on step 3 of upgrade testing"
                ),
            ),
        ),
    )
    @pytest.mark.parametrize("file_type", ("tx", "tx_body"))
    def test_prepare_tx(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addrs_disposable: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
        for_step: int,
        file_type: str,
    ):
        """Prepare transactions that will be submitted in next steps of upgrade testing.

        For testing that transaction created by previous node version and/or in previous era can
        be submitted in next node version and/or next era.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}_{for_step}_{file_type}"
        build_str = "build" if use_build_cmd else "build_raw"

        src_address = payment_addrs_disposable[0].address
        dst_address = payment_addrs_disposable[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs_disposable[0].skey_file])

        if use_build_cmd:
            tx_raw_output = cluster.g_transaction.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                tx_files=tx_files,
                txouts=destinations,
                fee_buffer=1_000_000,
            )
            out_file_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
        else:
            fee = cluster.g_transaction.calculate_tx_fee(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
            )
            tx_raw_output = cluster.g_transaction.build_raw_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
                fee=fee,
            )
            out_file_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )

        copy_files = [
            payment_addrs_disposable[0].skey_file,
            tx_raw_output.out_file,
            out_file_signed,
        ]

        tx_dir = (
            temptools.get_basetemp()
            / cluster_manager.cache.last_checksum
            / f"{UPGRADE_TESTS_STEP}for{for_step}"
            / file_type
            / build_str
        ).resolve()

        if tx_dir.exists():
            shutil.rmtree(tx_dir)
        tx_dir.mkdir(parents=True)

        for f in copy_files:
            shutil.copy(f, tx_dir)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP < 2, reason="runs only on step >= 2 of upgrade testing")
    @pytest.mark.order(5)
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize(
        "for_step",
        (
            pytest.param(
                2,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP != 2, reason="runs only on step 2 of upgrade testing"
                ),
            ),
            pytest.param(
                3,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP != 3, reason="runs only on step 3 of upgrade testing"
                ),
            ),
        ),
    )
    @pytest.mark.parametrize(
        "from_step",
        (
            1,
            pytest.param(
                2,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP == 2, reason="doesn't run on step 2 of upgrade testing"
                ),
            ),
        ),
    )
    @pytest.mark.parametrize("file_type", ("tx", "tx_body"))
    def test_submit_tx(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        use_build_cmd: bool,
        for_step: int,
        from_step: int,
        file_type: str,
    ):
        """Submit transaction that was created by previous node version and/or in previous era."""
        temp_template = (
            f"{common.get_test_id(cluster)}_{use_build_cmd}_{from_step}_{for_step}_{file_type}"
        )
        build_str = "build" if use_build_cmd else "build_raw"

        tx_dir = (
            temptools.get_basetemp()
            / cluster_manager.cache.last_checksum
            / f"{from_step}for{for_step}"
            / file_type
            / build_str
        ).resolve()

        if not tx_dir.exists():
            pytest.skip("No tx files found")

        tx_file = list(tx_dir.glob("*.signed"))[0]
        tx_body_file = list(tx_dir.glob("*.body"))[0]
        skey_file = list(tx_dir.glob("*.skey"))[0]

        if file_type == "tx_body":
            tx_file = cluster.g_transaction.sign_tx(
                tx_body_file=tx_body_file,
                tx_name=temp_template,
                signing_key_files=[skey_file],
            )

        cluster.g_transaction.submit_tx_bare(tx_file=tx_file)
        cluster.wait_for_new_block(2)
