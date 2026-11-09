"""Minting integration and execution budgets for CIP-0156 multiIndexArray (PV12)."""

import dataclasses
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


SCRIPTS = plutus_common.MULTI_INDEX_ARRAY_SCRIPTS

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
    """Cluster with multiIndexArray costing for all tested ledger/Core combinations."""

    cluster: clusterlib.ClusterLib
    # Reasons to skip ledger/Core combinations, keyed like `SCRIPTS`
    skip_reasons: dict[str, str]


def _get_decode_skip_reason(cluster_obj: clusterlib.ClusterLib, script_file: pl.Path) -> str:
    """Return a skip reason for known batch7 decoding incompatibilities."""
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
    the cost models of all tested languages already price multiIndexArray. Both Core
    versions reuse these models. Skip the whole test when the PlutusV3/PLC 1.1.0
    baseline has a known batch7 decoding incompatibility. Check the same decoding
    incompatibilities separately for the other ledger/Core combinations.
    """
    cluster = cluster_plutus
    pparams = cluster.g_query.get_protocol_params()
    if pparams["protocolVersion"]["major"] < 12:
        pytest.skip("multiIndexArray requires protocol version 12 or later")

    # Check CLI batch7 activation before governance setup
    skip_reason_v3 = _get_decode_skip_reason(
        cluster_obj=cluster, script_file=SCRIPTS["v3_110"].succeeding[0].script_file
    )
    if skip_reason_v3:
        pytest.skip(skip_reason_v3)
    skip_reasons = {}
    for variant, scripts in SCRIPTS.items():
        if scripts.cost_model == "PlutusV4" and VERSIONS.transaction_era < VERSIONS.DIJKSTRA_FIRST:
            skip_reasons[variant] = "Plutus V4 is available only in Dijkstra+ eras"
            continue

        skip_reason = _get_decode_skip_reason(
            cluster_obj=cluster, script_file=scripts.succeeding[0].script_file
        )
        if skip_reason:
            skip_reasons[variant] = skip_reason

    tested_cost_models = {s.cost_model for v, s in SCRIPTS.items() if v not in skip_reasons}

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

        Run the existing scenarios for PlutusV3 and PlutusV4, each compiled for PLC
        1.1.0 and 1.2.0. All four combinations reuse a single cost-model setup and
        minting/budget helpers. Skip known batch7 decoding incompatibilities for each
        combination and both PlutusV4 variants before the Dijkstra transaction era.
        Other decoder errors fail setup.

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

        for variant, scripts in SCRIPTS.items():
            skip_reason = setup.skip_reasons.get(variant)
            if skip_reason:
                with subtests.test(variant=variant):
                    pytest.skip(skip_reason)
                continue

            self._check_variant(
                cluster_manager=cluster_manager,
                cluster_obj=setup.cluster,
                temp_template=f"{temp_template}_{variant}",
                variant=variant,
                scripts=scripts,
                subtests=subtests,
            )

    def _check_variant(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        variant: str,
        scripts: plutus_common.MultiIndexArrayScripts,
        subtests: pytest_subtests.SubTests,
    ) -> None:
        """Run minting and budget scenarios for one ledger/Core combination."""
        cluster = cluster_obj
        over_limit_stem = next(
            s.script_file.stem for s in scripts.failing if "OverLimitPolicy" in s.script_file.stem
        )

        # Mint with all policies and collect the execution costs of successful mints
        mints = mint_build.run_plutusv3_builtins_test(
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            temp_template=temp_template,
            variant=f"multi_index_array_{variant}",
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

        with subtests.test(variant=variant, check="policy_budget_growth"):
            assert len(repeated_costs) == 3, (
                "Expected budgets for requests of 1, 64 and 1024 indices"
            )
            for resource in ("steps", "memory"):
                assert (
                    repeated_costs["1"][resource]
                    < repeated_costs["64"][resource]
                    < repeated_costs["1024"][resource]
                ), f"Policy execution {resource} did not grow with request size: {repeated_costs}"

        with subtests.test(variant=variant, check="execution_budget"):
            self._check_execution_budget(
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                temp_template=f"{temp_template}_budget",
                variant=variant,
                script=next(
                    s for s in scripts.succeeding if "Repeated1024Policy" in s.script_file.stem
                ),
                subtests=subtests,
            )

    def _check_execution_budget(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        variant: str,
        script: plutus_common.PlutusScriptData,
        subtests: pytest_subtests.SubTests,
    ) -> None:
        """Reject insufficient CPU and memory budgets, then mint with the measured budget.

        Evaluate the 1,024-index policy in a minting transaction using the node's cost
        model. Submit two otherwise valid raw transactions, first with one fewer CPU
        unit and then with one fewer memory unit, and require exhaustion in each case.
        Check the inputs and collateral remain unspent. Finally submit the same mint
        with the measured execution units and confirm its token on chain and in db-sync.
        """
        cluster = cluster_obj
        pparams = cluster.g_query.get_protocol_params()
        max_units = pparams["maxTxExecutionUnits"]
        payment_addr, issuer_addr = addrs_common.get_payment_addrs(
            name_template=temp_template,
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
            fund_idx=[0],
            caching_key=f"{helpers.get_current_line_str()}_{variant}",
            amount=1_000_000_000,
            min_amount=100_000_000,
        )
        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.ExecutionCost(
                per_time=max_units["steps"], per_space=max_units["memory"], fixed_cost=0
            ),
            protocol_params=pparams,
        )
        funds = mint_build.fund_issuer_batch(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_costs=[minting_cost],
            amount=mint_build.SCRIPT_FUND,
        )[0]
        policyid = cluster.g_transaction.get_policyid(script.script_file)
        token = f"{policyid}.{temp_template.encode().hex()[:64]}"
        token_out = clusterlib.TxOut(address=issuer_addr.address, amount=1, coin=token)
        txouts = [clusterlib.TxOut(address=issuer_addr.address, amount=2_000_000), token_out]
        tx_files = clusterlib.TxFiles(signing_key_files=[issuer_addr.skey_file])
        mint = clusterlib.Mint(
            txouts=[token_out],
            script_file=script.script_file,
            collaterals=funds.collateral_utxos,
            redeemer_file=plutus_common.REDEEMER_42,
        )
        costs = cluster.g_transaction.calculate_plutus_script_cost(
            src_address=issuer_addr.address,
            tx_name=f"{temp_template}_cost",
            txins=funds.mint_utxos,
            txouts=txouts,
            mint=[mint],
            tx_files=tx_files,
        )
        assert len(costs) == 1, "Expected one script cost record"
        units = costs[0]["executionUnits"]
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=issuer_addr.address,
            tx_name=f"{temp_template}_fee",
            txins=funds.mint_utxos,
            txouts=txouts,
            mint=[dataclasses.replace(mint, execution_units=(units["steps"], units["memory"]))],
            tx_files=tx_files,
        )

        for name, budget in (
            ("cpu", (units["steps"] - 1, units["memory"])),
            ("memory", (units["steps"], units["memory"] - 1)),
            ("sufficient", (units["steps"], units["memory"])),
        ):
            with subtests.test(variant=variant, budget=name):
                tx_name = f"{temp_template}_{name}"
                tx_output = cluster.g_transaction.build_raw_tx(
                    src_address=issuer_addr.address,
                    tx_name=tx_name,
                    txins=funds.mint_utxos,
                    txouts=txouts,
                    mint=[dataclasses.replace(mint, execution_units=budget)],
                    tx_files=tx_files,
                    fee=fee,
                )
                signed = cluster.g_transaction.sign_tx(
                    tx_body_file=tx_output.out_file,
                    signing_key_files=tx_files.signing_key_files,
                    tx_name=tx_name,
                )
                if name != "sufficient":
                    with pytest.raises(clusterlib.CLIError) as excinfo:
                        cluster.g_transaction.submit_tx_bare(tx_file=signed)
                    exc_value = str(excinfo.value)
                    with common.allow_unstable_error_messages():
                        assert "overspending the budget" in exc_value, exc_value

                    for utxo in (*funds.mint_utxos, *funds.collateral_utxos):
                        assert cluster.g_query.get_utxo(utxo=utxo), "Rejected mint spent an input"
                    continue

                cluster.g_transaction.submit_tx(tx_file=signed, txins=funds.mint_utxos)
                out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
                assert clusterlib.calculate_utxos_balance(out_utxos, coin=token) == 1
                db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
                if db_record:
                    dbsync_utils.check_plutus_costs(
                        redeemer_records=db_record.redeemers, cost_records=costs
                    )
