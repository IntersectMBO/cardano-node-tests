"""Trace valid Plutus spends through adopted certified Leios endorser blocks."""

import dataclasses
import logging
import pathlib as pl
import time

import allure
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import markers
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_dijkstra import leios
from cardano_node_tests.tests.tests_dijkstra import test_leios_blocks as eb_tests
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles

LOGGER = logging.getLogger(__name__)

pytestmark = [
    markers.SKIPIF_ON_TESTNET,
    pytest.mark.skipif(bool(leios.SKIP_REASON), reason=leios.SKIP_REASON),
    markers.SKIPIF_PLUTUSV2_UNUSABLE,
]

NUM_TXS = 30
MAX_ROUNDS = 3
ROUND_TIMEOUT_SEC = 2 * leios.MAX_SEARCH_SEC
SCRIPT_AMOUNT = 10_000_000


@dataclasses.dataclass(frozen=True)
class _ScriptTx:
    """A signed script spend, its ledger identity, and its EB wire reference size."""

    signed_file: pl.Path
    tx_id: str
    wire_size: int
    raw_output: clusterlib.TxRawOutput
    script_input: clusterlib.UTXOData


def _check_script_effects(
    *,
    cluster_obj: clusterlib.ClusterLib,
    eb: eb_tests._CertifiedEb,
    records: dict[str, _ScriptTx],
    destination: str,
    amount: int,
    collaterals: list[clusterlib.UTXOData],
    reference: clusterlib.UTXOData | None,
) -> None:
    """Check the selected EB's script effects and unchanged collateral/reference inputs."""
    txins = [f"{records[h].tx_id}#0" for h in sorted(eb.tx_hashes)]
    outputs = cluster_obj.g_query.get_utxo(txin=txins)
    assert {f"{u.utxo_hash}#{u.utxo_ix}" for u in outputs} == set(txins), outputs
    assert all(u.address == destination and u.amount == amount for u in outputs), outputs
    spent = [records[h].script_input for h in sorted(eb.tx_hashes)]
    assert not cluster_obj.g_query.get_utxo(utxo=spent), "Certified script inputs remain unspent"

    # Every transaction in the batch is valid, not only those selected for the EB claim.
    retained = cluster_obj.g_query.get_utxo(utxo=collaterals)
    assert sorted(retained) == sorted(collaterals), "A successful spend consumed collateral"
    if reference is not None:
        assert cluster_obj.g_query.get_utxo(utxo=reference) == [reference], (
            "The reference-script UTxO changed or was consumed"
        )


def _check_no_forks(
    *,
    searches: dict[pl.Path, logfiles.LogCursor],
    regex: str,
    lines_per_log: dict[pl.Path, list[str]],
    errors: dict[pl.Path, str],
    eb: eb_tests._CertifiedEb,
) -> None:
    """Retain the payment oracle's inconclusive outcome on post-adoption fork switches."""
    eb_tests._collect_lines(
        searches=searches, regex=regex, log_errors=errors, lines_per_log=lines_per_log
    )
    forks = eb_tests._get_fork_switches_after(lines_per_log=lines_per_log, eb=eb)
    if errors or forks:
        pytest.skip(f"Script transaction's certified-EB path is inconclusive: {errors}; {forks}")


class TestLeiosPlutus:
    """Known script spends included in certified EBs, not merely ordinary ranking blocks."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.leios
    @pytest.mark.plutus
    @pytest.mark.long
    @pytest.mark.dbsync
    @pytest.mark.xdist_split(markers.XdSplits.heavy)
    def test_plutus_spends_in_certified_eb(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        subtests: pytest_subtests.SubTests,
    ):
        """Require witness-script and reference-script spends in adopted certified EBs.

        Share the funded addresses and whole-cluster lock between both cases. For
        each, create independent script inputs with inline datums and collateral,
        then burst-submit large, valid Plutus V2 spends under Leios load. Match the
        signed transactions' exact block-format hashes/sizes to a forged EB, its
        certificate, adoption by the certifying producer, and another pool's tip.
        Retry with fresh inputs when a batch is included only in ordinary RBs.

        Check the certified script inputs were consumed, expected payment outputs
        exist, and collateral/reference UTxOs are unchanged, before and after block
        settlement. Retain the payment oracle's fork/log-read inconclusive outcome.
        This tests positive inclusion, not EB size or execution-budget enforcement.
        """
        cluster = cluster_singleton
        template = common.get_test_id(cluster)
        leios.skip_if_no_ebs(cluster_obj=cluster)
        seated = leios.is_committee_seated_in_genesis(cluster_obj=cluster)
        # Committee activation plus bounded block waits in funding and settlement.
        common.skip_on_long_epochs(
            cluster_obj=cluster, epochs=3 + (0 if seated else leios.VOTING_START_EPOCH)
        )
        if not seated:
            cluster.wait_for_epoch(epoch_no=leios.VOTING_START_EPOCH, padding_seconds=5)

        addrs = addrs_common.get_payment_addrs(
            name_template=f"{template}_payments",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
            fund_idx=[0],
            amount=4_000_000_000,
            caching_key=helpers.get_current_line_str(),
        )
        metadata_file = eb_tests._get_big_metadata_file(name_template=template)
        for use_reference in (False, True):
            case = "reference_script" if use_reference else "witness_script"
            with subtests.test(script_source=case):
                self._check_script_case(
                    cluster_obj=cluster,
                    payment=addrs[0],
                    destination=addrs[1].address,
                    name_template=f"{template}_{case}",
                    metadata_file=metadata_file,
                    use_reference=use_reference,
                )

    def _check_script_case(
        self,
        *,
        cluster_obj: clusterlib.ClusterLib,
        payment: clusterlib.AddressRecord,
        destination: str,
        name_template: str,
        metadata_file: pl.Path,
        use_reference: bool,
    ) -> None:
        """Burst independent spends and prove a specific adopted certified-EB inclusion."""
        cluster = cluster_obj
        script = plutus_common.ALWAYS_SUCCEEDS["v2"]
        cost = script.execution_cost
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=name_template, payment_script_file=script.script_file
        )
        pparams = cluster.g_query.get_protocol_params()
        # Cover max-size bytes, the small script budget, and reference-script fees.
        fee = pparams["txFeePerByte"] * pparams["maxTxSize"] + pparams["txFeeFixed"] + 1_000_000
        assert fee < SCRIPT_AMOUNT, pparams
        collateral_amount = max(5_000_000, (fee * pparams["collateralPercentage"] + 99) // 100)
        state_dir = cluster_nodes.get_cluster_env().state_dir
        pool_logs = sorted(state_dir.glob("pool*.stdout"))
        assert pool_logs, state_dir
        node_logs = [*pool_logs, state_dir / "bft1.stdout"]
        regex = "|".join(
            f"(?:{r})"
            for r in (
                leios.EB_FORGED_RE,
                leios.EB_ANNOUNCED_RE,
                leios.EB_CERTIFIED_RE,
                leios.BLOCK_ADOPTED_RE,
                leios.NEW_TIP_RE,
            )
        )

        for round_no in range(MAX_ROUNDS):
            template = f"{name_template}_r{round_no}"
            # Keep each script input and collateral distinct, even at the same address.
            txouts = [
                clusterlib.TxOut(
                    address=script_address,
                    amount=SCRIPT_AMOUNT,
                    inline_datum_file=plutus_common.DATUM_42,
                )
            ] * NUM_TXS + [
                clusterlib.TxOut(address=payment.address, amount=collateral_amount)
            ] * NUM_TXS
            if use_reference:
                txouts.append(
                    clusterlib.TxOut(
                        address=payment.address,
                        amount=10_000_000,
                        reference_script_file=script.script_file,
                    )
                )
            funding = cluster.g_transaction.send_tx(
                src_address=payment.address,
                tx_name=f"{template}_fund_scripts",
                txouts=txouts,
                tx_files=clusterlib.TxFiles(signing_key_files=[payment.skey_file]),
                join_txouts=False,
            )
            funded = {u.utxo_ix: u for u in cluster.g_query.get_utxo(tx_raw_output=funding)}
            script_inputs = [funded[i] for i in range(NUM_TXS)]
            collaterals = [funded[NUM_TXS + i] for i in range(NUM_TXS)]
            reference = funded[2 * NUM_TXS] if use_reference else None
            assert all(
                u.address == script_address and u.inline_datum is not None for u in script_inputs
            )
            assert all(u.amount == collateral_amount for u in collaterals), collaterals
            if reference is not None:
                assert reference.reference_script, reference
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=funding)

            # Build every spend before submitting, so metadata-backed load reaches an EB.
            records: dict[str, _ScriptTx] = {}
            for i, utxo in enumerate(script_inputs):
                tx_name = f"{template}_spend{i}"
                raw = cluster.g_transaction.build_raw_tx_bare(
                    out_file=f"{tx_name}.body",
                    txouts=[clusterlib.TxOut(address=destination, amount=SCRIPT_AMOUNT - fee)],
                    tx_files=clusterlib.TxFiles(
                        metadata_json_files=[metadata_file],
                        signing_key_files=[payment.skey_file],
                    ),
                    script_txins=[
                        clusterlib.ScriptTxIn(
                            txins=[utxo],
                            script_file="" if use_reference else script.script_file,
                            reference_txin=reference,
                            reference_type=clusterlib.ScriptTypes.PLUTUS_V2
                            if use_reference
                            else "",
                            inline_datum_present=True,
                            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
                            execution_units=(cost.per_time, cost.per_space),
                            collaterals=[collaterals[i]],
                        )
                    ],
                    fee=fee,
                )
                signed = cluster.g_transaction.sign_tx(
                    tx_body_file=raw.out_file,
                    signing_key_files=[payment.skey_file],
                    tx_name=tx_name,
                )
                tx_hash, size = leios.get_eb_tx_ref(tx_file=signed)
                records[tx_hash] = _ScriptTx(
                    signed_file=signed,
                    tx_id=cluster.g_transaction.get_txid(tx_file=signed),
                    wire_size=size,
                    raw_output=raw,
                    script_input=utxo,
                )

            searches = logfiles.init_searches(node_logs)
            lines: dict[pl.Path, list[str]] = {p: [] for p in node_logs}
            errors: dict[pl.Path, str] = {}
            refs = {h: rec.wire_size for h, rec in records.items()}
            for rec in records.values():
                cluster.g_transaction.submit_tx_bare(tx_file=rec.signed_file)

            # Ordinary RB inclusion cannot satisfy the correlated EB lifecycle oracle.
            deadline = time.monotonic() + ROUND_TIMEOUT_SEC
            certified: list[eb_tests._CertifiedEb] = []
            round_outputs = [f"{rec.tx_id}#0" for rec in records.values()]
            while True:
                eb_tests._collect_lines(
                    searches=searches, regex=regex, log_errors=errors, lines_per_log=lines
                )
                certified, problems = eb_tests._find_certified_ebs(
                    lines_per_pool={p: lines[p] for p in pool_logs}, tx_refs=refs
                )
                assert not problems, problems
                if len(cluster.g_query.get_utxo(txin=round_outputs)) == NUM_TXS:
                    # Let final certification/adoption traces catch up before retrying.
                    cluster.wait_for_new_block(new_blocks=1)
                    eb_tests._collect_lines(
                        searches=searches, regex=regex, log_errors=errors, lines_per_log=lines
                    )
                    certified, problems = eb_tests._find_certified_ebs(
                        lines_per_pool={p: lines[p] for p in pool_logs}, tx_refs=refs
                    )
                    assert not problems, problems
                    break
                assert time.monotonic() < deadline, (
                    f"Script spends did not reach the ledger within {ROUND_TIMEOUT_SEC}s: {errors}"
                )
                time.sleep(leios.SEARCH_STEP_SEC)
            if not certified:
                assert not errors, errors
                LOGGER.info("No adopted certified script EB in %s; retrying", template)
                continue

            eb = max(certified, key=lambda rec: len(rec.tx_hashes))
            _check_no_forks(
                searches=searches, regex=regex, lines_per_log=lines, errors=errors, eb=eb
            )
            helpers.write_json(
                out_file=pl.Path(f"{template}_certified_eb.json"),
                content={
                    "use_reference_script": use_reference,
                    "eb_slot": eb.eb_slot,
                    "eb_hash": eb.eb_hash,
                    "cert_slot": eb.cert_slot,
                    "cert_rb_hash": eb.cert_rb_hash,
                    "destination": destination,
                    "amount": SCRIPT_AMOUNT - fee,
                    "transactions": [
                        {"tx_id": records[h].tx_id, "eb_tx_hash": h, "wire_size": refs[h]}
                        for h in sorted(eb.tx_hashes)
                    ],
                },
            )
            _check_script_effects(
                cluster_obj=cluster,
                eb=eb,
                records=records,
                destination=destination,
                amount=SCRIPT_AMOUNT - fee,
                collaterals=collaterals,
                reference=reference,
            )
            cluster.wait_for_new_block(
                new_blocks=min(cluster.genesis["securityParam"] + 1, eb_tests.MAX_SETTLE_BLOCKS)
            )
            _check_no_forks(
                searches=searches, regex=regex, lines_per_log=lines, errors=errors, eb=eb
            )
            _check_script_effects(
                cluster_obj=cluster,
                eb=eb,
                records=records,
                destination=destination,
                amount=SCRIPT_AMOUNT - fee,
                collaterals=collaterals,
                reference=reference,
            )
            for tx_hash in sorted(eb.tx_hashes):
                dbsync_utils.check_tx(
                    cluster_obj=cluster, tx_raw_output=records[tx_hash].raw_output
                )
            LOGGER.info(
                "Certified EB %s contains %s script spends",
                (eb.eb_slot, eb.eb_hash),
                len(eb.tx_hashes),
            )
            return

        pytest.fail(f"No script spend in an adopted certified EB after {MAX_ROUNDS} batches")
