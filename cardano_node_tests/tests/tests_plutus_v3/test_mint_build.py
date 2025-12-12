"""Tests for minting with Plutus using `transaction build`."""

import logging
import os
import pathlib as pl
import typing as tp

import allure
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.tests.tests_plutus import mint_build
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

DATA_DIR = pl.Path(__file__).parent.parent / "data"
UPGRADE_TESTS_STEP = int(os.environ.get("UPGRADE_TESTS_STEP") or 0)

BATCH5_PROT_VERSION = 10
BATCH6_PROT_VERSION = 11
BATCH5_COST_MODEL_LEN = 297
BATCH6_COST_MODEL_LEN = 298  # TODO: replace with actual length when available

pytestmark = [
    common.SKIPIF_PLUTUSV3_UNUSABLE,
    pytest.mark.plutus,
]


@pytest.fixture
def cluster_plutus(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Mark whole governance and Plutus as "locked"."""
    cluster_obj = cluster_manager.get(
        use_resources=cluster_management.Resources.ALL_POOLS,
        lock_resources=[
            cluster_management.Resources.COMMITTEE,
            cluster_management.Resources.DREPS,
            cluster_management.Resources.PLUTUS,
        ],
    )
    return cluster_obj


def update_cost_model(
    cluster_obj: clusterlib.ClusterLib,
    cluster_manager: cluster_management.ClusterManager,
    temp_template: str,
    prot_version: int,
    cost_model_len: int,
) -> None:
    """Update cost model to include values for new Plutus Core built-in functions."""
    if prot_version == BATCH5_PROT_VERSION:
        if cost_model_len >= BATCH5_COST_MODEL_LEN:
            return
        cost_proposal_file = DATA_DIR / "cost_models_list_185_297_v2_v3.json"
    elif prot_version == BATCH6_PROT_VERSION:
        if cost_model_len >= BATCH6_COST_MODEL_LEN:
            return
        # TODO: replace with proper cost model file when available
        cost_proposal_file = DATA_DIR / "cost_models_list_185_297_v2_v3.json"
    else:
        err = f"Unsupported protocol version {prot_version} for updating cost model"
        raise RuntimeError(err)

    pool_user = common.get_registered_pool_user(
        name_template=temp_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster_obj
    )
    conway_common.update_cost_model(
        cluster_obj=cluster_obj,
        name_template=temp_template,
        governance_data=governance_data,
        cost_proposal_file=cost_proposal_file,
        pool_user=pool_user,
    )


def run_scenario(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    payment_addrs: list[clusterlib.AddressRecord],
    plutus_v_record: plutus_common.PlutusScriptData,
    is_success_script: bool,
    is_cost_model_ok: bool,
    is_prot_version_ok: bool,
):
    """Run an e2e test for a Plutus builtin."""
    payment_addr = payment_addrs[0]
    issuer_addr = payment_addrs[1]

    lovelace_amount = 2_000_000
    token_amount = 5
    script_fund = 10_000_000

    minting_cost = plutus_common.compute_cost(
        execution_cost=plutus_v_record.execution_cost,
        protocol_params=cluster_obj.g_query.get_protocol_params(),
    )

    # Step 1: fund the token issuer and create UTXO for collaterals

    mint_utxos, collateral_utxos, _tx_output_step1 = mint_build._fund_issuer(
        cluster_obj=cluster_obj,
        temp_template=temp_template,
        payment_addr=payment_addr,
        issuer_addr=issuer_addr,
        minting_cost=minting_cost,
        amount=script_fund,
    )

    # Step 2: mint the "qacoin"

    policyid = cluster_obj.g_transaction.get_policyid(plutus_v_record.script_file)
    asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
    token = f"{policyid}.{asset_name}"
    mint_txouts = [clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)]

    plutus_mint_data = [
        clusterlib.Mint(
            txouts=mint_txouts,
            script_file=plutus_v_record.script_file,
            collaterals=collateral_utxos,
            redeemer_file=plutus_common.REDEEMER_42,
        )
    ]

    tx_files_step2 = clusterlib.TxFiles(
        signing_key_files=[issuer_addr.skey_file],
    )
    txouts_step2 = [
        clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
        *mint_txouts,
    ]

    try:
        tx_output_step2 = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster_obj,
            name_template=f"{temp_template}_step2",
            src_address=payment_addr.address,
            build_method=clusterlib_utils.BuildMethods.BUILD,
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
        )
    except clusterlib.CLIError as excp:
        str_excp = str(excp)
        if (
            not is_prot_version_ok
            and "not available in language PlutusV3 at and protocol version" in str_excp
        ):
            return
        if not is_cost_model_ok and (
            "The machine terminated part way through evaluation due to "
            "overspending the budget." in str_excp
        ):
            return
        if not is_success_script and "The machine terminated because of an error" in str_excp:
            return
        raise

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output_step2)
    token_utxo = clusterlib.filter_utxos(utxos=out_utxos, address=issuer_addr.address, coin=token)
    assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"


def run_plutusv3_builtins_test(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    variant: str,
    success_scripts: tp.Iterable[plutus_common.PlutusScriptData],
    fail_scripts: tp.Iterable[plutus_common.PlutusScriptData],
    is_cost_model_ok: bool,
    is_prot_version_ok: bool,
    subtests: pytest_subtests.SubTests,
):
    """Run minting tests with the tested Plutus Core built-in functions."""
    payment_addrs = common.get_payment_addrs(
        name_template=temp_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        num=2,
        fund_idx=[0],
        caching_key="plutusv3_builtins_batch_testing",
        amount=700_000_000,
        min_amount=300_000_000,
    )

    for script in success_scripts:
        script_stem = script.script_file.stem
        with subtests.test(variant=f"{variant}_{script_stem}"):
            run_scenario(
                cluster_obj=cluster_obj,
                temp_template=f"{temp_template}_{script_stem}",
                payment_addrs=payment_addrs,
                plutus_v_record=script,
                is_success_script=True,
                is_cost_model_ok=is_cost_model_ok,
                is_prot_version_ok=is_prot_version_ok,
            )

    for script in fail_scripts:
        script_stem = script.script_file.stem
        with subtests.test(variant=f"{variant}_{script_stem}"):
            run_scenario(
                cluster_obj=cluster_obj,
                temp_template=f"{temp_template}_{script_stem}",
                payment_addrs=payment_addrs,
                plutus_v_record=script,
                is_success_script=False,
                is_cost_model_ok=is_cost_model_ok,
                is_prot_version_ok=is_prot_version_ok,
            )


class TestPlutusV3Builtins:
    """Tests for new batches of Plutus Core built-in functions."""

    batch5_success_scripts = (
        *plutus_common.SUCCEEDING_MINTING_RIPEMD_160_SCRIPTS_V3,
        *plutus_common.SUCCEEDING_MINTING_BITWISE_SCRIPTS_V3,
    )
    batch5_fail_scripts = plutus_common.FAILING_MINTING_BITWISE_SCRIPTS_V3

    batch6_success_scripts = plutus_common.SUCCEEDING_MINTING_BATCH6_SCRIPTS_V3
    batch6_fail_scripts = plutus_common.FAILING_MINTING_BATCH6_SCRIPTS_V3

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.team_plutus
    @pytest.mark.upgrade_step1
    @pytest.mark.upgrade_step2
    @pytest.mark.upgrade_step3
    def test_plutusv3_builtins(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_plutus: clusterlib.ClusterLib,
        subtests: pytest_subtests.SubTests,
    ):
        """Test minting with the new batches of Plutus Core built-in functions.

        * Query initial protocol parameters
        * Run tests with the initial cost model
        * Update cost model to include new built-in functions
        * Run tests with the updated cost model

        All batches are tested in a single test as each batch needs cost model update, and it would
        not be practical to update cost model multiple times in separate tests.
        """
        cluster = cluster_plutus
        temp_template = common.get_test_id(cluster)

        pparams_init = cluster.g_query.get_protocol_params()
        cost_model_len_init = len(pparams_init["costModels"]["PlutusV3"])
        prot_version = pparams_init["protocolVersion"]["major"]

        is_batch5_cost_model_ok = cost_model_len_init >= BATCH5_COST_MODEL_LEN
        is_batch5_prot_version_ok = prot_version >= BATCH5_PROT_VERSION
        is_batch6_cost_model_ok = cost_model_len_init >= BATCH6_COST_MODEL_LEN
        is_batch6_prot_version_ok = prot_version >= BATCH6_PROT_VERSION

        def _get_variant(batch: int, cost_model_len: int) -> str:
            if batch == 5:
                prot_part = "prot_ok" if is_batch5_prot_version_ok else "prot_nok"
                cost_part = "cost_ok" if cost_model_len >= BATCH5_COST_MODEL_LEN else "cost_nok"
            elif batch == 6:
                prot_part = "prot_ok" if is_batch6_prot_version_ok else "prot_nok"
                cost_part = "cost_ok" if cost_model_len >= BATCH6_COST_MODEL_LEN else "cost_nok"
            else:
                err = f"Unsupported batch number {batch}"
                raise ValueError(err)

            return f"batch{batch}_{prot_part}_{cost_part}"

        # Step 1: run tests with the initial cost model

        batch5_variant_init = f"init_{_get_variant(batch=5, cost_model_len=cost_model_len_init)}"
        run_plutusv3_builtins_test(
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{batch5_variant_init}",
            variant=batch5_variant_init,
            success_scripts=self.batch5_success_scripts,
            fail_scripts=self.batch5_fail_scripts,
            is_cost_model_ok=is_batch5_cost_model_ok,
            is_prot_version_ok=is_batch5_prot_version_ok,
            subtests=subtests,
        )

        batch6_variant_init = f"init_{_get_variant(batch=6, cost_model_len=cost_model_len_init)}"
        run_plutusv3_builtins_test(
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{batch6_variant_init}",
            variant=batch6_variant_init,
            success_scripts=self.batch6_success_scripts,
            fail_scripts=self.batch6_fail_scripts,
            is_cost_model_ok=is_batch6_cost_model_ok,
            is_prot_version_ok=is_batch6_prot_version_ok,
            subtests=subtests,
        )

        # Step 2: update cost model, if not already updated

        if UPGRADE_TESTS_STEP and UPGRADE_TESTS_STEP < 3:
            LOGGER.info(
                "Skipping cost model update on step %s of upgrade testing", UPGRADE_TESTS_STEP
            )
            return

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

        # Step 3: run tests with the updated cost model

        # Re-run tests only when the corresponding cost model was updated, othervise we would be
        # repeating the same tests as in Step 1.

        if cost_model_len_init < BATCH5_COST_MODEL_LEN <= cost_model_len_updated:
            batch5_variant_updated = (
                f"upd_{_get_variant(batch=5, cost_model_len=cost_model_len_updated)}"
            )
            run_plutusv3_builtins_test(
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                temp_template=f"{temp_template}_{batch5_variant_updated}",
                variant=batch5_variant_updated,
                success_scripts=self.batch5_success_scripts,
                fail_scripts=self.batch5_fail_scripts,
                is_cost_model_ok=True,
                is_prot_version_ok=is_batch5_prot_version_ok,
                subtests=subtests,
            )

        if cost_model_len_init < BATCH6_COST_MODEL_LEN <= cost_model_len_updated:
            batch6_variant_updated = (
                f"upd_{_get_variant(batch=6, cost_model_len=cost_model_len_updated)}"
            )
            run_plutusv3_builtins_test(
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                temp_template=f"{temp_template}_{batch6_variant_updated}",
                variant=batch6_variant_updated,
                success_scripts=self.batch6_success_scripts,
                fail_scripts=self.batch6_fail_scripts,
                is_cost_model_ok=True,
                is_prot_version_ok=is_batch6_prot_version_ok,
                subtests=subtests,
            )
