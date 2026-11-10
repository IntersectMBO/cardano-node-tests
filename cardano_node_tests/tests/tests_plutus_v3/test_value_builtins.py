"""CIP-0168 Value builtin minting tests for PlutusV3/V4 at protocol version 12."""

import allure
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import markers
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.tests.tests_plutus_v3 import builtins_mint_build as mint_build
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

VALUE_COST_MODEL_LEN = 369
SCRIPTS = plutus_common.VALUE_BUILTIN_SCRIPTS

pytestmark = [
    pytest.mark.skipif(
        VERSIONS.cluster_era < VERSIONS.DIJKSTRA_FIRST,
        reason="CIP-0168 Value builtins require protocol version 12 or later",
    ),
    markers.SKIPIF_PLUTUSV3_UNUSABLE,
    markers.SKIPIF_PLUTUSV4_UNUSABLE,
    markers.SKIPIF_BUILD_UNUSABLE,
    pytest.mark.plutus,
    pytest.mark.team_plutus,
]


@pytest.fixture
def value_builtins_setup(
    cluster_manager: cluster_management.ClusterManager,
    cluster_plutus: clusterlib.ClusterLib,
) -> clusterlib.ClusterLib:
    """Enable CIP-0168 costing once under governance/Plutus locks.

    Check PV12 and CLI policy decoding before changing governance state. Require
    V3/V4 cost models to have at least 369 parameters, as in the existing batch7
    tests. Mark the cluster for respin before submitting an update. Known CLI
    activation gaps skip the test before governance setup.
    """
    cluster = cluster_plutus
    pparams = cluster.g_query.get_protocol_params()
    if pparams["protocolVersion"]["major"] < 12:
        pytest.skip("CIP-0168 Value builtins require protocol version 12 or later")

    # Check every ledger/Core combination before governance setup.
    for scripts in SCRIPTS.values():
        for script in (*scripts.succeeding, *scripts.failing):
            try:
                cluster.g_transaction.get_policyid(script.script_file)
            except clusterlib.CLIError as exc:
                if "is not available" in str(exc) or "Failed to decode builtin tag" in str(exc):
                    pytest.skip(
                        f"CLI lacks batch7/PV12 support for {script.script_file.name}: {exc}"
                    )
                raise

    def _is_costing_enabled(cost_models: dict) -> bool:
        """Require sufficient parameters for both tested ledger languages."""
        return all(
            len(cost_models.get(language, ())) >= VALUE_COST_MODEL_LEN
            for language in ("PlutusV3", "PlutusV4")
        )

    if _is_costing_enabled(pparams["costModels"]):
        return cluster

    # One initial epoch, up to two delayed waits, then ratification/enactment.
    common.skip_on_long_epochs(cluster_obj=cluster, epochs=5)
    name_template = f"{common.get_test_id(cluster)}_cost_model"
    pool_user = addrs_common.get_registered_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster
    )

    # Mark before submission, since a failing check can leave an enacted update.
    cluster_manager.set_needs_respin()
    conway_common.update_cost_model(
        cluster_obj=cluster,
        name_template=name_template,
        governance_data=governance_data,
        cost_proposal_file=plutus_common.DATA_DIR / "cost_models_list_369_v3_v4.json",
        pool_user=pool_user,
    )
    assert _is_costing_enabled(cluster.g_query.get_protocol_params()["costModels"]), (
        "CIP-0168 cost models were not enacted"
    )
    return cluster


class TestValueBuiltins:
    """Run CIP-0168 policies through the shared batch7 minting runner."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(5)
    @pytest.mark.xdist_split(markers.XdSplits.governance, markers.XdSplits.heavy)
    @pytest.mark.long
    @pytest.mark.dbsync
    def test_value_builtins(
        self,
        cluster_manager: cluster_management.ClusterManager,
        value_builtins_setup: clusterlib.ClusterLib,
        subtests: pytest_subtests.SubTests,
    ):
        """Mint with Value policies and reject invalid filter lists while building.

        Exercise policies, keepPolicies, dropPolicies and assetCount in PlutusV3
        and PlutusV4, each compiled for PLC 1.1.0 and 1.2.0. Cover empty/ADA
        values, ordering, filtering, duplicates, absent/overlong IDs and the
        256/257 input-list boundary. The size policies are ordinary successful
        mints. All combinations share one cost-model setup and the existing
        minting runner. Check successful transactions and costs in db-sync when
        available.
        """
        cluster = value_builtins_setup
        temp_template = common.get_test_id(cluster)

        for variant, scripts in SCRIPTS.items():
            mints = mint_build.run_plutusv3_builtins_test(
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                temp_template=f"{temp_template}_{variant}",
                variant=f"value_{variant}",
                success_scripts=scripts.succeeding,
                fail_scripts=scripts.failing,
                overspend_scripts=(),
                is_cost_model_ok=True,
                is_prot_version_ok=True,
                subtests=subtests,
                expected_errors={
                    script.script_file.stem: (
                        "keepPolicies" if "ValueKeep" in script.script_file.stem else "dropPolicies"
                    )
                    + ": maximum number of policy ids (256) exceeded"
                    for script in scripts.failing
                },
            )

            # Reuse the transaction/cost checks from the existing batch7 test.
            for minted in mints:
                db_record = dbsync_utils.check_tx(
                    cluster_obj=cluster, tx_raw_output=minted.tx_output
                )
                if db_record:
                    dbsync_utils.check_plutus_costs(
                        redeemer_records=db_record.redeemers, cost_records=minted.plutus_costs
                    )
