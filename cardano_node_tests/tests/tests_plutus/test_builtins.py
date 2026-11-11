"""Minting tests for builtin activation and costing across Plutus ledger languages."""

import json
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
from cardano_node_tests.tests.tests_plutus import builtins_mint_build as mint_build
from cardano_node_tests.tests.tests_plutus import multi_index_array
from cardano_node_tests.tests.tests_plutus import value_builtins
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
UPGRADE_TESTS_STEP = helpers.get_env_int("UPGRADE_TESTS_STEP", 0)

# Transaction-era and protocol activation of each ledger language, independently
# of the later protocol versions that activate individual builtin groups.
LANGUAGE_REQUIREMENTS = {
    "PlutusV1": (VERSIONS.ALONZO_FIRST, 5),
    "PlutusV2": (VERSIONS.BABBAGE_FIRST, 7),
    "PlutusV3": (VERSIONS.CONWAY_FIRST, 9),
    "PlutusV4": (VERSIONS.DIJKSTRA_FIRST, 12),
}


class BuiltinGroup(tp.NamedTuple):
    """Scripts and activation requirements for one builtin family/ledger/Core variant."""

    name: str
    batch: int
    variant: str
    language: str
    protocol_version: int
    cost_model_len: int
    succeeding: tuple[plutus_common.PlutusScriptData, ...]
    failing: tuple[plutus_common.PlutusScriptData, ...]
    overspending: tuple[plutus_common.PlutusScriptData, ...] = ()


BUILTIN_GROUPS = (
    BuiltinGroup(
        name="batch5_v3",
        batch=5,
        variant="v3",
        language="PlutusV3",
        protocol_version=10,
        cost_model_len=297,
        succeeding=(
            *plutus_common.SUCCEEDING_MINTING_RIPEMD_160_SCRIPTS_V3,
            *plutus_common.SUCCEEDING_MINTING_BITWISE_SCRIPTS_V3,
        ),
        failing=plutus_common.FAILING_MINTING_BITWISE_SCRIPTS_V3,
    ),
    BuiltinGroup(
        name="batch6_v3",
        batch=6,
        variant="v3",
        language="PlutusV3",
        protocol_version=11,
        cost_model_len=330,
        succeeding=plutus_common.SUCCEEDING_MINTING_BATCH6_SCRIPTS_V3,
        failing=plutus_common.FAILING_MINTING_BATCH6_SCRIPTS_V3,
        overspending=plutus_common.OVERSPENDING_MINTING_BATCH6_SCRIPTS_V3,
    ),
    *(
        BuiltinGroup(
            name=f"batch7_multi_index_array_{variant}",
            batch=7,
            variant=variant,
            language=scripts.cost_model,
            protocol_version=12,
            cost_model_len=355,
            succeeding=scripts.succeeding,
            failing=scripts.failing,
        )
        for variant, scripts in multi_index_array.SCRIPTS.items()
    ),
    *(
        BuiltinGroup(
            name=f"batch7_value_{variant}",
            batch=7,
            variant=variant,
            language=scripts.cost_model,
            protocol_version=12,
            cost_model_len=369,
            succeeding=scripts.succeeding,
            failing=scripts.failing,
        )
        for variant, scripts in value_builtins.SCRIPTS.items()
    ),
)

# Install costing before builtin activation, but never propose a ledger language
# before its own activation. The proposal is filtered to these language keys.
# Start updates at PV10: the shared voting helper casts DRep votes, which are
# forbidden for parameter changes during PV9 bootstrap. PV9 keeps its own model.
COST_MODEL_UPDATES = (
    (12, {"PlutusV3": 369, "PlutusV4": 369}, "cost_models_list_369_v3_v4.json"),
    (10, {"PlutusV3": 369}, "cost_models_list_369_v3_v4.json"),
)

pytestmark = [markers.SKIPIF_PLUTUS_UNUSABLE, markers.SKIPIF_BUILD_UNUSABLE, pytest.mark.plutus]


def _check_language(group: BuiltinGroup, protocol_version: int) -> None:
    """Skip a ledger language unavailable in the transaction era or active protocol."""
    min_era, min_protocol = LANGUAGE_REQUIREMENTS[group.language]
    if VERSIONS.transaction_era < min_era or protocol_version < min_protocol:
        pytest.skip(f"{group.language} requires transaction era {min_era} and PV{min_protocol}+")


def _check_cli_support(cluster: clusterlib.ClusterLib, group: BuiltinGroup) -> None:
    """Skip known CLI activation gaps for an otherwise activated builtin group."""
    for script in (*group.succeeding, *group.failing, *group.overspending):
        try:
            cluster.g_transaction.get_policyid(script.script_file)
        except clusterlib.CLIError as exc:
            if "is not available" in str(exc) or "Failed to decode builtin tag" in str(exc):
                pytest.skip(f"CLI cannot decode {group.name}: {exc}")
            raise


def _get_scripts(group: BuiltinGroup, protocol_version: int) -> BuiltinGroup:
    """Classify batch5 rotate/shift scripts using the active protocol's semantics."""
    if group.name != "batch5_v3":
        return group
    if plutus_common.rotate_shift_bitwise_fails(protocol_version=protocol_version):
        return group._replace(
            failing=(*group.failing, *plutus_common.FAILING_MINTING_ROTATE_SHIFT_SCRIPTS_V3)
        )
    return group._replace(
        succeeding=(*group.succeeding, *plutus_common.SUCCEEDING_MINTING_ROTATE_SHIFT_SCRIPTS_V3)
    )


def update_cost_model(
    cluster: clusterlib.ClusterLib,
    cluster_manager: cluster_management.ClusterManager,
    temp_template: str,
    protocol_version: int,
    cost_models: dict,
) -> None:
    """Install latest costing for active languages, even before builtin activation.

    Propose V3 alone at PV10/PV11, then V3/V4 once both languages are available.
    Leave PV9 bootstrap costing unchanged because the voting helper needs DReps.
    Preserve the restrictions on updates and respins in upgrade tests.
    """
    if UPGRADE_TESTS_STEP and UPGRADE_TESTS_STEP < 3:
        LOGGER.info("Skipping cost model update on upgrade step %s", UPGRADE_TESTS_STEP)
        return

    for proposal in COST_MODEL_UPDATES:
        if protocol_version >= proposal[0]:
            break
    else:
        LOGGER.info("No cost model update for protocol version %s", protocol_version)
        return

    _, required_models, proposal_file = proposal
    if all(len(cost_models.get(lang, ())) >= size for lang, size in required_models.items()):
        return

    # Include initial and delayed-ratification waits as well as enactment.
    common.skip_on_long_epochs(cluster_obj=cluster, epochs=5)
    name_template = f"{temp_template}_cost_model_upd"
    proposal_models = json.loads(
        (plutus_common.DATA_DIR / proposal_file).read_text(encoding="utf-8")
    )
    cost_proposal_file = pl.Path(f"{name_template}.json")
    cost_proposal_file.write_text(
        json.dumps({lang: proposal_models[lang] for lang in required_models}, indent=4),
        encoding="utf-8",
    )

    pool_user = addrs_common.get_registered_pool_user(
        name_template=name_template, cluster_manager=cluster_manager, cluster_obj=cluster
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster
    )

    # A failed submit/check can still leave an enacted update. Keep upgrade state intact.
    if not UPGRADE_TESTS_STEP:
        cluster_manager.set_needs_respin()
    conway_common.update_cost_model(
        cluster_obj=cluster,
        name_template=name_template,
        governance_data=governance_data,
        cost_proposal_file=cost_proposal_file,
        pool_user=pool_user,
    )
    enacted_models = cluster.g_query.get_protocol_params()["costModels"]
    assert all(
        len(enacted_models.get(lang, ())) >= size for lang, size in required_models.items()
    ), "Builtin cost models were not enacted"


class TestPlutusBuiltins:
    """Test builtin activation and costing for every available ledger/Core script group."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.team_plutus
    @pytest.mark.upgrade_step1
    @pytest.mark.upgrade_step2
    @pytest.mark.upgrade_step3
    @pytest.mark.parametrize(
        "group",
        [
            pytest.param(
                group,
                id=group.name,
                marks=pytest.mark.skipif(
                    VERSIONS.transaction_era < LANGUAGE_REQUIREMENTS[group.language][0],
                    reason=f"{group.language} is unavailable in the transaction era",
                ),
            )
            for group in BUILTIN_GROUPS
        ],
    )
    def test_builtins_old(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        subtests: pytest_subtests.SubTests,
        group: BuiltinGroup,
    ):
        """Check pre-activation rejection and missing costing without updating parameters.

        Select the language-specific cost model and builtin activation requirements
        for each supplied group. Test V3 and V4 independently when their ledger
        language is available. Skip only fully enabled groups or known CLI gaps.
        Unpriced batch7 success policies must overspend, preserving the batch5/6
        constant-casing exceptions. Both PLC versions use their ledger's model.
        """
        temp_template = common.get_test_id(cluster)
        pparams = cluster.g_query.get_protocol_params()
        protocol_version = pparams["protocolVersion"]["major"]
        _check_language(group, protocol_version)
        group = _get_scripts(group, protocol_version)
        is_cost_model_ok = (
            len(pparams["costModels"].get(group.language, ())) >= group.cost_model_len
        )
        is_prot_version_ok = protocol_version >= group.protocol_version
        if is_cost_model_ok and is_prot_version_ok:
            pytest.skip(f"Protocol version and cost model are already current for {group.name}")
        if is_prot_version_ok:
            _check_cli_support(cluster, group)

        # Batch7 policies exercise unpriced builtins, unlike the batch5/6 exceptions.
        if group.batch >= 7 and not is_cost_model_ok:
            group = group._replace(
                overspending=(*group.overspending, *group.succeeding), succeeding=()
            )
        variant = f"old_{group.name}_{'prot_ok' if is_prot_version_ok else 'prot_nok'}"
        mint_build.run_builtins_test(
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{variant}",
            variant=variant,
            success_scripts=group.succeeding,
            fail_scripts=group.failing,
            overspend_scripts=group.overspending,
            is_cost_model_ok=is_cost_model_ok,
            is_prot_version_ok=is_prot_version_ok,
            subtests=subtests,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(5)
    @pytest.mark.xdist_split(markers.XdSplits.governance, markers.XdSplits.heavy)
    @pytest.mark.long
    @pytest.mark.dbsync
    @pytest.mark.team_plutus
    @pytest.mark.upgrade_step1
    @pytest.mark.upgrade_step2
    @pytest.mark.upgrade_step3
    def test_builtins_new_cost(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_plutus: clusterlib.ClusterLib,
        subtests: pytest_subtests.SubTests,
    ):
        """Share one supported cost-model update across all applicable script groups.

        Run each ledger/Core variant independently with its language-specific cost
        model and protocol requirements. Unsupported languages and known CLI gaps
        skip only their own groups. Reuse detailed batch7 minting/budget/db-sync
        checks once fully enabled. Do not update before upgrade step3 or respin an
        upgrade cluster. Newly supported groups also participate in upgrade tests.
        Run batch7 V3 before PV12 with updated costing and require protocol
        rejection. Still exercise that rejection if an upgrade step leaves the
        old model in place; only activated groups require sufficient costing here.
        """
        cluster = cluster_plutus
        temp_template = common.get_test_id(cluster)
        pparams = cluster.g_query.get_protocol_params()
        protocol_version = pparams["protocolVersion"]["major"]
        update_cost_model(
            cluster=cluster,
            cluster_manager=cluster_manager,
            temp_template=temp_template,
            protocol_version=protocol_version,
            cost_models=pparams["costModels"],
        )
        cost_models = cluster.g_query.get_protocol_params()["costModels"]

        for original_group in BUILTIN_GROUPS:
            with subtests.test(group=original_group.name):
                _check_language(original_group, protocol_version)
                group = _get_scripts(original_group, protocol_version)
                is_prot_version_ok = protocol_version >= group.protocol_version
                is_cost_model_ok = len(cost_models.get(group.language, ())) >= group.cost_model_len
                if is_prot_version_ok and not is_cost_model_ok:
                    pytest.skip(f"Cost model is not current for {group.name}")
                if is_prot_version_ok:
                    _check_cli_support(cluster, group)
                variant = f"upd_{group.name}_{'prot_ok' if is_prot_version_ok else 'prot_nok'}"
                group_template = f"{temp_template}_{variant}"

                # These guards select specialized success/budget checks, not
                # whether batch7 runs. Before activation, batch7 falls through
                # to the generic runner below to require protocol rejection,
                # even after installing its costing. Each ledger/Core variant
                # is independent, so a CLI gap cannot suppress other variants.
                if group.name.startswith("batch7_multi_index_array_") and is_prot_version_ok:
                    multi_index_array.MultiIndexArrayChecks().run(
                        cluster_manager=cluster_manager,
                        cluster_obj=cluster,
                        temp_template=group_template,
                        variant=group.variant,
                        scripts=multi_index_array.SCRIPTS[group.variant],
                        subtests=subtests,
                    )
                elif group.name.startswith("batch7_value_") and is_prot_version_ok:
                    value_builtins.ValueBuiltinsChecks().run(
                        cluster_manager=cluster_manager,
                        cluster_obj=cluster,
                        temp_template=group_template,
                        variant=group.variant,
                        scripts=value_builtins.SCRIPTS[group.variant],
                        subtests=subtests,
                    )
                else:
                    # Run batches 5/6 here, and batch7 before its activation.
                    # Deliberately do not guard this call on is_prot_version_ok:
                    # False tells the runner to require rejection, proving that
                    # updated costing alone cannot activate future builtins.
                    mint_build.run_builtins_test(
                        cluster_manager=cluster_manager,
                        cluster_obj=cluster,
                        temp_template=group_template,
                        variant=variant,
                        success_scripts=group.succeeding,
                        fail_scripts=group.failing,
                        overspend_scripts=group.overspending,
                        is_cost_model_ok=is_cost_model_ok,
                        is_prot_version_ok=is_prot_version_ok,
                        subtests=subtests,
                    )
