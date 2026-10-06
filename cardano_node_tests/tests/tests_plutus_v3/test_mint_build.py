"""Tests for minting with Plutus using `transaction build`."""

import enum
import logging
import math
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
from cardano_node_tests.utils import clusterlib_utils
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
# Lovelace amount used for minting with a single script
SCRIPT_FUND = 10_000_000
# Max number of scripts to fund the token issuer for in a single transaction
FUND_CHUNK_SIZE = 30

pytestmark = [
    markers.SKIPIF_PLUTUSV3_UNUSABLE,
    pytest.mark.plutus,
]


class Outcomes(enum.StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    OVERSPEND = "overspend"


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


class IssuerFunds(tp.NamedTuple):
    """UTxOs prepared on the token issuer address for minting with a single script."""

    mint_utxos: list[clusterlib.UTXOData]
    collateral_utxos: list[clusterlib.UTXOData]


def fund_issuer_batch(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    payment_addr: clusterlib.AddressRecord,
    issuer_addr: clusterlib.AddressRecord,
    minting_costs: list[plutus_common.ScriptCost],
    amount: int,
) -> list[IssuerFunds]:
    """Fund the token issuer for minting with multiple scripts in a single transaction.

    For each minting cost, create one UTxO for minting and one UTxO for collateral.
    Funding all scripts at once saves one transaction (and one wait for a block) per script.

    Returns:
        list[IssuerFunds]: The minting and collateral UTxOs, in the order of `minting_costs`.
    """
    txouts = []
    for cost in minting_costs:
        txouts.append(clusterlib.TxOut(address=issuer_addr.address, amount=amount))
        txouts.append(clusterlib.TxOut(address=issuer_addr.address, amount=cost.collateral))

    tx_output = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{temp_template}_fund_issuer",
        src_address=payment_addr.address,
        build_method=clusterlib_utils.BuildMethods.BUILD,
        tx_files=clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file]),
        txouts=txouts,
        fee_buffer=2_000_000,
        # Don't join the txouts, we need separate UTxOs
        join_txouts=False,
    )

    out_utxos = sorted(
        cluster_obj.g_query.get_utxo(tx_raw_output=tx_output), key=lambda u: u.utxo_ix
    )
    # Check the outputs of the funding tx, not the address balance. Minting txs that were
    # submitted earlier can still change the balance of the token issuer address.
    issuer_amount = sum(u.amount for u in out_utxos if u.address == issuer_addr.address)
    assert issuer_amount == sum(t.amount for t in txouts), (
        f"Incorrect amount funded to token issuer address `{issuer_addr.address}`"
    )
    utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(utxos=out_utxos, txouts=txouts)
    utxos_by_ix = {u.utxo_ix: u for u in out_utxos}

    return [
        IssuerFunds(
            mint_utxos=[utxos_by_ix[utxo_ix_offset + 2 * i]],
            collateral_utxos=[utxos_by_ix[utxo_ix_offset + 2 * i + 1]],
        )
        for i in range(len(minting_costs))
    ]


def run_scenario(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    plutus_v_record: plutus_common.PlutusScriptData,
    payment_addr: clusterlib.AddressRecord,
    issuer_addr: clusterlib.AddressRecord,
    issuer_funds: IssuerFunds,
    outcome: Outcomes,
    is_cost_model_ok: bool,
    is_prot_version_ok: bool,
):
    """Run an e2e test for a Plutus builtin.

    The token issuer is expected to be already funded (see `fund_issuer_batch`).
    """
    lovelace_amount = 2_000_000
    token_amount = 5
    mint_utxos = issuer_funds.mint_utxos
    collateral_utxos = issuer_funds.collateral_utxos

    # Mint the "qacoin"

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

    tx_files_mint = clusterlib.TxFiles(
        signing_key_files=[issuer_addr.skey_file],
    )
    txouts_mint = [
        clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
        *mint_txouts,
    ]

    def _dump_cost() -> None:
        try:  # noqa: SIM105
            cluster_obj.g_transaction.calculate_plutus_script_cost(
                src_address=payment_addr.address,
                tx_name=plutus_v_record.script_file.name,
                tx_files=tx_files_mint,
                txins=mint_utxos,
                txouts=txouts_mint,
                mint=plutus_mint_data,
            )
        except clusterlib.CLIError:
            pass

    _dump_cost()

    try:
        tx_output_mint = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster_obj,
            name_template=f"{temp_template}_mint",
            src_address=payment_addr.address,
            build_method=clusterlib_utils.BuildMethods.BUILD,
            tx_files=tx_files_mint,
            txins=mint_utxos,
            txouts=txouts_mint,
            mint=plutus_mint_data,
        )
    except clusterlib.CLIError as excp:
        str_excp = str(excp)
        if not is_prot_version_ok and (
            "not available in language PlutusV3 at and protocol version" in str_excp
            or "Script evaluation error" in str_excp
        ):
            return
        if (not is_cost_model_ok or outcome == Outcomes.OVERSPEND) and (
            "The machine terminated part way through evaluation due to "
            "overspending the budget." in str_excp
        ):
            return
        if outcome == Outcomes.ERROR and (
            "The machine terminated because of an error" in str_excp
            or "Script evaluation error" in str_excp
        ):
            return
        raise

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output_mint)
    token_utxo = clusterlib.filter_utxos(utxos=out_utxos, address=issuer_addr.address, coin=token)
    assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"


def run_plutusv3_builtins_test(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    variant: str,
    success_scripts: tp.Iterable[plutus_common.PlutusScriptData],
    fail_scripts: tp.Iterable[plutus_common.PlutusScriptData],
    overspend_scripts: tp.Iterable[plutus_common.PlutusScriptData],
    is_cost_model_ok: bool,
    is_prot_version_ok: bool,
    subtests: pytest_subtests.SubTests,
):
    """Run minting tests with the tested Plutus Core built-in functions.

    The token issuer is funded for multiple scripts at once, in chunks of
    `FUND_CHUNK_SIZE` scripts, to save one funding transaction per script.
    """
    cases = [
        *((s, Outcomes.SUCCESS) for s in success_scripts),
        *((s, Outcomes.ERROR) for s in fail_scripts),
        *((s, Outcomes.OVERSPEND) for s in overspend_scripts),
    ]
    protocol_params = cluster_obj.g_query.get_protocol_params()
    all_minting_costs = [
        plutus_common.compute_cost(
            execution_cost=script.execution_cost, protocol_params=protocol_params
        )
        for script, __ in cases
    ]

    # Make sure the payment address has enough funds for all the chunks, plus fees.
    # The addresses are cached per variant, so tests running in parallel on the same
    # cluster instance don't share them.
    num_chunks = math.ceil(len(cases) / FUND_CHUNK_SIZE)
    total_amount = (
        sum(SCRIPT_FUND + c.collateral for c in all_minting_costs) + num_chunks * 10_000_000
    )
    payment_addr, issuer_addr = addrs_common.get_payment_addrs(
        name_template=temp_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        num=2,
        fund_idx=[0],
        caching_key=f"plutusv3_builtins_batch_testing_{variant}",
        amount=max(1_000_000_000, total_amount),
        min_amount=total_amount,
    )

    for chunk_idx, chunk_start in enumerate(range(0, len(cases), FUND_CHUNK_SIZE)):
        chunk = cases[chunk_start : chunk_start + FUND_CHUNK_SIZE]
        all_issuer_funds = fund_issuer_batch(
            cluster_obj=cluster_obj,
            temp_template=f"{temp_template}_chunk{chunk_idx}",
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_costs=all_minting_costs[chunk_start : chunk_start + FUND_CHUNK_SIZE],
            amount=SCRIPT_FUND,
        )

        for (script, outcome), issuer_funds in zip(chunk, all_issuer_funds, strict=True):
            script_stem = script.script_file.stem
            with subtests.test(variant=f"{variant}_{script_stem}"):
                run_scenario(
                    cluster_obj=cluster_obj,
                    temp_template=f"{temp_template}_{script_stem}",
                    plutus_v_record=script,
                    payment_addr=payment_addr,
                    issuer_addr=issuer_addr,
                    issuer_funds=issuer_funds,
                    outcome=outcome,
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
        run_plutusv3_builtins_test(
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
        run_plutusv3_builtins_test(
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
            run_plutusv3_builtins_test(
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
