# multiIndexArray E2E coverage

Issue: [plutus-private#2261](https://github.com/IntersectMBO/plutus-private/issues/2261).
The builtin takes the array first, unlike the older signature in the issue.
The tests target PV12 with PlutusV3 / PLC 1.1.0. The node and CLI must both use
a Plutus revision that activates batch7 at PV12; a development version number
alone does not establish that activation.

## Established practice

[Array builtins](https://github.com/IntersectMBO/cardano-node-tests/pull/3268),
[dropList](https://github.com/IntersectMBO/cardano-node-tests/pull/3228), and
[multi-scalar multiplication](https://github.com/IntersectMBO/cardano-node-tests/pull/3270)
use policies from [plutus-scripts-e2e](https://github.com/mkoura/plutus-scripts-e2e).
The compiled envelopes live here and run through minting transactions. The E2E
selection samples semantic properties and checks integration, rather than copying
the complete conformance suite or its golden budgets.

The test reuses the chunked funding/subtest runner, caches funded addresses, and
checks confirmed transaction outputs and optional db-sync records. Its fixture locks
governance and Plutus resources and uses the cluster pools. When necessary, it
enacts a PlutusV3 cost-model update before running all scenarios in one test, then
marks the cluster for respin. The update can require three epochs; the fixture
skips this setup on long-epoch testnets before doing any governance work.

## Scenario selection

The source scenarios are the 26 fixtures in
[Plutus PR #7996](https://github.com/IntersectMBO/plutus/pull/7996), commit
`bfd0cd470a233376a32f21f2d39d9b75c27ff140`.

| E2E policy | Conformance cases / property |
| --- | --- |
| Integer selection | 02, 04–09: boundaries, order, duplicates and empty requests |
| Data selection | 23: preserve structured Data and repeated selections |
| Repeated 1, 64, 1024 | 06, 17: repeated lookup, inclusive limit and budget growth |
| Negative / upper bound / empty array | 10–12: invalid lookup |
| Huge positive / negative | 14–15: reject before narrowing to machine integers |
| Late invalid | 16: fail the entire request after valid indices |
| Over limit | 18: reject 1025 indices even when each is in bounds |

Each negative policy succeeds if the builtin returns any list. This prevents a
policy assertion from hiding an unexpectedly successful lookup. The E2E runner
also checks that building such a transaction fails with a `multiIndexArray:` log.

## Budgets and execution

Budgets are measured using the node's active cost model, saved by the CLI and
checked against transaction limits. The 1/64/1024 series checks budget growth;
it does not assert linear CPU pricing (the current CPU model is quadratic).
Budget subtests submit raw transactions with insufficient CPU or memory,
check rejection and unspent inputs/collateral, then mint with the measured budget.
`UNDETERMINED_COST` in the script records supplies funding estimates only; it is
not an expected or verified execution budget.

Generate the envelopes with `cabal run envelopes` in the source repository.
The batch7 modules use the common generator, updated to Plutus 1.72 by
[PR #30](https://github.com/mkoura/plutus-scripts-e2e/pull/30). Keep builtin
evaluation disabled in the compiler so the tested operation survives optimization.

`cost_models_multi_index_array_v3.json` preserves the existing 350 PlutusV3
parameters from `cost_models_list_332_350_v2_v3.json` and appends the five
`multiIndexArray` parameters in ledger `V3.ParamName` order: CPU c0/c1/c2
`326163/12304/2`, memory intercept/slope `4/3`, from Plutus's
`plutus-core/cost-model/data/builtinCostModelE.json`. Other language models are
unchanged by the proposal. These are test parameters, not proposed mainnet pricing.

The CLI must also decode PV12 scripts in `transaction policyid` and transaction
witnesses. Some development builds accept `dijkstra` commands but still decode
policies as Conway/PV11. The fixture checks policy decoding before governance setup
so this incompatibility fails immediately rather than after several epochs.

With a running PV12 cluster whose node and CLI support batch7:

```sh
PROTOCOL_VERSION=12 COMMAND_ERA=dijkstra ./ai_run.sh pytest -v \
  cardano_node_tests/tests/tests_plutus_v3/test_multi_index_array.py
```

Add `TESTNET_VARIANT=leios_fast` if that is the variant of the running cluster.
Pytest discovers this module automatically; the existing `plutus` and `team_plutus`
CI selections include it. It skips before PV12. Collection or local UPLC evaluation
does not establish that the E2E tests pass or that budgets were verified on a node.

## Local validation (2026-10-21)

- The common Plutus 1.72 generator built successfully. All 12 generated policies
  retained the runtime builtin and produced the expected local evaluator result.
  Replacing the builtin with a successful empty-list result made all seven negative
  policies succeed, verifying their failure oracle.
- On the local PV12 cluster, the fixture enacted the 355-parameter model through
  governance, including ratification and enactment checks.
- Existing batch5/6 regression: 242 subtests passed on node base `918a9df`, before
  the activation integration, validating the shared runner changes.
- New E2E suite: 27 subtests passed in 66.16 seconds after integrating activation.
  This includes confirmed mints, seven invalid requests, budget growth, rejection
  of insufficient CPU/memory, unspent inputs after rejection, and a sufficient-budget mint.
- Linters and mypy passed. db-sync was unavailable; its optional checks and remote
  CI have not been verified.

The successful online run used a **local experimental integration**, not a stock
release: node base `918a9df28077e63add5badefa5e1a40a46722d70`, Plutus 1.71 with
upstream activation commit `da649103a9cc484b15a7408b3df847fffc69bc1c` applied
unchanged, and a CLI 11.3 fix using its era-independent policy-hashing reader.
Both node and CLI used the same patched Plutus library. Published Plutus 1.71
and 1.72 still assign batch7 to `futurePV`, so neither is sufficient unmodified.
The build expression, patches, binary hashes and offline checks are preserved at
`/home/yura/projects/cardano/node/.local/integrations/mia-pv12/`.
The CLI and node report a zero Git revision for these local package builds;
use that directory's `provenance.json` for their actual sources.

Measured transaction execution units for repeated selections:

| Indices | CPU steps | Memory units |
| --- | --- | --- |
| 1 | 4,663,887 | 23,687 |
| 64 | 111,472,953 | 542,870 |
| 1024 | 1,741,003,833 | 8,454,230 |

These include the policy's list construction and assertions; they are not isolated
builtin benchmarks or stable golden budgets. Run logs are
`/tmp/mia-e2e-integrated-console.log` and `/tmp/mia-e2e-integrated.log`;
the regression log is `/tmp/mia-regression-console.log`.
