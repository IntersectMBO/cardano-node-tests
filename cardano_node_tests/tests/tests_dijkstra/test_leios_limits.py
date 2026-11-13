"""Isolated honest-producer EB packing limits and the node's wire-limit guard."""

import dataclasses
import json
import math
import pathlib as pl
import re
import subprocess
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
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools
from cardano_node_tests.utils.versions import VERSIONS

pytestmark = [
    markers.SKIPIF_ON_TESTNET,
    pytest.mark.skipif(
        VERSIONS.cluster_era < VERSIONS.DIJKSTRA_FIRST, reason="Needs Dijkstra endorser blocks"
    ),
]

NUM_TXS = 30
MAX_ROUNDS = 3
MAX_COMMITTEE_WAIT_EPOCHS = 3
MEMPOOL_BYTES = 8_000_000
REFERENCE_CAP = 5 + 3 * 37
CLOSURE_CAP = 44_000
WIRE_LIMIT = 600_000
MEMORY_BUDGET = 100_000
STEPS_BUDGET = 10_000_000
SCRIPT = plutus_common.ALWAYS_SUCCEEDS["v2"]
REF_SCRIPT_SIZE = clusterlib_utils.get_reference_script_size(script_file=SCRIPT.script_file)


@pytest.fixture
def cluster_leios_limits(
    cluster_manager: cluster_management.ClusterManager, request: pytest.FixtureRequest
) -> clusterlib.ClusterLib:
    """Start a disposable singleton with isolated EB caps and no adaptive sizing.

    The transactions, rather than an external load generator, create the backlog.
    Override the mempool bytes independently of the reduced EB closure capacity.
    Suppress generators only in this instance's startup process, leaving the
    worker's environment unchanged. Cleanup forces a respin before other tests.
    """
    if configuration.DEV_CLUSTER_RUNNING or configuration.FORBID_RESPIN:
        pytest.skip("EB limit tests need a framework-managed disposable cluster")
    cap = request.param
    assert cap in ("references", "closure", "execution"), cap
    shared_tmp = temptools.get_pytest_shared_tmp()
    scriptsdir = shared_tmp / f"startup_files_leios_limit_{cap}"
    with locking.FileLockIfXdist(f"{scriptsdir}.lock"):
        if not (scriptsdir / "start-cluster").exists():
            startup = cluster_nodes.get_cluster_type().cluster_scripts.copy_scripts_files(
                destdir=scriptsdir
            )
            genesis = json.loads(startup.genesis_spec.read_text())
            leios.skip_if_no_ebs_in_genesis(genesis=genesis)
            dijkstra_file = scriptsdir / "genesis.dijkstra.spec.json"
            dijkstra = json.loads(dijkstra_file.read_text())
            if cap == "references":
                dijkstra["maxEndorserBlockReferencesSize"] = REFERENCE_CAP
            elif cap == "closure":
                dijkstra["maxEndorserBlockTxsSize"] = CLOSURE_CAP
                # The wire guard shares this otherwise compatible packing config.
                dijkstra["maxEndorserBlockReferencesSize"] = WIRE_LIMIT
            else:
                dijkstra["maxEndorserBlockExecutionUnits"] = {
                    "memory": 3 * MEMORY_BUDGET,
                    "steps": 3 * STEPS_BUDGET,
                }
                dijkstra["maxRefScriptSizePerEndorserBlock"] = 3 * REF_SCRIPT_SIZE
            helpers.write_json(out_file=dijkstra_file, content=dijkstra)

            config_file = scriptsdir / "template-config.json"
            config = json.loads(config_file.read_text())
            config["MempoolCapacityBytesOverride"] = MEMPOOL_BYTES
            config["LeiosAdaptiveEndorsements"] = {"Enabled": False}
            helpers.write_json(out_file=config_file, content=config)

            # The startup hook changes only this instance's child-shell environment.
            with (scriptsdir / "shell_env").open("a", encoding="utf-8") as shell_env:
                shell_env.write(
                    "\nunset ENABLE_TX_GENERATOR ENABLE_TX_CENTRIFUGE ENABLE_TX_FIREHOSE\n"
                )

    cluster = cluster_manager.get(
        lock_resources=[cluster_management.Resources.CLUSTER],
        prio=True,
        cleanup=True,
        scriptsdir=scriptsdir,
    )
    generator_services = {"tx_generator", "tx_centrifuge", "tx_firehose"}
    unexpected = [
        service
        for service in cluster_nodes.services_status()
        if service.name.rsplit(":", 1)[-1] in generator_services
    ]
    assert not unexpected, f"External generators configured on the isolated instance: {unexpected}"
    return cluster


@dataclasses.dataclass(frozen=True)
class _Candidate:
    """A test-owned transaction, its identities, and independently known EB costs."""

    signed: pl.Path
    tx_id: str
    wire_size: int
    raw: clusterlib.TxRawOutput
    memory: int = 0
    steps: int = 0
    reference_scripts: int = 0

    @property
    def closure_size(self) -> int:
        """Return consensus's ledger size plus its four-byte per-tx packing overhead."""
        # Valid top-level Dijkstra wire format appends one byte to the ledger encoding.
        return self.wire_size - 1 + 4


def _get_limits(pparams: dict) -> dict[str, int]:
    """Return the capacity of each independent EB packing dimension."""
    return {
        "references": pparams["maxEndorserBlockReferencesSize"] - 5,
        "closure": pparams["maxEndorserBlockTxsSize"],
        "memory": pparams["maxEndorserBlockExecutionUnits"]["memory"],
        "steps": pparams["maxEndorserBlockExecutionUnits"]["steps"],
        "reference_scripts": pparams["maxRefScriptSizePerEndorserBlock"],
    }


def _get_costs(candidate: _Candidate) -> dict[str, int]:
    """Return costs of a large top-level Dijkstra candidate in consensus's units."""
    return {
        "references": 37,
        "closure": candidate.closure_size,
        "memory": candidate.memory,
        "steps": candidate.steps,
        "reference_scripts": candidate.reference_scripts,
    }


def _wait_for_committee(*, cluster_obj: clusterlib.ClusterLib) -> None:
    """Wait at most three epoch boundaries for active keyed seats to reach quorum.

    Startup may register BLS keys only after an on-chain hard fork. Inspect the
    current set snapshot and voting state instead of assuming activation at an
    absolute epoch. An empty committee is not an unsupported query schema.
    """
    quorum = helpers.get_rational_pparam(
        cluster_obj.g_query.get_protocol_params()["leiosQuorumStakeThreshold"]
    )
    for attempt in range(MAX_COMMITTEE_WAIT_EPOCHS + 1):
        snapshot = cluster_obj.g_query.get_stake_snapshot(all_stake_pools=True)
        committee = leios.get_committee(cluster_obj=cluster_obj, snapshot=snapshot)
        # Opaque keyless seats cannot be mapped to pools, but cannot vote either.
        keyed_seats = [
            seat for seat in committee if seat.get("seatVKey", "resolved") not in (None, "Nothing")
        ]
        required = {"poolId", "key", "voting"}
        if any(required - seat.keys() for seat in keyed_seats):
            leios.skip_unsupported_committee(
                "Waiting for an active committee needs pool identities, keys and voting state."
            )
        voting_pools = {seat["poolId"] for seat in keyed_seats if seat["key"] and seat["voting"]}
        pools_stake = {pool: int(s["stakeSet"]) for pool, s in snapshot["pools"].items()}
        total_stake = sum(pools_stake.values())
        voting_stake = sum(pools_stake.get(pool, 0) for pool in voting_pools)
        if total_stake and voting_stake >= quorum * total_stake:
            return
        if attempt < MAX_COMMITTEE_WAIT_EPOCHS:
            cluster_obj.wait_for_new_epoch(new_epochs=1, padding_seconds=5)
    pytest.fail(
        f"Active keyed committee did not reach quorum {quorum} after "
        f"{MAX_COMMITTEE_WAIT_EPOCHS} epoch boundaries: "
        f"voting stake {voting_stake}/{total_stake}; committee {committee}"
    )


@dataclasses.dataclass(frozen=True)
class _Batch:
    """A funded, signed batch with the expected consumed and retained inputs."""

    records: dict[str, _Candidate]
    funding: clusterlib.TxRawOutput
    spent: list[clusterlib.UTXOData]
    retained: list[clusterlib.UTXOData]


def _prepare_batch(
    *,
    cluster_obj: clusterlib.ClusterLib,
    payment: clusterlib.AddressRecord,
    input_address: str,
    cap: str,
    name: str,
    metadata: pl.Path,
    fee: int,
) -> _Batch:
    """Fund independent candidates, then sign all of them before submitting any."""
    cluster = cluster_obj
    execution_case = cap in ("memory", "steps")
    execution_units = (
        STEPS_BUDGET if cap == "steps" else SCRIPT.execution_cost.per_time,
        MEMORY_BUDGET if cap == "memory" else SCRIPT.execution_cost.per_space,
    )
    txouts = [
        clusterlib.TxOut(
            address=input_address,
            amount=5_000_000,
            inline_datum_file=plutus_common.DATUM_42 if execution_case else "",
        )
    ] * NUM_TXS
    if execution_case:
        txouts += [clusterlib.TxOut(address=payment.address, amount=5_000_000)] * NUM_TXS
    if cap == "reference_scripts":
        txouts.append(
            clusterlib.TxOut(
                address=payment.address, amount=5_000_000, reference_script_file=SCRIPT.script_file
            )
        )
    funding = cluster.g_transaction.send_tx(
        src_address=payment.address,
        tx_name=f"{name}_fanout",
        txouts=txouts,
        tx_files=clusterlib.TxFiles(signing_key_files=[payment.skey_file]),
        join_txouts=False,
    )
    funded = {u.utxo_ix: u for u in cluster.g_query.get_utxo(tx_raw_output=funding)}
    utxos = [funded[i] for i in range(NUM_TXS)]
    collaterals = [funded[NUM_TXS + i] for i in range(NUM_TXS)] if execution_case else []
    reference = funded[NUM_TXS] if cap == "reference_scripts" else None
    if reference:
        measured = cluster.g_query.get_ref_script_size(utxo=reference)
        assert measured["refInputScriptSize"] == REF_SCRIPT_SIZE, measured

    # Explicit script budgets are the declared units charged by consensus.
    records: dict[str, _Candidate] = {}
    for i, utxo in enumerate(utxos):
        tx_name = f"{name}_tx{i}"
        script_txins = (
            [
                clusterlib.ScriptTxIn(
                    txins=[utxo],
                    script_file=SCRIPT.script_file,
                    inline_datum_present=True,
                    redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
                    execution_units=execution_units,
                    collaterals=[collaterals[i]],
                )
            ]
            if execution_case
            else []
        )
        raw = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{tx_name}.body",
            txins=[] if execution_case else [utxo],
            txouts=[clusterlib.TxOut(address=payment.address, amount=5_000_000 - fee)],
            tx_files=clusterlib.TxFiles(
                metadata_json_files=[metadata], signing_key_files=[payment.skey_file]
            ),
            script_txins=script_txins,
            readonly_reference_txins=[reference] if reference else [],
            fee=fee,
        )
        signed = cluster.g_transaction.sign_tx(
            tx_body_file=raw.out_file, signing_key_files=[payment.skey_file], tx_name=tx_name
        )
        tx_hash, size = leios.get_eb_tx_ref(tx_file=signed)
        assert 255 < size < 65_532, size
        records[tx_hash] = _Candidate(
            signed=signed,
            tx_id=cluster.g_transaction.get_txid(tx_file=signed),
            wire_size=size,
            raw=raw,
            memory=execution_units[1] if execution_case else 0,
            steps=execution_units[0] if execution_case else 0,
            reference_scripts=REF_SCRIPT_SIZE if reference else 0,
        )
    return _Batch(
        records=records,
        funding=funding,
        spent=utxos,
        retained=[*collaterals, *([reference] if reference else [])],
    )


def _check_forged_limits(
    *,
    lines: dict[pl.Path, list[str]],
    records: dict[str, _Candidate],
    cap: str,
    limits: dict[str, int],
) -> list[dict]:
    """Check every nonempty forged EB and return full boundary EBs.

    These large transactions cost 37 bytes per reference. Consensus reserves the
    widest possible map header (5 bytes), even when the actual header is smaller.
    An EB must contain only known transactions, with their exact wire sizes.
    """
    boundary: list[dict] = []
    for path, pool_lines in lines.items():
        for line in pool_lines:
            match = re.search(leios.EB_FORGED_RE, line)
            if not match:
                continue
            refs = eb_tests._get_eb_tx_refs(match.group(2))
            if not refs:
                continue
            assert refs.keys() <= records.keys(), f"Unaccounted EB transaction: {line}"
            assert all(size == records[h].wire_size for h, size in refs.items()), line
            measures = {
                dimension: sum(_get_costs(records[h])[dimension] for h in refs)
                for dimension in limits
            }
            assert all(measures[d] <= limits[d] for d in limits), (measures, limits, line)
            next_cost = min(_get_costs(r)[cap] for r in records.values())
            if len(refs) == 3 and measures[cap] + next_cost > limits[cap]:
                boundary.append(
                    {
                        "pool": path.name,
                        "slot": int(match.group(1)),
                        "references": refs,
                        "measures": measures,
                        "next_candidate_cost": next_cost,
                        "limits": limits,
                    }
                )
    return boundary


class TestLeiosLimits:
    """Test honest EB packing independently of malicious-EB aggregate validation."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.leios
    @pytest.mark.long
    @pytest.mark.dbsync
    @pytest.mark.xdist_split(markers.XdSplits.heavy)
    @pytest.mark.parametrize(
        "cluster_leios_limits",
        [
            "references",
            "closure",
            pytest.param("execution", marks=[pytest.mark.plutus, markers.SKIPIF_PLUTUSV2_UNUSABLE]),
        ],
        indirect=True,
    )
    def test_packing_caps(
        self,
        cluster_leios_limits: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        request: pytest.FixtureRequest,
        subtests: pytest_subtests.SubTests,
    ):
        """Require a full three-transaction EB while no observed EB exceeds a cap.

        Share one genesis, addresses and metadata between memory, steps and
        reference-script-byte subtests. Inflate only the tested execution
        dimension's declared budget so the entire batch still fits the other
        dimension. Reference-script payments execute no scripts, and execution
        cases reference none, so those limits are independent too. The wire
        startup guard shares the closure-cap cluster. Reference and closure-byte
        caps need distinct genesis configurations to stay independently binding.

        Wait for active keyed committee weight to reach quorum, allowing at most
        three epoch boundaries from startup, then burst-submit independent large
        transactions into an enlarged mempool.
        Verify that every other EB dimension could hold the entire batch, and that
        three candidates fit but any fourth would exceed only the selected cap.
        Match every forged EB entry to the signed file's wire hash and size.
        Require a boundary EB and eventual outputs for every submitted transaction.
        Execution cases use known-valid Plutus V2 spends with fixed declared units.
        Reference-byte cases read one script UTxO per payment without executing it,
        isolating reference bytes from execution units. Check retained reference
        inputs and collateral. Memory and steps are tested independently.
        This proves producer packing, not certified-chain effects or validation of
        deliberately malformed EBs; those are separate oracles.
        """
        cluster = cluster_leios_limits
        template = common.get_test_id(cluster)
        setup = request.node.callspec.params["cluster_leios_limits"]
        caps = ("memory", "steps", "reference_scripts") if setup == "execution" else (setup,)
        leios.skip_if_no_ebs(cluster_obj=cluster)
        seated = leios.is_committee_seated_in_genesis(cluster_obj=cluster)
        # Funding waits two blocks initially and two per round. Workload polling
        # has its own wall-clock bounds and does not wait for epoch transitions.
        funding_blocks = 2 + 2 * MAX_ROUNDS * len(caps)
        blocks_per_epoch = cluster.genesis["activeSlotsCoeff"] * cluster.genesis["epochLength"]
        funding_epochs = math.ceil(funding_blocks / blocks_per_epoch)
        common.skip_on_long_epochs(
            cluster_obj=cluster,
            epochs=funding_epochs + (0 if seated else MAX_COMMITTEE_WAIT_EPOCHS),
        )
        if setup == "closure":
            with subtests.test(check="wire_limit_startup"):
                if helpers.is_truthy_env_var("HF_TO_DIJKSTRA"):
                    pytest.skip("Peerless startup guard needs Dijkstra from genesis")
                self._check_wire_limit_startup(
                    cluster_obj=cluster, name_template=f"{template}_wire", subtests=subtests
                )
        _wait_for_committee(cluster_obj=cluster)

        pparams = cluster.g_query.get_protocol_params()
        payment = addrs_common.get_payment_addr(
            name_template=template,
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            amount=1_200_000_000,
        )
        metadata = eb_tests._get_big_metadata_file(name_template=template)
        fee = pparams["txFeePerByte"] * pparams["maxTxSize"] + pparams["txFeeFixed"] + 1_000_000
        input_address = (
            cluster.g_address.gen_payment_addr(
                addr_name=f"{template}_script", payment_script_file=SCRIPT.script_file
            )
            if setup == "execution"
            else payment.address
        )
        mempool = cluster.g_query.get_mempool_info()
        assert mempool["capacityInBytes"] >= MEMPOOL_BYTES, mempool

        for cap in caps:
            with subtests.test(cap=cap):
                self._check_packing_cap(
                    cluster_obj=cluster,
                    payment=payment,
                    input_address=input_address if cap in ("memory", "steps") else payment.address,
                    name_template=f"{template}_{cap}",
                    metadata=metadata,
                    fee=fee,
                    pparams=pparams,
                    cap=cap,
                )

    def _check_packing_cap(
        self,
        *,
        cluster_obj: clusterlib.ClusterLib,
        payment: clusterlib.AddressRecord,
        input_address: str,
        name_template: str,
        metadata: pl.Path,
        fee: int,
        pparams: dict,
        cap: str,
    ) -> None:
        """Require a full boundary EB, other dimensions non-binding, and all effects."""
        cluster = cluster_obj
        template = name_template
        limits = _get_limits(pparams)
        expected_limits = {
            "references": REFERENCE_CAP - 5,
            "closure": CLOSURE_CAP,
            "memory": 3 * MEMORY_BUDGET,
            "steps": 3 * STEPS_BUDGET,
            "reference_scripts": 3 * REF_SCRIPT_SIZE,
        }
        assert limits[cap] == expected_limits[cap], pparams
        pool_logs = sorted(cluster_nodes.get_cluster_env().state_dir.glob("pool*.stdout"))
        assert pool_logs, cluster_nodes.get_cluster_env().state_dir

        for round_no in range(MAX_ROUNDS):
            name = f"{template}_r{round_no}"
            batch = _prepare_batch(
                cluster_obj=cluster,
                payment=payment,
                input_address=input_address,
                cap=cap,
                name=name,
                metadata=metadata,
                fee=fee,
            )
            records = batch.records
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=batch.funding)

            measures = sorted(r.closure_size for r in records.values())
            assert len(records) == NUM_TXS
            # Every other dimension could hold the entire batch. Any three fit,
            # while even the cheapest four exceed the selected dimension.
            costs = sorted(_get_costs(r)[cap] for r in records.values())
            assert sum(costs[-3:]) <= limits[cap] < sum(costs[:4]), (cap, costs, limits)
            other_totals = {
                dimension: sum(_get_costs(r)[dimension] for r in records.values())
                for dimension in limits
                if dimension != cap
            }
            assert all(total < limits[d] for d, total in other_totals.items()), (
                other_totals,
                limits,
            )
            assert sum(measures) > pparams["maxBlockBodySize"], pparams
            searches = logfiles.init_searches(pool_logs)
            lines: dict[pl.Path, list[str]] = {p: [] for p in pool_logs}
            errors: dict[pl.Path, str] = {}
            for record in records.values():
                cluster.g_transaction.submit_tx_bare(tx_file=record.signed)

            deadline = time.monotonic() + 2 * leios.MAX_SEARCH_SEC
            expected = {f"{r.tx_id}#0" for r in records.values()}
            while True:
                eb_tests._collect_lines(
                    searches=searches,
                    regex=leios.EB_FORGED_RE,
                    log_errors=errors,
                    lines_per_log=lines,
                )
                boundary = _check_forged_limits(
                    lines=lines, records=records, cap=cap, limits=limits
                )
                outputs = cluster.g_query.get_utxo(txin=sorted(expected))
                if len(outputs) == NUM_TXS:
                    break
                assert time.monotonic() < deadline, f"Payments did not settle: {errors}"
                time.sleep(leios.SEARCH_STEP_SEC)
            assert not errors, errors
            assert {f"{u.utxo_hash}#{u.utxo_ix}" for u in outputs} == expected, outputs
            assert all(
                u.address == payment.address and u.amount == 5_000_000 - fee for u in outputs
            )
            assert not cluster.g_query.get_utxo(utxo=batch.spent), "Candidate inputs remain unspent"
            if batch.retained:
                assert sorted(cluster.g_query.get_utxo(utxo=batch.retained)) == sorted(
                    batch.retained
                )
            if not boundary:
                continue
            helpers.write_json(
                out_file=pl.Path(f"{name}_boundary_ebs.json"),
                content={"cap": cap, "pparams": pparams, "boundary_ebs": boundary},
            )
            for record in records.values():
                dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=record.raw)
            return

        pytest.fail(f"No full three-transaction EB observed for {cap} in {MAX_ROUNDS} batches")

    def _check_wire_limit_startup(
        self,
        *,
        cluster_obj: clusterlib.ClusterLib,
        name_template: str,
        subtests: pytest_subtests.SubTests,
    ) -> None:
        """Accept the wire boundary, then require a direct node failure one byte above.

        A running disposable cluster proves startup with a 600000-byte cap. Start
        a separate non-forging, peerless process from its generated configuration,
        with only the Dijkstra reference cap changed to 600001. Give it a new
        database, socket and ephemeral port. Require its nonzero exit status and
        the wire-limit diagnostic, not a managed cluster's startup timeout.
        Skip governance-driven hard forks: the peerless child's empty database
        cannot learn the on-chain action that activates Dijkstra.
        """
        cluster = cluster_obj
        template = name_template
        with subtests.test(boundary="accepted"):
            assert (
                cluster.g_query.get_protocol_params()["maxEndorserBlockReferencesSize"]
                == WIRE_LIMIT
            )
            assert cluster.g_query.get_mempool_info()["capacityInBytes"] >= MEMPOOL_BYTES

        state_dir = cluster_nodes.get_cluster_env().state_dir
        config = json.loads((state_dir / "config-bft1.json").read_text())
        # Config-relative genesis paths must keep pointing at the generated originals.
        for key, value in list(config.items()):
            if key.endswith("GenesisFile"):
                config[key] = str((state_dir / value).resolve())
        genesis = json.loads(pl.Path(config["DijkstraGenesisFile"]).read_text())
        genesis["maxEndorserBlockReferencesSize"] = WIRE_LIMIT + 1
        genesis_file = pl.Path(f"{template}_invalid_genesis.json").resolve()
        helpers.write_json(out_file=genesis_file, content=genesis)
        config["DijkstraGenesisFile"] = str(genesis_file)
        config.pop("DijkstraGenesisHash", None)
        # Do not bind the cluster's metrics port in the child process.
        config["TraceOptions"][""]["backends"] = ["Stdout HumanFormatUncoloured"]
        config_file = pl.Path(f"{template}_invalid_config.json").resolve()
        helpers.write_json(out_file=config_file, content=config)
        topology = pl.Path(f"{template}_empty_topology.json").resolve()
        helpers.write_json(
            out_file=topology,
            content={"localRoots": [], "publicRoots": [], "useLedgerAfterSlot": -1},
        )
        # Socket paths are short and test-owned, unlike the pytest artifact path.
        socket = state_dir / f"guard_{clusterlib.get_rand_str(4)}.socket"
        command = [
            "cardano-node",
            "run",
            "--config",
            str(config_file),
            "--topology",
            str(topology),
            "--database-path",
            str(pl.Path(f"{template}_db").resolve()),
            "--socket-path",
            str(socket),
            "--host-addr",
            "127.0.0.1",
            "--port",
            "0",
        ]
        with subtests.test(boundary="rejected"):
            log_file = pl.Path(f"{template}_invalid_node.log")
            with (
                log_file.open("w", encoding="utf-8") as log,
                subprocess.Popen(
                    command, cwd=state_dir, stdout=log, stderr=subprocess.STDOUT
                ) as node,
            ):
                try:
                    status = node.wait(timeout=60)
                finally:
                    if node.poll() is None:
                        node.terminate()
                        try:
                            node.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            node.kill()
                            node.wait()
            diagnostic = log_file.read_text()
            assert status != 0, diagnostic
            with common.allow_unstable_error_messages():
                assert (
                    "maxEndorserBlockReferencesSize (600001) exceeds the LeiosFetch "
                    "message limit (600000)"
                ) in diagnostic, diagnostic
