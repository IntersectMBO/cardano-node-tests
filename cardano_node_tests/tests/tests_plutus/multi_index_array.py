"""Minting integration and execution budgets for CIP-0156 multiIndexArray (PV12)."""

import dataclasses
import logging
import pathlib as pl

import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import builtins_mint_build as mint_build
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

SCRIPTS = plutus_common.MULTI_INDEX_ARRAY_SCRIPTS


class MultiIndexArrayChecks:
    """Exercise multiIndexArray minting and budgets for any supplied ledger/Core variant."""

    def run(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        variant: str,
        scripts: plutus_common.MultiIndexArrayScripts,
        subtests: pytest_subtests.SubTests,
    ) -> None:
        """Mint with valid selections and reject invalid requests while building transactions.

        The caller checks language/protocol/CLI availability and enables costing
        before selecting a ledger/Core variant. All variants reuse the shared
        cost-model setup and minting/budget helpers.

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
        cluster = cluster_obj
        over_limit_stem = next(
            s.script_file.stem for s in scripts.failing if "OverLimitPolicy" in s.script_file.stem
        )

        # Mint with all policies and collect the execution costs of successful mints
        mints = mint_build.run_builtins_test(
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
