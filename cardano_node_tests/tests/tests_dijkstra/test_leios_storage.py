"""Tests for SQLite LeiosDB persistence after an orderly node restart.

SQLite partitions live beside the node's volatile and immutable chain databases.
Tests run on a fresh, test-owned cluster with unchanged chain databases.
When firehose is enabled, its submission node stays running during the restart.
"""

import contextlib
import hashlib
import json
import logging
import pathlib as pl
import sqlite3
import time

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import markers
from cardano_node_tests.tests.tests_dijkstra import leios
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.leios,
    markers.SKIPIF_ON_TESTNET,
    pytest.mark.skipif(bool(leios.SKIP_REASON), reason=leios.SKIP_REASON),
]

# Bound both the immutable-store wait and the wall time on variants with large k.
MAX_SECURITY_PARAM = 20
RECOVERY_TIMEOUT_SEC = 2 * leios.MAX_SEARCH_SEC
DB_EXCEPTION_RE = (
    r"Consensus\.Leios(?:Kernel|Peer)\.DbException\]|"
    r"Leios(?: peer)? DB exception:|LeiosDb(?:Write)?Exception"
)
ACTIVITY_MSGS = frozenset(
    (leios.MSG_VOTED, leios.MSG_CERTIFIED, leios.MSG_BLOCK_CERTIFIED, DB_EXCEPTION_RE)
)


@pytest.fixture
def cluster_leios_storage(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Start a fresh singleton cluster with the SQLite LeiosDB backend.

    Custom startup scripts are ignored on externally managed development clusters,
    so reject those before requesting an instance. This fixture deliberately needs
    Leios block timing instead of the usual local_fast custom-cluster restriction.
    """
    if configuration.DEV_CLUSTER_RUNNING or configuration.FORBID_RESPIN:
        pytest.skip("LeiosDB storage tests need a framework-managed disposable cluster")
    if configuration.ENABLE_TX_FIREHOSE and not any(
        variant in configuration.TESTNET_VARIANT for variant in ("leios_fast", "mainnet_fast")
    ):
        pytest.skip("Firehose-safe subset restarts require leios_fast or mainnet_fast")

    shared_tmp = temptools.get_pytest_shared_tmp()
    with locking.FileLockIfXdist(f"{shared_tmp}/startup_files_leios_db_SQLite.lock"):
        scriptsdir = shared_tmp / "startup_files_leios_db_SQLite"
        if not (scriptsdir / "start-cluster").exists():
            startup_files = cluster_nodes.get_cluster_type().cluster_scripts.copy_scripts_files(
                destdir=scriptsdir
            )
            with open(startup_files.genesis_spec, encoding="utf-8") as fp_in:
                genesis = json.load(fp_in)
            leios.skip_if_no_ebs_in_genesis(genesis=genesis)
            if genesis["securityParam"] > MAX_SECURITY_PARAM:
                pytest.skip("LeiosDB recovery tests need a small local security parameter")

            # The instance generator creates every node's config from this template.
            config_file = scriptsdir / "template-config.json"
            with open(config_file, encoding="utf-8") as fp_in:
                node_config = json.load(fp_in)
            node_config["LeiosDbConfig"] = {"Backend": "SQLite"}
            helpers.write_json(out_file=config_file, content=node_config)

    return cluster_manager.get(
        lock_resources=[cluster_management.Resources.CLUSTER],
        prio=True,
        cleanup=True,
        scriptsdir=scriptsdir,
    )


def _search_activity(*, searches: dict[pl.Path, logfiles.LogCursor]) -> None:
    """Collect fresh activity and fail on a Leios kernel or peer database exception."""
    errors: dict[pl.Path, str] = {}
    logfiles.search_round(
        searches=searches,
        missing_msgs=lambda search: ACTIVITY_MSGS - search.found,
        log_errors=errors,
    )
    assert not errors, "\n".join(errors.values())
    db_errors = [str(path) for path, cursor in searches.items() if DB_EXCEPTION_RE in cursor.found]
    assert not db_errors, f"LeiosDB exception in node logs: {db_errors}"


def _wait_for_activity(
    *, searches: dict[pl.Path, logfiles.LogCursor], pool_logs: list[pl.Path], phase: str
) -> None:
    """Require votes and assembled certificates on every pool, and an included certificate.

    Cursors are created by the caller before the operation being observed. In
    particular, pre-restart messages cannot satisfy the recovery phase.
    """
    deadline = time.monotonic() + RECOVERY_TIMEOUT_SEC
    while True:
        _search_activity(searches=searches)
        if all(
            {leios.MSG_VOTED, leios.MSG_CERTIFIED} <= searches[path].found for path in pool_logs
        ) and any(leios.MSG_BLOCK_CERTIFIED in searches[path].found for path in pool_logs):
            return
        if time.monotonic() >= deadline:
            found = {path.name: sorted(cursor.found) for path, cursor in searches.items()}
            pytest.fail(
                f"Incomplete Leios activity {phase} within {RECOVERY_TIMEOUT_SEC}s: {found}"
            )
        time.sleep(min(leios.SEARCH_STEP_SEC, max(0, deadline - time.monotonic())))


def _read_immutable_ebs(db_file: pl.Path) -> dict[tuple[int, str], tuple[str, str]]:
    """Read stored EB body/closure digests without creating or modifying the node DB.

    The immutable partition contains certified EBs copied from the selected chain.
    Compare actual stored bytes across a reopen, not just file presence or row counts.
    """
    assert db_file.is_file(), f"Missing LeiosDB partition: {db_file}"
    with contextlib.closing(sqlite3.connect(f"{db_file.as_uri()}?mode=ro", uri=True)) as conn:
        rows = conn.execute(
            "SELECT e.ebSlot, e.ebHashBytes, b.body, c.txs FROM ebs e "
            "JOIN ebBodies b ON b.ebHashBytes = e.ebHashBytes "
            "JOIN ebClosures c ON c.ebHashBytes = e.ebHashBytes"
        ).fetchall()
    return {
        (slot, eb_hash.hex()): (
            hashlib.blake2b(body, digest_size=32).hexdigest(),
            hashlib.blake2b(txs, digest_size=32).hexdigest(),
        )
        for slot, eb_hash, body, txs in rows
        if body and txs
    }


def _wait_for_immutable_eb(
    *, db_dirs: dict[str, pl.Path], searches: dict[pl.Path, logfiles.LogCursor]
) -> tuple[tuple[int, str], tuple[str, str]]:
    """Wait for the same nonempty certified EB in every node's immutable partition."""
    deadline = time.monotonic() + RECOVERY_TIMEOUT_SEC
    while True:
        _search_activity(searches=searches)
        stored = {
            node: _read_immutable_ebs(db_dir / "leios.imm.db") for node, db_dir in db_dirs.items()
        }
        common_points = set.intersection(*(set(ebs) for ebs in stored.values()))
        if common_points:
            point = min(common_points)
            digests = {ebs[point] for ebs in stored.values()}
            assert len(digests) == 1, f"Nodes stored different EB bytes for {point}: {stored}"
            return point, digests.pop()
        if time.monotonic() >= deadline:
            counts = {node: len(ebs) for node, ebs in stored.items()}
            pytest.fail(f"No common immutable EB within {RECOVERY_TIMEOUT_SEC}s: {counts}")
        time.sleep(min(leios.SEARCH_STEP_SEC, max(0, deadline - time.monotonic())))


def _check_storage(*, db_dirs: dict[str, pl.Path]) -> None:
    """Check SQLite partition presence and integrity."""
    for db_dir in db_dirs.values():
        for filename in ("leios.vol.db", "leios.imm.db"):
            db_file = db_dir / filename
            assert db_file.is_file(), f"Missing LeiosDB partition: {db_file}"
            with contextlib.closing(
                sqlite3.connect(f"{db_file.as_uri()}?mode=ro", uri=True)
            ) as conn:
                result = conn.execute("PRAGMA quick_check").fetchall()
            assert result == [("ok",)], f"Invalid SQLite partition {db_file}: {result}"


def _wait_for_nodes(
    *,
    cluster_obj: clusterlib.ClusterLib,
    nodes: list[str],
    searches: dict[pl.Path, logfiles.LogCursor],
) -> None:
    """Wait for restarted node sockets to serve a non-genesis tip, with bounded retries."""
    deadline = time.monotonic() + RECOVERY_TIMEOUT_SEC
    instance_num = cluster_nodes.get_instance_num()
    pending = set(nodes)
    errors: dict[str, str] = {}
    try:
        while pending:
            _search_activity(searches=searches)
            failed = [
                status
                for status in cluster_nodes.services_status()
                if status.name.startswith(cluster_nodes.NODES_SERVICE_PREFIX)
                and status.status in {"FATAL", "EXITED", "STOPPED", "BACKOFF"}
            ]
            assert not failed, f"Node services failed during LeiosDB recovery: {failed}"
            for node in sorted(pending):
                cluster_nodes.set_cluster_env(
                    instance_num=instance_num, socket_file_name=f"{node}.socket"
                )
                try:
                    tip = cluster_obj.g_query.get_tip()
                except clusterlib.CLIError as err:
                    errors[node] = str(err)
                    continue
                if isinstance(tip.get("block"), int):
                    pending.remove(node)
                    errors.pop(node, None)
            if pending:
                if time.monotonic() >= deadline:
                    pytest.fail(f"Nodes did not recover within {RECOVERY_TIMEOUT_SEC}s: {errors}")
                time.sleep(min(5, max(0, deadline - time.monotonic())))
    finally:
        cluster_nodes.set_cluster_env(instance_num=instance_num)


def _check_outputs(
    *,
    cluster_obj: clusterlib.ClusterLib,
    nodes: list[str],
    tx_output: clusterlib.TxRawOutput,
    address: str,
    amount: int,
) -> None:
    """Check the test-owned payment output on each node without claiming identical live tips."""
    instance_num = cluster_nodes.get_instance_num()
    try:
        for node in nodes:
            cluster_nodes.set_cluster_env(
                instance_num=instance_num, socket_file_name=f"{node}.socket"
            )
            utxos = clusterlib.filter_utxos(
                utxos=cluster_obj.g_query.get_utxo(tx_raw_output=tx_output), address=address
            )
            assert len(utxos) == 1 and utxos[0].amount == amount, (
                f"Unexpected retained payment output on {node}: {utxos}"
            )
    finally:
        cluster_nodes.set_cluster_env(instance_num=instance_num)


class TestLeiosStorage:
    """Tests for SQLite LeiosDB storage and voting recovery on disposable local clusters."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.dbsync
    @pytest.mark.xdist_split(markers.XdSplits.heavy)
    def test_sqlite_restart(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_leios_storage: clusterlib.ClusterLib,
    ):
        """Check SQLite storage, retained ledger outputs, and fresh Leios activity after restart.

        * Start a dedicated cluster with the SQLite backend on every node
        * Wait for the committee if its BLS keys were not injected at genesis
        * Submit a known payment and require votes/certificates before restarting
        * Require the same nonempty immutable EB body/closure on all nodes
        * Restart nodes other than the firehose submission node, preserving their databases
        * Require restarted nodes to serve queries and every pool to vote/certify anew
        * Check the known payment on all nodes and SQLite partition integrity
        * Verify the recorded immutable EB bytes survived the reopen

        The payment is a ledger-state marker, not a claim that it was included in an
        EB. The separate immutable-EB byte check proves SQLite storage persistence.
        Read firehose's submission socket from its generated config and leave that
        node running to avoid load-generator connection errors. All nodes restart
        when firehose is disabled. Uninterrupted certification is not assumed.
        """
        cluster = cluster_leios_storage
        temp_template = common.get_test_id(cluster)
        leios.skip_if_no_ebs(cluster_obj=cluster)
        assert cluster.genesis["securityParam"] <= MAX_SECURITY_PARAM, cluster.genesis

        seated_in_genesis = leios.is_committee_seated_in_genesis(cluster_obj=cluster)
        # Four bounded observation/recovery windows plus settlement and payment waits.
        common.skip_on_long_epochs(
            cluster_obj=cluster,
            epochs=5 + (0 if seated_in_genesis else leios.VOTING_START_EPOCH),
        )
        if not seated_in_genesis:
            cluster.wait_for_epoch(epoch_no=leios.VOTING_START_EPOCH, padding_seconds=5)

        state_dir = cluster_nodes.get_cluster_env().state_dir
        nodes = sorted(cluster_nodes.get_cluster_type().NODES)
        restarted_nodes = nodes
        if configuration.ENABLE_TX_FIREHOSE:
            with open(state_dir / "tx-firehose-config.json", encoding="utf-8") as fp_in:
                firehose_config = json.load(fp_in)
            firehose_socket = (state_dir / firehose_config["socket_path"]).resolve()
            firehose_nodes = {
                node
                for node in nodes
                if (state_dir / f"{node}.socket").resolve() == firehose_socket
            }
            assert firehose_nodes, f"Unknown firehose submission socket: {firehose_socket}"
            restarted_nodes = [node for node in nodes if node not in firehose_nodes]
        assert restarted_nodes, "No nodes available for LeiosDB restart coverage"
        db_dirs = {node: state_dir / f"db-{node}" for node in nodes}
        node_logs = [state_dir / f"{node}.stdout" for node in nodes]
        pool_logs = [path for path in node_logs if path.name.startswith("pool")]
        assert pool_logs, f"No pool nodes in {nodes}"
        for node in nodes:
            with open(state_dir / f"config-{node}.json", encoding="utf-8") as fp_in:
                config = json.load(fp_in)
            assert config["LeiosDbConfig"] == {"Backend": "SQLite"}, config

        payment_addrs = addrs_common.get_payment_addrs(
            name_template=temp_template,
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )

        # Establish ledger state and positive Leios activity before the restart.
        baseline = logfiles.init_searches(node_logs)
        amount = 2_000_000
        tx_output = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_before_restart",
            txouts=[clusterlib.TxOut(address=payment_addrs[1].address, amount=amount)],
            tx_files=clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file]),
        )
        _wait_for_activity(searches=baseline, pool_logs=pool_logs, phase="before restart")
        stored_eb = _wait_for_immutable_eb(db_dirs=db_dirs, searches=baseline)
        helpers.write_json(
            out_file=pl.Path(f"{temp_template}_stored_eb.json"),
            content={"point": stored_eb[0], "body_and_closure_digests": stored_eb[1]},
        )
        cluster.wait_for_new_block(new_blocks=cluster.genesis["securityParam"] + 1)
        _check_outputs(
            cluster_obj=cluster,
            nodes=nodes,
            tx_output=tx_output,
            address=payment_addrs[1].address,
            amount=amount,
        )
        _check_storage(db_dirs=db_dirs)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        _search_activity(searches=baseline)

        # Keep firehose's submission node online. This leios_fast subset restart is
        # intentional: its longer epochs allow peer reconnection after the restart.
        logfiles.add_ignore_rule(
            files_glob="*.stdout", regex="MuxBearerClosed", ignore_file_id=cluster_manager.worker_id
        )
        recovery = logfiles.init_searches(node_logs)
        started = time.monotonic()
        with cluster_manager.respin_on_failure():
            LOGGER.info("Restarting nodes for SQLite LeiosDB recovery: %s", restarted_nodes)
            if configuration.ENABLE_TX_FIREHOSE:
                cluster_nodes.restart_nodes(restarted_nodes, delay=5)
            else:
                cluster_nodes.restart_all_nodes(delay=5)
            _wait_for_nodes(cluster_obj=cluster, nodes=restarted_nodes, searches=recovery)
            # Shutdown can emit buffered pre-restart votes. Only activity observed
            # after every restarted node serves queries proves voting recovery.
            fresh_activity = logfiles.init_searches(node_logs)
            _wait_for_activity(searches=fresh_activity, pool_logs=pool_logs, phase="after restart")
            _check_outputs(
                cluster_obj=cluster,
                nodes=nodes,
                tx_output=tx_output,
                address=payment_addrs[1].address,
                amount=amount,
            )
            _check_storage(db_dirs=db_dirs)
            point, digests = stored_eb
            for node, db_dir in db_dirs.items():
                persisted = _read_immutable_ebs(db_dir / "leios.imm.db")
                assert persisted.get(point) == digests, (
                    f"Stored EB {point} changed or disappeared after restart on {node}"
                )
            _search_activity(searches=recovery)
        LOGGER.info("SQLite LeiosDB recovery took %.1fs", time.monotonic() - started)
