"""Historical certified-EB fetching by a non-producer with a fresh chain database."""

import json
import logging
import pathlib as pl
import shutil
import time

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import markers
from cardano_node_tests.tests.tests_dijkstra import leios
from cardano_node_tests.tests.tests_dijkstra import test_leios_blocks as eb_tests
from cardano_node_tests.tests.tests_dijkstra import test_leios_storage as storage_tests
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles

LOGGER = logging.getLogger(__name__)

pytestmark = [
    markers.SKIPIF_ON_TESTNET,
    pytest.mark.skipif(bool(leios.SKIP_REASON), reason=leios.SKIP_REASON),
]

# Reuse the disposable SQLite setup and the existing payment-only EB trace oracle.
cluster_leios_storage = storage_tests.cluster_leios_storage
NUM_TXS = 30
MAX_ROUNDS = 3
TX_IN_AMOUNT = 5_000_000
TIMEOUT_SEC = storage_tests.RECOVERY_TIMEOUT_SEC


def _prepare_historical_eb(
    *,
    cluster_obj: clusterlib.ClusterLib,
    cluster_manager: cluster_management.ClusterManager,
    name_template: str,
    db_dirs: dict[str, pl.Path],
    node_logs: list[pl.Path],
) -> tuple[eb_tests._CertifiedEb, tuple[str, str], list[str], str, int]:
    """Record a payment-bearing adopted EB and identical immutable bytes on every node.

    Until the selected EB is immutable, fork switches make the payment's EB path
    inconclusive, just as in the original certified-EB payment test.
    """
    addrs = addrs_common.get_payment_addrs(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        num=2,
        fund_idx=[0],
        amount=(NUM_TXS * TX_IN_AMOUNT + 10_000_000) * MAX_ROUNDS,
        caching_key=helpers.get_current_line_str(),
    )
    src, dst = addrs
    metadata = eb_tests._get_big_metadata_file(name_template=name_template)
    pparams = cluster_obj.g_query.get_protocol_params()
    fee = pparams["txFeePerByte"] * pparams["maxTxSize"] + pparams["txFeeFixed"]
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
        fanout = cluster_obj.g_transaction.send_tx(
            src_address=src.address,
            tx_name=f"{template}_fanout",
            txouts=[clusterlib.TxOut(address=src.address, amount=TX_IN_AMOUNT)] * NUM_TXS,
            tx_files=clusterlib.TxFiles(signing_key_files=[src.skey_file]),
            join_txouts=False,
        )
        inputs = [
            u
            for u in cluster_obj.g_query.get_utxo(tx_raw_output=fanout)
            if u.address == src.address and u.amount == TX_IN_AMOUNT
        ][:NUM_TXS]
        assert len(inputs) == NUM_TXS, inputs

        # Independent inputs allow a burst of large payments without chaining them.
        refs: dict[str, int] = {}
        txids: dict[str, str] = {}
        signed: list[pl.Path] = []
        for i, utxo in enumerate(inputs):
            tx_name = f"{template}_tx{i}"
            raw = cluster_obj.g_transaction.build_raw_tx(
                src_address=src.address,
                tx_name=tx_name,
                txins=[utxo],
                txouts=[clusterlib.TxOut(address=dst.address, amount=TX_IN_AMOUNT - fee)],
                tx_files=clusterlib.TxFiles(metadata_json_files=[metadata]),
                fee=fee,
            )
            tx_file = cluster_obj.g_transaction.sign_tx(
                tx_body_file=raw.out_file,
                signing_key_files=[src.skey_file],
                tx_name=tx_name,
            )
            tx_hash, tx_size = leios.get_eb_tx_ref(tx_file=tx_file)
            refs[tx_hash] = tx_size
            txids[tx_hash] = cluster_obj.g_transaction.get_txid(tx_file=tx_file)
            signed.append(tx_file)

        searches = logfiles.init_searches(node_logs)
        errors: dict[pl.Path, str] = {}
        lines: dict[pl.Path, list[str]] = {p: [] for p in node_logs}
        for tx_file in signed:
            cluster_obj.g_transaction.submit_tx_bare(tx_file=tx_file)

        # Wait for inclusion and correlate the same EB through forging and adoption.
        deadline = time.monotonic() + TIMEOUT_SEC
        while time.monotonic() < deadline:
            eb_tests._collect_lines(
                searches=searches, regex=regex, log_errors=errors, lines_per_log=lines
            )
            assert not errors, errors
            certified, problems = eb_tests._find_certified_ebs(
                lines_per_pool={p: ls for p, ls in lines.items() if p.name.startswith("pool")},
                tx_refs=refs,
            )
            assert not problems, problems
            if certified:
                break
            time.sleep(leios.SEARCH_STEP_SEC)
        else:
            LOGGER.info("No certified EB with test payments in round %s", round_no)
            continue

        # Only this specific payment-bearing EB can become the historical reference.
        eb = certified[0]
        point = (eb.eb_slot, eb.eb_hash)
        deadline = time.monotonic() + TIMEOUT_SEC
        while True:
            eb_tests._collect_lines(
                searches=searches, regex=regex, log_errors=errors, lines_per_log=lines
            )
            forks = eb_tests._get_fork_switches_after(lines_per_log=lines, eb=eb)
            if errors or forks:
                pytest.skip(f"Historical payment EB path is inconclusive: {errors}; {forks}")
            stored = {
                node: storage_tests._read_immutable_ebs(db_dir / "leios.imm.db").get(point)
                for node, db_dir in db_dirs.items()
            }
            if all(stored.values()):
                digests = set(stored.values())
                assert len(digests) == 1, stored
                digest = stored[next(iter(stored))]
                assert digest is not None
                txins = [f"{txids[h]}#0" for h in sorted(eb.tx_hashes)]
                dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=fanout)
                return eb, digest, txins, dst.address, TX_IN_AMOUNT - fee
            assert time.monotonic() < deadline, f"EB {point} not immutable on every node: {stored}"
            time.sleep(leios.SEARCH_STEP_SEC)

    pytest.fail(f"No test payment in an adopted certified EB after {MAX_ROUNDS} rounds")


def _check_known_outputs(
    *, cluster_obj: clusterlib.ClusterLib, txins: list[str], address: str, amount: int
) -> None:
    """Check only the unspent outputs owned by payments in the saved certified EB."""
    utxos = cluster_obj.g_query.get_utxo(txin=txins)
    assert {f"{u.utxo_hash}#{u.utxo_ix}" for u in utxos} == set(txins), utxos
    assert all(u.address == address and u.amount == amount for u in utxos), utxos


class TestLeiosSync:
    """Historical certified-EB recovery on an isolated SQLite cluster."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.leios
    @pytest.mark.long
    @pytest.mark.dbsync
    @pytest.mark.xdist_split(markers.XdSplits.heavy)
    def test_historical_eb_reacquisition(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_leios_storage: clusterlib.ClusterLib,
    ):
        """Fetch a known historical certified EB after resetting the non-producer database.

        Record an adopted certified EB containing independent test payments and wait
        for its exact body/closure bytes in every node's immutable SQLite partition.
        Stop the non-firehose cohort, move aside the entire test-owned bft1 database
        (including chain stores and snapshots), then restart that cohort. Require
        post-reset body and transaction acquisition traces for the saved EB, its
        exact bytes in the new immutable store, progress beyond its certifying slot,
        and known outputs on bft1 and a retained-history peer. Queries of live tips
        are deliberately not claimed to be a same-point ledger comparison.

        The old database is retained beside the test's transaction artifacts. This
        scoped leios_fast restart exception never stops firehose's submission node.
        """
        cluster = cluster_leios_storage
        template = common.get_test_id(cluster)
        leios.skip_if_no_ebs(cluster_obj=cluster)
        if not any(v in configuration.TESTNET_VARIANT for v in ("leios_fast", "mainnet_fast")):
            pytest.skip("Historical EB sync requires leios_fast or mainnet_fast")
        seated = leios.is_committee_seated_in_genesis(cluster_obj=cluster)
        # Three inclusion windows, an immutable-store window, and recovery, plus setup.
        common.skip_on_long_epochs(
            cluster_obj=cluster, epochs=6 + (0 if seated else leios.VOTING_START_EPOCH)
        )
        if not seated:
            cluster.wait_for_epoch(epoch_no=leios.VOTING_START_EPOCH, padding_seconds=5)

        state_dir = cluster_nodes.get_cluster_env().state_dir
        nodes = sorted(cluster_nodes.get_cluster_type().NODES)
        assert "bft1" in nodes, nodes
        retained: str | None = None
        if configuration.ENABLE_TX_FIREHOSE:
            with open(state_dir / "tx-firehose-config.json", encoding="utf-8") as fp:
                socket = (state_dir / json.load(fp)["socket_path"]).resolve()
            matches = [n for n in nodes if (state_dir / f"{n}.socket").resolve() == socket]
            assert len(matches) == 1, f"Unknown firehose submission socket: {socket}"
            retained = matches[0]
            if retained == "bft1":
                pytest.skip("Fresh-sync target bft1 must not be the firehose submission node")
        cohort = [n for n in nodes if n != retained]
        reference = retained or next(n for n in nodes if n != "bft1")
        db_dirs = {n: state_dir / f"db-{n}" for n in nodes}
        node_logs = [state_dir / f"{n}.stdout" for n in nodes]
        eb, digests, txins, address, amount = _prepare_historical_eb(
            cluster_obj=cluster,
            cluster_manager=cluster_manager,
            name_template=template,
            db_dirs=db_dirs,
            node_logs=node_logs,
        )
        _check_known_outputs(cluster_obj=cluster, txins=txins, address=address, amount=amount)
        helpers.write_json(
            out_file=pl.Path(f"{template}_historical_eb.json"),
            content={
                "eb_slot": eb.eb_slot,
                "eb_hash": eb.eb_hash,
                "cert_slot": eb.cert_slot,
                "cert_rb_hash": eb.cert_rb_hash,
                "body_and_closure_digests": digests,
                "txins": txins,
                "address": address,
                "amount": amount,
                "retained_history_reference": reference,
                "restarted_nodes": cohort,
            },
        )

        # Never move a symlink or any path other than this disposable target database.
        target_db = db_dirs["bft1"]
        assert target_db.is_dir() and not target_db.is_symlink(), target_db
        assert target_db.resolve().parent == state_dir.resolve(), target_db
        backup = pl.Path(f"{template}_original_db_bft1").resolve()
        assert not backup.exists(), backup
        logfiles.add_ignore_rule(
            files_glob="*.stdout", regex="MuxBearerClosed", ignore_file_id=cluster_manager.worker_id
        )
        instance_num = cluster_nodes.get_instance_num()
        with cluster_manager.respin_on_failure():
            cluster_nodes.stop_nodes(cohort)
            try:
                shutil.move(str(target_db), str(backup))
                assert not target_db.exists(), target_db
            finally:
                # A failed move must not leave the cohort stopped.
                recovery = logfiles.init_searches(node_logs)
                acquired = logfiles.init_searches([state_dir / "bft1.stdout"])
                started = time.monotonic()
                cluster_nodes.start_nodes(cohort)

            storage_tests._wait_for_nodes(cluster_obj=cluster, nodes=cohort, searches=recovery)
            point_re = rf"\({eb.eb_slot}, {eb.eb_hash}\)"
            msgs = frozenset(
                rf"Consensus\.LeiosKernel\.{event}\].*EB {kind} acquired: {point_re}"
                for event, kind in (("BlockAcquired", "body"), ("BlockTxsAcquired", "txs"))
            )
            deadline = started + TIMEOUT_SEC
            try:
                cluster_nodes.set_cluster_env(
                    instance_num=instance_num, socket_file_name="bft1.socket"
                )
                while True:
                    storage_tests._search_activity(searches=recovery)
                    errors: dict[pl.Path, str] = {}
                    logfiles.search_round(
                        searches=acquired,
                        missing_msgs=lambda cursor: msgs - cursor.found,
                        log_errors=errors,
                    )
                    assert not errors, errors
                    tip = cluster.g_query.get_tip()
                    stored = storage_tests._read_immutable_ebs(target_db / "leios.imm.db")
                    if (
                        msgs <= acquired[state_dir / "bft1.stdout"].found
                        and stored.get((eb.eb_slot, eb.eb_hash)) == digests
                        and tip.get("slot", 0) > eb.cert_slot
                    ):
                        break
                    assert time.monotonic() < deadline, (
                        f"Historical EB did not recover within {TIMEOUT_SEC}s: "
                        f"point={(eb.eb_slot, eb.eb_hash)}, tip={tip}, "
                        f"traces={acquired[state_dir / 'bft1.stdout'].found}, "
                        f"stored={stored.get((eb.eb_slot, eb.eb_hash))}"
                    )
                    time.sleep(leios.SEARCH_STEP_SEC)

                for node in ("bft1", reference):
                    cluster_nodes.set_cluster_env(
                        instance_num=instance_num, socket_file_name=f"{node}.socket"
                    )
                    assert cluster.g_query.get_tip()["slot"] > eb.cert_slot
                    _check_known_outputs(
                        cluster_obj=cluster, txins=txins, address=address, amount=amount
                    )
                storage_tests._check_storage(db_dirs=db_dirs)
                storage_tests._search_activity(searches=recovery)
                failed = [
                    s
                    for s in cluster_nodes.services_status()
                    if s.name.startswith(cluster_nodes.NODES_SERVICE_PREFIX)
                    and s.status != "RUNNING"
                ]
                assert not failed, failed
            finally:
                cluster_nodes.set_cluster_env(instance_num=instance_num)
        elapsed = time.monotonic() - started
        helpers.write_json(
            out_file=pl.Path(f"{template}_recovery.json"),
            content={"catchup_seconds": elapsed, "original_database": str(backup)},
        )
        LOGGER.info("Historical EB %s recovered in %.1fs", (eb.eb_slot, eb.eb_hash), elapsed)
