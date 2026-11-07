"""Minting integration and execution budgets for CIP-0156 multiIndexArray (PV12)."""

import logging
import pathlib as pl
import typing as tp

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

LOGGER = logging.getLogger(__name__)

MULTI_INDEX_ARRAY_COST_MODEL_LEN = 355


class VersionScripts(tp.NamedTuple):
    """multiIndexArray policies compiled for one Plutus version."""

    cost_model: str
    succeeding: tuple[plutus_common.PlutusScriptData, ...]
    failing: tuple[plutus_common.PlutusScriptData, ...]


# The policies don't read the script context, so both versions share the same code
SCRIPTS = {
    "v3": VersionScripts(
        cost_model="PlutusV3",
        succeeding=plutus_common.SUCCEEDING_MINTING_MULTI_INDEX_ARRAY_SCRIPTS_V3,
        failing=plutus_common.FAILING_MINTING_MULTI_INDEX_ARRAY_SCRIPTS_V3,
    ),
    "v4": VersionScripts(
        cost_model="PlutusV4",
        succeeding=plutus_common.SUCCEEDING_MINTING_MULTI_INDEX_ARRAY_SCRIPTS_V4,
        failing=plutus_common.FAILING_MINTING_MULTI_INDEX_ARRAY_SCRIPTS_V4,
    ),
}

pytestmark = [
    # Skip before the fixture locks governance and Plutus resources
    pytest.mark.skipif(
        VERSIONS.cluster_era < VERSIONS.DIJKSTRA_FIRST,
        reason="multiIndexArray requires protocol version 12 or later",
    ),
    markers.SKIPIF_PLUTUSV3_UNUSABLE,
    markers.SKIPIF_BUILD_UNUSABLE,
    pytest.mark.plutus,
    pytest.mark.team_plutus,
]


class MultiIndexArraySetup(tp.NamedTuple):
    """Cluster with multiIndexArray costing for all Plutus versions that are tested."""

    cluster: clusterlib.ClusterLib
    # Reasons to skip Plutus versions, keyed like `SCRIPTS`
    skip_reasons: dict[str, str]


def _get_decode_skip_reason(cluster_obj: clusterlib.ClusterLib, script_file: pl.Path) -> str:
    """Return the reason to skip a Plutus version if the CLI cannot decode its policy."""
    try:
        cluster_obj.g_transaction.get_policyid(script_file)
    except clusterlib.CLIError as exc:
        # This test requires node/CLI built with the batch7 activation
        # from IntersectMBO/plutus#8006 (da649103).
        if "MultiIndexArray is not available" in str(exc) or "Failed to decode builtin tag" in str(
            exc
        ):
            return f"CLI cannot decode multiIndexArray in {script_file.name} at PV12"
        raise
    return ""


@pytest.fixture
def multi_index_array_setup(
    cluster_manager: cluster_management.ClusterManager,
    cluster_plutus: clusterlib.ClusterLib,
) -> MultiIndexArraySetup:
    """Enable multiIndexArray costing while locking governance and Plutus resources.

    Update the PlutusV3 and PlutusV4 cost models in a single governance action, unless
    the cost models of all tested Plutus versions already price multiIndexArray. Skip
    the whole test when PlutusV3 cannot be tested, and only the PlutusV4 scenarios
    when PlutusV4 cannot be tested.
    """
    cluster = cluster_plutus
    pparams = cluster.g_query.get_protocol_params()
    if pparams["protocolVersion"]["major"] < 12:
        pytest.skip("multiIndexArray requires protocol version 12 or later")

    # Fail before governance setup if the CLI cannot decode PV12 scripts
    skip_reason_v3 = _get_decode_skip_reason(
        cluster_obj=cluster, script_file=SCRIPTS["v3"].succeeding[0].script_file
    )
    if skip_reason_v3:
        pytest.skip(skip_reason_v3)
    skip_reasons = {}
    if VERSIONS.transaction_era < VERSIONS.DIJKSTRA_FIRST:
        skip_reasons["v4"] = "Plutus V4 is available only in Dijkstra+ eras"
    else:
        skip_reason_v4 = _get_decode_skip_reason(
            cluster_obj=cluster, script_file=SCRIPTS["v4"].succeeding[0].script_file
        )
        if skip_reason_v4:
            skip_reasons["v4"] = skip_reason_v4

    tested_cost_models = [s.cost_model for v, s in SCRIPTS.items() if v not in skip_reasons]

    def _is_costing_enabled(cost_models: dict) -> bool:
        return all(
            len(cost_models.get(m, ())) >= MULTI_INDEX_ARRAY_COST_MODEL_LEN
            for m in tested_cost_models
        )

    setup = MultiIndexArraySetup(cluster=cluster, skip_reasons=skip_reasons)
    if _is_costing_enabled(pparams["costModels"]):
        return setup

    # Up to one epoch for delayed ratification, then two for enactment.
    common.skip_on_long_epochs(cluster_obj=cluster, epochs=3)
    name_template = f"{common.get_test_id(cluster)}_cost_model"
    pool_user = addrs_common.get_registered_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster
    )

    # Mark before submission: even a failed check can leave an enacted update.
    cluster_manager.set_needs_respin()
    conway_common.update_cost_model(
        cluster_obj=cluster,
        name_template=name_template,
        governance_data=governance_data,
        # The PlutusV4 parameters have the same names and order as the PlutusV3 ones
        cost_proposal_file=plutus_common.DATA_DIR / "cost_models_list_355_v3_v4.json",
        pool_user=pool_user,
    )
    assert _is_costing_enabled(cluster.g_query.get_protocol_params()["costModels"]), (
        "multiIndexArray cost model was not enacted"
    )
    return setup


class TestMultiIndexArray:
    """Exercise multiIndexArray through complete PlutusV3 and PlutusV4 minting transactions."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(5)
    @pytest.mark.xdist_split(markers.XdSplits.governance, markers.XdSplits.heavy)
    @pytest.mark.long
    @pytest.mark.dbsync
    def test_multi_index_array(
        self,
        cluster_manager: cluster_management.ClusterManager,
        multi_index_array_setup: MultiIndexArraySetup,
        subtests: pytest_subtests.SubTests,
    ):
        """Mint with valid selections and reject invalid requests while building transactions.

        Run all scenarios with the PlutusV3 policies, then with the same policies compiled
        for PlutusV4. Both versions reuse a single cost-model setup. Skip the PlutusV4
        scenarios when the transaction era is older than Dijkstra or when the CLI cannot
        decode the PlutusV4 policies.

        Check ordering, duplicate indices, empty selections, heterogeneous Data, and
        repeated selections of 1, 64 and 1,024 elements. The Data policy selects
        Integer, ByteString and homogeneous List Data. Reject negative, upper-bound,
        huge and late invalid indices, an indexed empty array, and 1,025 valid indices.
        Confirm successful mints on chain and compare their costs with db-sync when
        available. Record budgets for all successful policies and check transaction
        limits. Check whole-policy budget growth with the request size as a sanity
        check, including list construction and result validation in the measured costs.
        Reuse the cost-model setup to reject insufficient CPU and memory budgets,
        then confirm a raw transaction with sufficient execution units.
        """
        setup = multi_index_array_setup
        temp_template = common.get_test_id(setup.cluster)

        for plutus_version, scripts in SCRIPTS.items():
            skip_reason = setup.skip_reasons.get(plutus_version)
            if skip_reason:
                with subtests.test(plutus_version=plutus_version):
                    pytest.skip(skip_reason)
                continue

            self._check_plutus_version(
                cluster_manager=cluster_manager,
                cluster_obj=setup.cluster,
                temp_template=f"{temp_template}_{plutus_version}",
                plutus_version=plutus_version,
                scripts=scripts,
                subtests=subtests,
            )

    def _check_plutus_version(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        plutus_version: str,
        scripts: VersionScripts,
        subtests: pytest_subtests.SubTests,
    ) -> None:
        """Run the minting and budget scenarios with the policies of one Plutus version."""
        cluster = cluster_obj
        over_limit_stem = next(
            s.script_file.stem for s in scripts.failing if "OverLimitPolicy" in s.script_file.stem
        )

        # Mint with all policies and collect the execution costs of successful mints
        mints = mint_build.run_plutusv3_builtins_test(
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            temp_template=temp_template,
            variant=f"multi_index_array_{plutus_version}",
            success_scripts=scripts.succeeding,
            fail_scripts=scripts.failing,
            overspend_scripts=(),
            is_cost_model_ok=True,
            is_prot_version_ok=True,
            subtests=subtests,
            expected_error="multiIndexArray: array index out of bounds",
            expected_errors={
                over_limit_stem: "multiIndexArray: too many indices (maximum is 1024)",
            },
        )

        # Check the budgets of successful mints against the transaction limits
        max_units = cluster.g_query.get_protocol_params()["maxTxExecutionUnits"]
        repeated_costs: dict[str, dict] = {}
        for minted in mints:
            assert minted.tx_output.mint, "Expected a minting transaction"
            script_name = pl.Path(minted.tx_output.mint[0].script_file).stem
            with subtests.test(script=script_name, check="budget"):
                assert len(minted.plutus_costs) == 1, "Expected one script cost record"
                units = minted.plutus_costs[0]["executionUnits"]
                LOGGER.info("multiIndexArray execution budget: %s: %s", script_name, units)
                assert 0 < units["steps"] <= max_units["steps"]
                assert 0 < units["memory"] <= max_units["memory"]
                for count in (1, 64, 1024):
                    if f"Repeated{count}Policy" in script_name:
                        repeated_costs[str(count)] = units
                db_record = dbsync_utils.check_tx(
                    cluster_obj=cluster, tx_raw_output=minted.tx_output
                )
                if db_record:
                    dbsync_utils.check_plutus_costs(
                        redeemer_records=db_record.redeemers, cost_records=minted.plutus_costs
                    )

        with subtests.test(plutus_version=plutus_version, check="policy_budget_growth"):
            assert len(repeated_costs) == 3, (
                "Expected budgets for requests of 1, 64 and 1024 indices"
            )
            for resource in ("steps", "memory"):
                assert (
                    repeated_costs["1"][resource]
                    < repeated_costs["64"][resource]
                    < repeated_costs["1024"][resource]
                ), f"Policy execution {resource} did not grow with request size: {repeated_costs}"

        with subtests.test(plutus_version=plutus_version, check="execution_budget"):
            mint_build.check_execution_budget(
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                temp_template=f"{temp_template}_budget",
                plutus_version=plutus_version,
                script=next(
                    s for s in scripts.succeeding if "Repeated1024Policy" in s.script_file.stem
                ),
                subtests=subtests,
            )
