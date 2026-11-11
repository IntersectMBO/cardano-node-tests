# multiIndexArray E2E coverage

Issue: [plutus-private#2261](https://github.com/IntersectMBO/plutus-private/issues/2261).
The builtin takes the array first, unlike the older signature in the issue.
The tests target PV12 with PlutusV3 and PlutusV4, each compiled for PLC 1.1.0 and
1.2.0. The node and CLI must both use a Plutus revision that activates batch7 at
PV12 in both languages; a development version number alone does not establish
that activation.

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
marks the cluster for respin before proposing one cost-model update for every
tested language. The update can require three epochs; the fixture skips this
setup on long-epoch testnets before doing any governance work. All four ledger/Core
combinations run as subtests of one test and share this setup. Each combination
reuses the same scenario runner, budget-growth checks and raw budget tests.
Separate parametrized tests would each pay for the update and a respin.

The PlutusV4 policies have the same code as the PlutusV3 ones, because the
policies don't read the script context. Only the envelope `type` differs between
languages for a given Core version. The scenario definitions are shared between
both Core versions and compiled separately with their respective target versions.
Known batch7 decoding incompatibilities produce skips per combination; both
PlutusV4 variants are skipped when the transaction era is older than Dijkstra.
Other decoder errors, including missing artifacts, fail setup.

## Scenario selection

The source scenarios are the 26 fixtures in
[Plutus PR #7996](https://github.com/IntersectMBO/plutus/pull/7996), commit
`bfd0cd470a233376a32f21f2d39d9b75c27ff140`.

| E2E policy | Conformance cases / property |
| --- | --- |
| Integer selection | 02, 04–09: boundaries, order, duplicates and empty requests |
| Data selection | Adapted from 23: Integer, ByteString and homogeneous List Data |
| Repeated 1, 64, 1024 | 06, 17: repeated lookup, inclusive limit and whole-policy budget sanity |
| Negative / upper bound / empty array | 10–12: invalid lookup |
| Huge positive / negative | 14–15: reject before narrowing to machine integers |
| Late invalid | 16: fail the entire request after valid indices |
| Over limit | 18: reject 1025 indices even when each is in bounds |

Each negative policy succeeds if the builtin returns any list. This prevents a
policy assertion from hiding an unexpectedly successful lookup. The E2E runner
also requires the out-of-bounds log for invalid lookups and the distinct
maximum-index-count log for over-limit requests.

## Budgets and execution

Budgets are measured using the node's active cost model, saved by the CLI and
checked against transaction limits. The 1/64/1024 series checks whole-policy budget
growth, including list construction and result validation. Growth can persist even
with zero builtin cost, so this sanity check does not validate the builtin's pricing
or cost formula. Budget subtests submit raw transactions with one fewer CPU or
memory unit than measured, check rejection and unspent inputs/collateral, then
mint with the measured budget.
`UNDETERMINED_COST` in the script records supplies funding estimates only; it is
not an expected or verified execution budget.

Generate the envelopes with `cabal run envelopes` in the source repository.
The batch7 modules use the common generator, updated to Plutus 1.72 by
[PR #30](https://github.com/mkoura/plutus-scripts-e2e/pull/30). Keep builtin
evaluation disabled in the compiler so the tested operation survives optimization.

For PlutusV3, `cost_models_list_355_v3_v4.json` preserves the existing 350
parameters from `cost_models_list_332_350_v2_v3.json` and appends the five
`multiIndexArray` parameters in ledger `V3.ParamName` order: CPU c0/c1/c2
`326163/12304/2`, memory intercept/slope `4/3`, from Plutus's
`plutus-core/cost-model/data/builtinCostModelE.json`. Other language models are
unchanged by the proposal. These are test parameters, not proposed mainnet pricing.

The file gives PlutusV4 the same 355 values. At PV12 the PlutusV4 parameters have
the same names and order as the PlutusV3 ones, and both languages use the same
builtin semantics variant. The genesis from cardonnay gives PlutusV4 only 251
parameters. Plutus sets the missing parameters to `maxBound`, so a PlutusV4
policy that uses `multiIndexArray` exceeds any budget without this update. The
shared setup now uses `cost_models_list_369_v3_v4.json`, which preserves those
355 parameters and adds Value builtin costing. It proposes and checks both languages
at PV12+, or V3 alone at PV10/PV11, once for all builtin groups in the same test.
At PV10/PV11 the test installs all 369 V3 parameters, then requires batch7
protocol rejection: updated costing must not activate the builtins. PV9 keeps
its existing costing and still runs pre-activation rejection checks; the shared
update helper's DRep votes are forbidden during Conway bootstrap.

The CLI must also decode PV12 scripts in `transaction policyid` and transaction
witnesses. Some development builds accept `dijkstra` commands but still decode
policies as Conway/PV11. For activated builtin groups, the shared test checks each
ledger/Core group's policy decoding independently before running its scenarios.
A known incompatibility skips only that variant; a V3 decoding failure cannot
suppress V4 coverage (or vice versa). Other groups still use the shared governance
setup.

Before activation, an availability error is an expected rejection. An unknown
builtin tag instead skips the affected subtest: that older CLI decoder cannot
establish protocol rejection, so its incompatibility is not counted as a pass.

Use a `local_fast` PV12 cluster whose node and CLI support batch7. These builtin
tests do not depend on Leios features such as endorsement blocks.

```sh
TESTNET_VARIANT=local_fast PROTOCOL_VERSION=12 COMMAND_ERA=dijkstra pytest -v \
  cardano_node_tests/tests/tests_plutus/test_builtins.py \
  -k test_builtins_new_cost
```

Run the same updated-cost selection with `PROTOCOL_VERSION=11 COMMAND_ERA=conway`
on a `local_fast` PV11 cluster for batch7 V3 pre-activation coverage. V4 groups
skip because the ledger language itself is unavailable before PV12. If an
upgrade step prevents the cost-model update, pre-activation groups still run
with the existing model and must reject the scripts.

For negative coverage without updating cost models, select
`-k 'test_builtins_old and batch7'` instead. This covers multiIndexArray and Value
policies for each applicable ledger language and both PLC versions, using the
actual protocol version and each language's cost model (355 and 369 parameters
respectively). Fully enabled groups skip; pre-activation rejection and missing
costing remain separate cases. V4 groups require the Dijkstra transaction era
and PV12; V3 groups can also exercise builtin rejection before PV12. Use `-k v3`
or `-k v4` with the old-state test selection to select a particular language.

When a cost-model update is needed, the shared setup checks a worst-case wait of
five epochs against the local 90-minute limit. Long-epoch variants such as
`leios_fast` can therefore skip the updated-cost test. This guard does not apply
when the cost models are already current or to the old-state negative tests.

Pytest discovers the shared test automatically; the existing `plutus` and `team_plutus`
CI selections include it. Batch7 V3 groups also run before PV12 to check rejection.
Collection or local UPLC evaluation
does not establish that the E2E tests pass or that budgets were verified on a node.
