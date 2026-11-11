"""CIP-0168 Value builtin minting scenarios for any supplied ledger/Core variant."""

import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import builtins_mint_build as mint_build
from cardano_node_tests.utils import dbsync_utils

SCRIPTS = plutus_common.VALUE_BUILTIN_SCRIPTS


class ValueBuiltinsChecks:
    """Run CIP-0168 policies through the shared builtin minting runner."""

    def run(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        variant: str,
        scripts: plutus_common.ValueBuiltinScripts,
        subtests: pytest_subtests.SubTests,
    ) -> None:
        """Mint with Value policies and reject invalid filter lists while building.

        The caller checks language/protocol/CLI availability and enables costing
        before selecting a ledger/Core variant. Exercise policies, keepPolicies,
        dropPolicies and assetCount. Cover empty/ADA values, ordering, filtering,
        duplicates, absent/overlong IDs and the 256/257 input-list boundary.
        The size policies are ordinary successful mints. Check successful
        transactions and costs in db-sync when available.
        """
        mints = mint_build.run_builtins_test(
            cluster_manager=cluster_manager,
            cluster_obj=cluster_obj,
            temp_template=temp_template,
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

        # Verify the successful transactions and measured execution costs.
        for minted in mints:
            db_record = dbsync_utils.check_tx(
                cluster_obj=cluster_obj, tx_raw_output=minted.tx_output
            )
            if db_record:
                dbsync_utils.check_plutus_costs(
                    redeemer_records=db_record.redeemers, cost_records=minted.plutus_costs
                )
