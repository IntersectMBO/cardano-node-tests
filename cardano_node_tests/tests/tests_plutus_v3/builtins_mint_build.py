"""Shared minting scenarios for PlutusV3 builtin E2E tests."""

import enum
import logging
import math
import pathlib as pl
import typing as tp

import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

# Lovelace amount used for minting with a single script.
SCRIPT_FUND = 10_000_000
# Max number of scripts to fund in a single transaction.
FUND_CHUNK_SIZE = 30


class Outcomes(enum.StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    OVERSPEND = "overspend"


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


class PendingMint(tp.NamedTuple):
    """Minting transaction submitted to the mempool, but not yet confirmed on chain."""

    tx_file: pl.Path
    tx_output: clusterlib.TxRawOutput
    issuer_address: str
    token: str
    token_amount: int
    plutus_costs: list[dict]


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
    expected_error: str = "",
) -> PendingMint | None:
    """Run an e2e test for a Plutus builtin.

    The token issuer is expected to be already funded (see `fund_issuer_batch`).

    The minting transaction is submitted without waiting for it to appear on chain, so
    transactions for multiple scripts can be confirmed together (see `confirm_mints`).
    When `expected_error` is set and both the protocol version and the cost model are
    up to date, the error of a script expected to fail must contain that text.

    A script expected to fail, or a script submitted with a protocol version that doesn't
    support it, must not be accepted. A script expected to succeed can be accepted with
    an old cost model, e.g. casing on constants doesn't use any new cost model parameters.

    Returns:
        PendingMint | None: The submitted minting transaction, or None when the minting
        failed as expected.
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

    is_success_expected = is_prot_version_ok and is_cost_model_ok and outcome == Outcomes.SUCCESS

    def _dump_cost() -> list[dict]:
        """Save execution costs, allowing evaluation failures unless success is expected."""
        try:
            return cluster_obj.g_transaction.calculate_plutus_script_cost(
                src_address=payment_addr.address,
                tx_name=plutus_v_record.script_file.name,
                tx_files=tx_files_mint,
                txins=mint_utxos,
                txouts=txouts_mint,
                mint=plutus_mint_data,
            )
        except clusterlib.CLIError:
            if is_success_expected:
                raise
            return []

    plutus_costs = _dump_cost()

    try:
        tx_output_mint = cluster_obj.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_mint",
            tx_files=tx_files_mint,
            txins=mint_utxos,
            txouts=txouts_mint,
            mint=plutus_mint_data,
        )
        tx_signed_mint = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_output_mint.out_file,
            signing_key_files=tx_files_mint.signing_key_files,
            tx_name=f"{temp_template}_mint",
        )
        cluster_obj.g_transaction.submit_tx_bare(tx_file=tx_signed_mint)
    except clusterlib.CLIError as excp:
        str_excp = str(excp)
        if not is_prot_version_ok and (
            "not available in language PlutusV3 at and protocol version" in str_excp
            or "Script evaluation error" in str_excp
        ):
            return None
        if (not is_cost_model_ok or outcome == Outcomes.OVERSPEND) and (
            "The machine terminated part way through evaluation due to "
            "overspending the budget." in str_excp
        ):
            return None
        if outcome == Outcomes.ERROR and (
            "The machine terminated because of an error" in str_excp
            or "Script evaluation error" in str_excp
        ):
            if expected_error and is_prot_version_ok and is_cost_model_ok:
                with common.allow_unstable_error_messages():
                    assert expected_error in str_excp, str_excp
            return None
        raise

    assert is_prot_version_ok and outcome == Outcomes.SUCCESS, (
        f"Script {plutus_v_record.script_file.name} unexpectedly succeeded: "
        f"{outcome=}, {is_prot_version_ok=}, {is_cost_model_ok=}"
    )

    return PendingMint(
        tx_file=tx_signed_mint,
        tx_output=tx_output_mint,
        issuer_address=issuer_addr.address,
        token=token,
        token_amount=token_amount,
        plutus_costs=plutus_costs,
    )


def confirm_mints(
    cluster_obj: clusterlib.ClusterLib,
    pending_mints: list[PendingMint],
    attempts: int = 10,
) -> list[PendingMint]:
    """Wait for submitted minting transactions to appear on chain.

    Resubmit the transactions that didn't make it to the chain, e.g. because they were
    dropped from the mempool after a rollback. Resubmit only after a round where no
    transaction got confirmed. Otherwise the transactions are likely still waiting in the
    mempool, as not all of them fit into one block.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pending_mints: The submitted minting transactions.
        attempts: Max number of resubmits. Rounds where some transaction got confirmed
            don't count.

    Returns:
        list[PendingMint]: The minting transactions that didn't make it to the chain.
    """
    unconfirmed = list(pending_mints)
    progressed = True
    resubmits = 0
    # Each round that doesn't count against `attempts` confirms at least one transaction,
    # so the loop always ends
    while unconfirmed and resubmits < attempts:
        if not progressed:
            resubmits += 1
            for pending_mint in unconfirmed:
                LOGGER.warning(
                    "Resubmitting transaction from '%s'. Attempt %s/%s.",
                    pending_mint.tx_file,
                    resubmits,
                    attempts,
                )
                try:
                    cluster_obj.g_transaction.submit_tx_bare(tx_file=pending_mint.tx_file)
                except clusterlib.CLIError as exc:
                    # The transaction is likely still in the mempool
                    if not helpers.is_inputs_spent_err(str(exc)):
                        raise

        cluster_obj.wait_for_new_block(new_blocks=cluster_obj.confirm_blocks)

        issuer_utxos = cluster_obj.g_query.get_utxo(
            address=list({m.issuer_address for m in unconfirmed})
        )
        minted = {(u.address, u.coin, u.amount) for u in issuer_utxos}
        still_unconfirmed = [
            m for m in unconfirmed if (m.issuer_address, m.token, m.token_amount) not in minted
        ]
        progressed = len(still_unconfirmed) < len(unconfirmed)
        unconfirmed = still_unconfirmed

    return unconfirmed


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
    expected_error: str = "",
    expected_errors: dict[str, str] | None = None,
) -> list[PendingMint]:
    """Run minting tests with the tested Plutus Core built-in functions.

    The scripts are processed in chunks of `FUND_CHUNK_SIZE` scripts. For each chunk,
    the token issuer is funded in a single transaction, and the minting transactions are
    submitted without waiting, and then confirmed on chain together. This saves waiting
    for blocks for each script separately.

    The `expected_error` text is required in the errors of all `fail_scripts` (see
    `run_scenario`). `expected_errors` overrides it for individual script file stems.

    Returns:
        list[PendingMint]: Confirmed minting transactions with their execution costs.
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

    confirmed_mints: list[PendingMint] = []
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

        pending_mints: list[tuple[str, PendingMint]] = []
        for case_idx, ((script, outcome), issuer_funds) in enumerate(
            zip(chunk, all_issuer_funds, strict=True), start=chunk_start
        ):
            script_stem = script.script_file.stem
            with subtests.test(variant=f"{variant}_{script_stem}"):
                pending_mint = run_scenario(
                    cluster_obj=cluster_obj,
                    # The index keeps tx file names unique even if script stems repeat
                    temp_template=f"{temp_template}_{case_idx}_{script_stem}",
                    plutus_v_record=script,
                    payment_addr=payment_addr,
                    issuer_addr=issuer_addr,
                    issuer_funds=issuer_funds,
                    outcome=outcome,
                    is_cost_model_ok=is_cost_model_ok,
                    is_prot_version_ok=is_prot_version_ok,
                    expected_error=(expected_errors or {}).get(script_stem, expected_error),
                )
                if pending_mint:
                    pending_mints.append((script_stem, pending_mint))

        # Confirm all minting transactions of the chunk at once
        not_minted = confirm_mints(
            cluster_obj=cluster_obj, pending_mints=[m for __, m in pending_mints]
        )
        for script_stem, pending_mint in pending_mints:
            with subtests.test(variant=f"{variant}_{script_stem}_minted"):
                assert pending_mint not in not_minted, "The token was not minted"
                confirmed_mints.append(pending_mint)

    return confirmed_mints
