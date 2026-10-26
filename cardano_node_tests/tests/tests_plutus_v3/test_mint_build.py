"""Tests for minting with Plutus using `transaction build`."""

import logging
import pathlib as pl

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
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

DATA_DIR = pl.Path(__file__).parent.parent / "data"
UPGRADE_TESTS_STEP = helpers.get_env_int("UPGRADE_TESTS_STEP", 0)

# Minimum protocol version required for batch5 built-in functions
BATCH5_PROT_VERSION = 10
# Minimum protocol version required for batch6 built-in functions
BATCH6_PROT_VERSION = 11
# Cost model length including batch5 built-in functions
BATCH5_COST_MODEL_LEN = 297
# Cost model length including batch6 built-in functions
BATCH6_COST_MODEL_LEN = 330

pytestmark = [
    markers.SKIPIF_PLUTUSV3_UNUSABLE,
    pytest.mark.plutus,
]


def update_cost_model(
    cluster_obj: clusterlib.ClusterLib,
    cluster_manager: cluster_management.ClusterManager,
    temp_template: str,
    prot_version: int,
    cost_model_len: int,
) -> None:
    """Update cost model to include values for new Plutus Core built-in functions."""
    name_template = f"{temp_template}_cost_model_upd"

    if prot_version >= BATCH6_PROT_VERSION:
        if cost_model_len >= BATCH6_COST_MODEL_LEN:
            return
        cost_proposal_file = DATA_DIR / "cost_models_list_332_350_v2_v3.json"
    elif prot_version == BATCH5_PROT_VERSION:
        if cost_model_len >= BATCH5_COST_MODEL_LEN:
            return
        cost_proposal_file = DATA_DIR / "cost_models_list_185_297_v2_v3.json"
    else:
        LOGGER.warning(
            "Unsupported protocol version %s for updating cost model, skipping update.",
            prot_version,
        )
        return

    pool_user = addrs_common.get_registered_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster_obj
    )
    conway_common.update_cost_model(
        cluster_obj=cluster_obj,
        name_template=name_template,
        governance_data=governance_data,
        cost_proposal_file=cost_proposal_file,
        pool_user=pool_user,
    )


class TestPlutusV3Builtins:
    """Tests for new batches of Plutus Core built-in functions."""

    batch5_success_scripts = (
        *plutus_common.SUCCEEDING_MINTING_RIPEMD_160_SCRIPTS_V3,
        *plutus_common.SUCCEEDING_MINTING_BITWISE_SCRIPTS_V3,
    )
    batch5_fail_scripts = plutus_common.FAILING_MINTING_BITWISE_SCRIPTS_V3
    batch5_overspend_scripts = ()

    batch6_success_scripts = plutus_common.SUCCEEDING_MINTING_BATCH6_SCRIPTS_V3
    batch6_fail_scripts = plutus_common.FAILING_MINTING_BATCH6_SCRIPTS_V3
    batch6_overspend_scripts = plutus_common.OVERSPENDING_MINTING_BATCH6_SCRIPTS_V3

    def _get_scripts(
        self, batch: int, prot_version: int
    ) -> tuple[
        tuple[plutus_common.PlutusScriptData, ...], tuple[plutus_common.PlutusScriptData, ...]
    ]:
        """Get success and fail scripts for the given batch.

        The rotate/shift bitwise scripts (batch 5) succeed or fail depending on the actual
        protocol version of the cluster, so they are classified at runtime.
        """
        success_scripts = tuple(getattr(self, f"batch{batch}_success_scripts"))
        fail_scripts = tuple(getattr(self, f"batch{batch}_fail_scripts"))

        if batch == 5:
            if plutus_common.rotate_shift_bitwise_fails(protocol_version=prot_version):
                fail_scripts = (
                    *fail_scripts,
                    *plutus_common.FAILING_MINTING_ROTATE_SHIFT_SCRIPTS_V3,
                )
            else:
                success_scripts = (
                    *success_scripts,
                    *plutus_common.SUCCEEDING_MINTING_ROTATE_SHIFT_SCRIPTS_V3,
                )

        return success_scripts, fail_scripts

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.team_plutus
    @pytest.mark.upgrade_step1
    @pytest.mark.upgrade_step2
    @pytest.mark.upgrade_step3
    @pytest.mark.parametrize(
        ("batch", "cost_model_required", "prot_version_required"),
        (
            (5, BATCH5_COST_MODEL_LEN, BATCH5_PROT_VERSION),
            (6, BATCH6_COST_MODEL_LEN, BATCH6_PROT_VERSION),
        ),
        ids=("batch5", "batch6"),
    )
    def test_plutusv3_builtins_old(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        subtests: pytest_subtests.SubTests,
        batch: int,
        cost_model_required: int,
        prot_version_required: int,
    ):
        """Test minting with a batch of Plutus Core built-in functions.

        Run tests with the old cost model and possibly also old protocol version.

        Expect correct behavior (errors) depending on whether the protocol version
        supports the new built-in functions or not.
        """
        temp_template = common.get_test_id(cluster)

        pparams_init = cluster.g_query.get_protocol_params()
        cost_model_len_init = len(pparams_init["costModels"]["PlutusV3"])
        prot_version = pparams_init["protocolVersion"]["major"]

        is_cost_model_ok = cost_model_len_init >= cost_model_required
        is_prot_version_ok = prot_version >= prot_version_required

        # Run tests only when the corresponding cost model isn't up-to-date, otherwise
        # we would be repeating the same tests in `test_plutusv3_builtins_new_cost`.
        if is_cost_model_ok:
            pytest.skip(
                f"Cost model is already up-to-date, skipping batch{batch} old cost model tests."
            )

        success_scripts, fail_scripts = self._get_scripts(batch=batch, prot_version=prot_version)

        variant = f"old_batch{batch}_{'prot_ok' if is_prot_version_ok else 'prot_nok'}"
        mint_build.run_plutusv3_builtins_test(
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{variant}",
            variant=variant,
            success_scripts=success_scripts,
            fail_scripts=fail_scripts,
            overspend_scripts=getattr(self, f"batch{batch}_overspend_scripts"),
            is_cost_model_ok=is_cost_model_ok,
            is_prot_version_ok=is_prot_version_ok,
            subtests=subtests,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(5)
    @pytest.mark.xdist_split(markers.XdSplits.governance, markers.XdSplits.heavy)
    @pytest.mark.long
    @pytest.mark.team_plutus
    @pytest.mark.upgrade_step1
    @pytest.mark.upgrade_step2
    @pytest.mark.upgrade_step3
    def test_plutusv3_builtins_new_cost(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_plutus: clusterlib.ClusterLib,
        subtests: pytest_subtests.SubTests,
    ):
        """Test minting with the new batches of Plutus Core built-in functions.

        * When needed, update cost model to include new built-in functions
        * Run tests with the updated cost model

        Expect correct behavior (errors or success) depending on whether the protocol version
        supports the new built-in functions or not.

        All batches are tested in a single test as each batch needs cost model update, and it would
        not be practical to update cost model multiple times in separate tests.
        """
        cluster = cluster_plutus
        # 2 epochs for cost model update, up to 1 epoch for delayed ratification
        common.skip_on_long_epochs(cluster_obj=cluster, epochs=3)
        temp_template = common.get_test_id(cluster)

        pparams_init = cluster.g_query.get_protocol_params()
        cost_model_len_init = len(pparams_init["costModels"]["PlutusV3"])
        prot_version = pparams_init["protocolVersion"]["major"]

        is_batch5_prot_version_ok = prot_version >= BATCH5_PROT_VERSION
        is_batch6_prot_version_ok = prot_version >= BATCH6_PROT_VERSION

        def _get_variant(batch: int) -> str:
            if batch == 5:
                prot_part = "prot_ok" if is_batch5_prot_version_ok else "prot_nok"
            elif batch == 6:
                prot_part = "prot_ok" if is_batch6_prot_version_ok else "prot_nok"
            else:
                err = f"Unsupported batch number {batch}"
                raise ValueError(err)

            return f"batch{batch}_{prot_part}"

        # Update cost model, if not already updated

        if UPGRADE_TESTS_STEP and UPGRADE_TESTS_STEP < 3:
            LOGGER.info(
                "Skipping cost model update on step %s of upgrade testing", UPGRADE_TESTS_STEP
            )
            cost_model_len_updated = cost_model_len_init
        else:
            update_cost_model(
                cluster_obj=cluster,
                cluster_manager=cluster_manager,
                temp_template=temp_template,
                prot_version=prot_version,
                cost_model_len=cost_model_len_init,
            )
            cost_model_len_updated = len(
                cluster.g_query.get_protocol_params()["costModels"]["PlutusV3"]
            )
            # The cluster needs respin if cost model was updated.
            # Don't try to respin if the test runs as part of upgrade testing.
            if not UPGRADE_TESTS_STEP and cost_model_len_updated != cost_model_len_init:
                cluster_manager.set_needs_respin()

            if prot_version >= BATCH6_PROT_VERSION:
                assert cost_model_len_updated >= BATCH6_COST_MODEL_LEN
            elif prot_version >= BATCH5_PROT_VERSION:
                assert cost_model_len_updated >= BATCH5_COST_MODEL_LEN

        # Run tests with the updated cost model

        if cost_model_len_updated < BATCH5_COST_MODEL_LEN:
            pytest.skip("Cost model is not updated, skipping new cost model tests.")

        batch5_success_scripts, batch5_fail_scripts = self._get_scripts(
            batch=5, prot_version=prot_version
        )

        batch5_variant_updated = f"upd_{_get_variant(batch=5)}"
        mint_build.run_plutusv3_builtins_test(
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{batch5_variant_updated}",
            variant=batch5_variant_updated,
            success_scripts=batch5_success_scripts,
            fail_scripts=batch5_fail_scripts,
            overspend_scripts=self.batch5_overspend_scripts,
            is_cost_model_ok=True,
            is_prot_version_ok=is_batch5_prot_version_ok,
            subtests=subtests,
        )

        if cost_model_len_updated >= BATCH6_COST_MODEL_LEN:
            batch6_variant_updated = f"upd_{_get_variant(batch=6)}"
            mint_build.run_plutusv3_builtins_test(
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                temp_template=f"{temp_template}_{batch6_variant_updated}",
                variant=batch6_variant_updated,
                success_scripts=self.batch6_success_scripts,
                fail_scripts=self.batch6_fail_scripts,
                overspend_scripts=self.batch6_overspend_scripts,
                is_cost_model_ok=True,
                is_prot_version_ok=is_batch6_prot_version_ok,
                subtests=subtests,
            )
