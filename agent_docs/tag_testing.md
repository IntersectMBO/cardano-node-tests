# Tag Testing

Tag testing is the release testing of a `cardano-node` tag. For every tag we run a fixed
checklist of test activities and publish the outcome as a report page under
`src_docs/source/test_results/node/tag_<version>.rst`.

Use this document when:

- explaining the tag testing process, or one of its items, to a user;
- writing a new tag report, or extending an existing one with details.

Related docs:

- `src_docs/source/process/400_system_level_tag_testing.rst` - the high-level, audience-facing
  process description (what/where/how, not the mechanics).
- `agent_docs/running_tests.md` - running individual tests locally.
- `README.md` - the full list of test configuration environment variables.

---

## How a tag testrun is executed

Most items on the checklist end up in the same place: `runner/regression.sh`, which

1. builds `cardano-node` (and optionally `cardano-cli`, `cardano-db-sync`) with Nix from the
   revision given in `NODE_REV` - for tag testing, the tag itself (e.g. `NODE_REV=11.1.1`);
2. creates a clean `run_workdir/`, translates the CI-level inputs into cluster environment
   variables, and starts local testnet cluster instances;
3. runs `runner/run_tests.sh` (pytest with `pytest-xdist`), which produces an HTML report,
   a JUnit XML report and Allure results;
4. greps all collected artifacts for errors, stops the clusters, and archives the artifacts.

The differences between checklist items are almost entirely a matter of *which environment
variables* are set and *which tests are selected*.

Ways to launch it:

| Method | Use |
| ------ | --- |
| GitHub Actions workflow dispatch (`01 Regression tests`) | The normal path for tag testing. |
| `./runner/runc.sh -- <ENV=val ...> ./runner/regression.sh` | Same run in a container (podman/docker); the only supported way to test on a specific Linux distro. |
| `<ENV=val ...> ./runner/regression.sh` | Directly on a host that has Nix. |

The purpose-built scripts (`scripts/test_rollbacks.sh`, `scripts/test_node_reconnect.sh`,
`scripts/test_block_production.sh`) are thin wrappers that set the right environment and call
`runner/regression.sh`.

**Upgrade testing is the exception.** It does not use `runner/regression.sh` at all - it has its
own driver, `runner/node_upgrade.sh` (workflow `03 Upgrade tests`), which builds both the base
and the upgrade revision, then runs the three upgrade steps through
`runner/node_upgrade_pytest.sh`. See item 5 below.

### Where results live

- **Allure report** - the per-test result browser, published on the reports server under
  `https://cardano-tests-reports-3-74-115-22.nip.io/01-regression-tests/<testrun name>/`. The
  testrun name is chosen when the workflow is dispatched and encodes the tag and the
  configuration (e.g. `11.1.1-conway11_disk_genesis_01`); the trailing number distinguishes
  repeated runs of the same combination.
- **GitHub Actions artifacts** - `allure-results.tar.xz`, `testrun-report.html`,
  `testrun-report.xml`, `testing_artifacts.tar.xz` (node logs, configs, cluster state),
  `scheduling.log`, `errors_all.log`.
- **Test statuses** in Allure follow the convention described in
  `src_docs/source/test_results/nightly_system_tests.rst`: *failed* = assertion failure or
  unhandled exception, *broken* = hit a known issue (marked `xfail` until fixed), *skipped* =
  not applicable to that configuration.

A checklist item is marked |:heavy_check_mark:| when the run finished with no unexpected
failures. Anything else gets |:x:| plus a footnote linking the issue.

### Tag testing vs. nightly testing

The nightly pipelines run the same test suite against `cardano-node` master on a schedule, and
are documented in `src_docs/source/test_results/nightly_system_tests.rst`. Tag testing pins a
released tag and additionally covers the configurations and one-off activities listed below.

---

## The checklist items

### 1. Regression testsuite - default UTxO backend

**What it is:** the whole E2E suite (`cardano_node_tests/tests/`) against a local testnet in the
target era, with the node's default ledger DB backend.

**How it runs:** workflow `01 Regression tests` (`.github/workflows/regression.yaml`) with
`node_rev=<tag>`, `cluster_era="conway 11"`, `markexpr=all`, `utxo_backend=""`,
`consensus_mode=Praos`. Equivalent locally:

```sh
NODE_REV=11.1.1 ./runner/runc.sh -- ./runner/regression.sh
```

**Scale:** 20 pytest-xdist workers over up to 9 parallel local testnet cluster instances
(`CLUSTERS_COUNT` defaults to `min(workers, 9)`), 3 stake pools per cluster, 3 hour session
timeout. Cluster variant `local_fast` - starts directly in the target era.

**Covers:** transactions, fees, native tokens, metadata, mempool, Plutus V1/V2/V3, staking and
rewards, stake pools, KES, governance (Conway), CLI behaviour, node metrics, configuration,
socket path handling, and more.

### 2. Regression testsuite - LSM disk UTxO backend

**What it is:** the same full suite, but the node keeps the UTxO set on disk instead of in
memory.

**How it runs:** as above with `utxo_backend=disk` (env `UTXO_BACKEND=disk`).

**Mechanics:** the cluster start scripts (cardonnay) translate `UTXO_BACKEND` into the node
config `LedgerDB.Backend` field:

| `UTXO_BACKEND` | `LedgerDB.Backend` | Note |
| -------------- | ------------------ | ---- |
| unset / `empty` | not set | node default |
| `mem` | `V2InMemory` | in-memory, explicit |
| `disk` | `V2LSM` | LSM-tree on-disk backend, the current "disk" backend |
| `disklmdb` | `V1LMDB` | legacy LMDB backend |

`runner/regression.sh` lowers `MAX_TESTS_PER_CLUSTER` to 5 for the disk backends, to avoid too
many concurrent readers.

**Why it is a separate item:** the on-disk backend changes ledger state access patterns, so it
can expose issues (query hangs, performance cliffs, corruption) invisible with the in-memory
backend.

### 3. Genesis consensus mode

**What it is:** the same full suite with Ouroboros Genesis instead of the default Praos
chain-selection mode.

**How it runs:** `consensus_mode=Genesis` (env `USE_GENESIS_MODE=true`). At cluster startup a
ledger peer snapshot is generated from `pool1`, every pool's topology gets its local roots
marked `trustable` and pointed at that snapshot, `ConsensusMode` is set to `GenesisMode` in each
pool's config, and the nodes are restarted.

**Note:** this is frequently combined with the disk backend in a single run, which is why the
two checklist rows can link to the same report (e.g. `..._disk_genesis_01`).

### 4. Testing on Preview

**What it is:** a subset of the suite run against the real, long-running Preview network rather
than a local testnet.

**How it runs:** `BOOTSTRAP_DIR` points at a directory with the Preview genesis, config and
topology files plus faucet keys; the framework then starts a single `relay1` node that joins
Preview:

```sh
NODE_REV=11.1.1 BOOTSTRAP_DIR=~/tmp/preview_config/ ./runner/regression.sh
```

Setting `BOOTSTRAP_DIR` switches `TESTNET_VARIANT` to `testnets` and the run target to
`testnets`: `CLUSTERS_COUNT=1`, `FORBID_RESTART=true` (the cluster can never be restarted),
6 workers, 24 hour session timeout, `-m testnets`.

**Why only a subset:** only tests marked `@pytest.mark.testnets` are selected. Preview epochs
last 2 hours, which is already far too long for a test that waits for an epoch boundary or for a
reward cycle spanning several epochs, so those tests are excluded by construction.

**Value:** exercises the tag against real network conditions - real peers, real traffic, real
protocol parameters and a chain that cannot be reset.

### 5. Upgrade testing

**What it is:** a simulation of a real network upgrade: a cluster running the current Mainnet
release is upgraded node-by-node to the tag under test, with the network temporarily running
mixed versions.

**How it runs:** workflow `03 Upgrade tests` (`.github/workflows/upgrade.yaml`), or
`runner/node_upgrade.sh` with `BASE_TAR_URL` (a released binary tarball; see
`runner/env_nightly_upgrade`) or `BASE_REVISION`, plus `UPGRADE_REVISION=<tag>`. One cluster,
4 pools, 10 workers, `FORBID_RESTART=true`.

Three steps, driven by `runner/node_upgrade_pytest.sh`, each producing its own report:

| Step | What happens | Tests selected |
| ---- | ------------ | -------------- |
| step1 | Start the cluster on the **base** release. | `smoke or upgrade_step1` |
| step2 | Upgrade every node **except `pool3`**, regenerate configs and topologies keeping the original genesis files, restart. `pool3` keeps running the base binary, so the network is mixed-version. Plutus cost models are updated via a governance action (`test_update_cost_models`). | `smoke or upgrade_step2` |
| step3 | Upgrade `pool3` too. `pool1` uses a ledger peer snapshot taken with the base version, `pool3` one taken with the new version, both switched to `GenesisMode`. If the target protocol version is higher than the base one, `test_hardfork` performs the hard fork. | `smoke or upgrade_step3` |

The base and target protocol versions are the `BASE_PROT_VER` / `TARGET_PROT_VER` constants at
the top of `runner/node_upgrade_pytest.sh`. When they are equal (as for 11.1.1: 11 -> 11) no hard
fork is performed and the run only tests the binary upgrade. Each step also runs
`test_ignore_log_errors` first, which registers the log errors that are expected during the
upgrade.

**Value:** catches issues that only appear across versions - config/genesis incompatibilities,
mixed-version block diffusion, cost model updates, hard fork handling, and log errors that would
otherwise alarm operators.

### 6. Rollback testing

**What it is:** deliberately breaking consensus by splitting the network in two, letting both
halves accept conflicting transactions, and then reconnecting them.

**How it runs:**

```sh
./scripts/test_rollbacks.sh
```

`NUM_POOLS=10`, one cluster, no parallelism. Two scenarios:

| Test | Cluster variant | Scenario |
| ---- | --------------- | -------- |
| `test_permanent_fork` | `local_fast` (low `securityParam`) | Split, produce **more** than `securityParam` blocks on both sides, then restore the topology - the result must be a permanent fork. |
| `test_consensus_reached` | `mainnet_fast` (Mainnet-like `securityParam` and epoch length) | Split, restore the topology **before** `securityParam` blocks are produced - global consensus must be restored and the losing branch rolled back. |

Release testing always uses the non-interactive mode above, i.e. both scenarios. (The script
also has an `INTERACTIVE=1` mode that runs only `test_consensus_reached` and pauses after each
step for manual inspection; that is a debugging aid, not part of tag testing.)

**Value:** rollbacks are what applications built on Cardano must survive; this verifies the node
behaves as specified on both sides of the `securityParam` boundary.

### 7. Reconnection testing

**What it is:** stopping a block-producing node and verifying that it rejoins the network and
re-synchronizes cleanly.

**How it runs:**

```sh
TEST_RECONNECT=1 ./scripts/test_node_reconnect.sh
# or
TEST_METRICS_RECONNECT=1 ./scripts/test_node_reconnect.sh
```

`mainnet_fast` variant, one cluster, no parallelism, tests in
`cardano_node_tests/tests/test_reconnect.py`.

- `test_reconnect` - 10 iterations of: stop `pool2`, submit a Tx on `pool1`, start `pool2`,
  submit a Tx on `pool2`, wait for two blocks and check that both nodes know both transactions.
- `test_metrics_reconnect` - checks that the Prometheus peer-selection and inbound-governor
  metrics return to their expected values after a disconnect (requires exactly 3 pools).

**Value:** node restarts are routine for SPOs; this verifies no manual intervention or state
loss is involved.

### 8. Block-production testing

**What it is:** a long run measuring whether pools with equal stake produce roughly the same
number of blocks, with half of the pools on the in-memory and half on the disk UTxO backend.

**How it runs:**

```sh
BLOCK_PRODUCTION_DB=~/tmp/block_production.db ./scripts/test_block_production.sh
```

`MIXED_UTXO_BACKENDS="mem disk"` (backends are rotated over the block producers),
`NUM_POOLS=10` (must be even), `BLOCK_PRODUCTION_EPOCHS=100`, 10 hour session timeout, one
cluster. Each epoch the test saves the ledger state and appends block counts to the SQLite
database at `BLOCK_PRODUCTION_DB`, while generating transaction activity.

**Value:** a backend or configuration that silently slows down forging shows up here as a
skewed block distribution, not as a failing assertion elsewhere.

### 9. Testing on Ubuntu, Debian, Mint

**What it is:** a regression run inside a container based on a mainstream Linux distribution,
instead of the usual Alpine/NixOS container.

**How it runs:**

```sh
./runner/runc.sh --ubuntu-container=24.04 -- NODE_REV=11.1.1 ./runner/regression.sh
./runner/runc.sh --debian-container=bookworm -- NODE_REV=11.1.1 ./runner/regression.sh
./runner/runc.sh --mint-container -- NODE_REV=11.1.1 ./runner/regression.sh
```

These images require `/nix` on the host - `runner/runc.sh` bind-mounts it into the container.
`--nixos-container` is the self-contained fallback (its own `/nix` store, no host Nix needed).
Narrow the run with `MARKEXPR` or `PYTEST_ARGS` when a full suite is not needed.

**What is *not* varied:** the binaries. `cardano-node` and `cardano-cli` are still built or
fetched with Nix, and `runner/regression.sh` executes inside `nix develop`, so the node, the
Python environment and the rest of the toolchain come from the same Nix store in every
distribution. The node is therefore *not* linked against the distribution's glibc or system
libraries.

**What the distribution actually supplies:** the surrounding userland - the container image's
`/etc`, CA certificates, locale, shell and base utilities, user and permission setup,
filesystem and `/tmp` behaviour, and how the container runtime and kernel interact with it.

**Value:** confirms the node and the test framework run correctly on those base environments.
Building the node *against* a distribution's own system libraries is a separate item - see
item 13 (`cabal_build_tests`), which installs the distro dev packages and builds from source.

### 10. Shutdown testing

**What it is:** verifying that `cardano-node` shuts down cleanly through every supported
mechanism, leaving a reusable database behind. Run manually against a node synced to a
long-running testnet.

| Mechanism | How |
| --------- | --- |
| Slot-based | start the node with `--shutdown-on-slot-synced SLOT` |
| Block-based | start the node with `--shutdown-on-block-synced BLOCK` |
| IPC | `scripts/test_node_ipc_shutdown.sh` - starts the node with `--shutdown-ipc FD` reading from a FIFO, then closes the write end; the node must exit |
| Ctrl+C | send `SIGINT` to a running node |

In each case check that the node exits promptly, logs a clean shutdown, and starts again
afterwards without a database replay or corruption.

### 11. Sync testing on Mainnet (Linux)

**What it is:** a full Mainnet sync from genesis, measuring sync time, per-era and per-epoch
speed, and CPU/RSS usage, compared against the previous releases.

**Where:** the [cardano-sync-tests](https://github.com/IntersectMBO/cardano-sync-tests)
repository, not this one. Results are written up under
`src_docs/source/test_results/sync_reports/` and linked from the tag report with a `:doc:` role.

**Duration:** around 30 hours per node version.

**Value:** the only item that measures resource consumption at Mainnet scale; memory
regressions (e.g. the 11.1.0 peak-RSS regression) are found here and nowhere else.

### 12. Byron to Conway hard-fork testing

**What it is:** starting a local testnet in the **Byron** era and hard-forking it through every
intermediate era up to the target Conway protocol version, instead of starting directly in the
target era.

**How it runs:** the `local_slow` cluster variant - either `cardonnay create -t local_slow`
standalone, or a regression run with `byron_cluster=true` (env `CI_BYRON_CLUSTER=true`, which
sets `TESTNET_VARIANT=local_slow`).

**Value:** exercises every era translation and the hard-fork combinator paths that a
directly-started Conway cluster never touches.

### 13. Check build instructions changes

**What it is:** building `cardano-node` from source in a clean container by following the
documented (developer portal) build instructions, to confirm the instructions are still correct
and complete for the tag.

**How it runs:**

```sh
cd cabal_build_tests
./run.sh -d ubuntu -o 11.1.1
./run.sh -d fedora -o 11.1.1
```

The `-o` argument accepts any git reference. "Success" and exit code 0 mean the build completed.

**Value:** stale build instructions block downstream users and SPOs even when the node itself is
fine; failures here are usually reported as developer-portal PRs, not node issues.

---

## Writing or extending a tag report

### File layout

- Report file: `src_docs/source/test_results/node/tag_<version with underscores>.rst`
  (e.g. `tag_11_1_1.rst`).
- Register it at the **top** of the toctree in `src_docs/source/test_results/tag_tests.rst`
  (newest first).
- Sync report, if any: `src_docs/source/test_results/sync_reports/mainnet_<version>.rst`,
  registered in `src_docs/source/test_results/sync_tests.rst` and linked from the tag report
  with `:doc:`.

### Existing conventions

Follow the shape of the most recent report (currently `tag_11_1_1.rst`):

- Title = the bare version, underlined with `=`.
- A `Release notes` bullet linking the GitHub release.
- `Release Testing Checklists` section with two `list-table` blocks, `:widths: 64 7`,
  `:header-rows: 0`: **Regression Testsuite** (the local-cluster configuration matrix plus
  Preview) and **Other Testing** (everything else).
- Status column: `|:heavy_check_mark:|` or `|:x:|` (provided by the `sphinxemoji` extension).
- A failed item gets a superscript footnote marker and a matching line at the bottom of the
  file:

  ```rst
     * - Rollback testing
       - |:x:|\ :sup:`1`

  | \ :sup:`1` - Consensus issue, see `issue #1787 <https://github.com/IntersectMBO/ouroboros-consensus/issues/1787>`__
  ```

- Row labels link to the published Allure report where one exists.
- An optional `New issues` section listing `**[BUG]**` entries with issue links.

### Adding detail for readers unfamiliar with the testing

The checklist rows are terse by design. To make a report usable on its own, add explanatory
material **around** the checklist rather than changing its structure:

1. **A short "What was tested" intro** after the release-notes bullet: two or three sentences
   naming the tag, the base version it was upgraded from, the cluster era and protocol version,
   and whether a hard fork was involved.
2. **A description column or a glossary section** explaining what each checklist item covers.
   Use the per-item text in this document as the source, condensed to one or two sentences per
   item, in plain language and without repo-internal names (`runner/regression.sh`,
   `UTXO_BACKEND`, marker names) unless the reader needs them. Keep the wording stable across
   reports so readers can compare tags.
3. **Scope and limits**, where they are not obvious: that Preview runs only the subset of tests
   that do not need to cross an epoch boundary; that upgrade testing for this tag did or did not
   include a hard fork; that a checkmark means "no unexpected failures", with known issues
   tracked as *broken*/`xfail`.
4. **Numbers that make the run concrete** where they are known: number of tests executed,
   parallel clusters, protocol version, base release for the upgrade run.

When extending an existing report, do not silently change a status emoji or a footnote - those
record the outcome of a run that already happened. Add explanation, keep the verdicts.
