# Running Leios Tests

The Leios testrun exercises the experimental Leios feature of `cardano-node` on the
experimental Dijkstra era. It is a regular regression run with a different setup, plus the
dedicated Leios tests in `cardano_node_tests/tests/test_leios_blocks.py`.

## The Leios Setup

The setup lives in [`runner/env_leios`](https://github.com/IntersectMBO/cardano-node-tests/blob/master/runner/env_leios), except `NODE_REV`, which the launcher and the CI workflow default:

| Variable              | Value                                  | Why                                                     |
| --------------------- | -------------------------------------- | ------------------------------------------------------- |
| `NODE_REV`            | `leios-prototype`                      | The node branch that has Leios.                         |
| `TESTNET_VARIANT`     | `leios_fast`                           | Starts in Dijkstra, block rate slow enough to see EBs.  |
| `PROTOCOL_VERSION`    | `12`                                   | Dijkstra protocol version.                              |
| `ENABLE_TX_FIREHOSE`  | `true`                                 | Tx load generator, needed to fill the mempool.          |
| `TX_TPS`              | `15`                                   | Tx rate ceiling of the load generator.                  |
| `DESELECT_FROM_FILE`  | `scripts/deselected_leios_tests.txt`   | Skips the tests that are known to fail here.            |

## Running in CI

Workflow [`04 Regression tests with Leios`](https://github.com/IntersectMBO/cardano-node-tests/actions/workflows/regression-leios.yaml)
triggered manually from the `Actions` tab (`Run workflow`) or with `gh`:

```sh
gh workflow run "04 Regression tests with Leios" --ref master
```

Inputs:

| Input                       | Default            | Description                                            |
| --------------------------- | ------------------ | ------------------------------------------------------ |
| `node_rev`                  | `leios-prototype`  | `cardano-node` revision.                               |
| `cli_rev`                   | (empty)            | `cardano-cli` revision, optional.                      |
| `allow_unstable_error_msgs` | `true`             | Let tests pass with unstable error messages.           |
| `skip_deselect`             | `false`            | Run also the tests that are known to fail on Leios.    |

## Running Locally

### Full Leios Regression Run

Run the commands below from the repo root.

Inside a container. Preferred, as no local Nix is needed.

```sh
./runner/runc.sh -- ./scripts/test_leios.sh
```

Or directly on the host. Local Nix is needed.

```sh
./scripts/test_leios.sh
```

Any variable from `runner/env_leios` can be overridden, but **only as a `VAR=VALUE`
argument** - the env file is loaded on top of the environment, so an exported value would be
overwritten:

```sh
./scripts/test_leios.sh TX_TPS=10
```

The exceptions are `NODE_REV`, `MARKEXPR` and `ALLOW_UNSTABLE_ERROR_MESSAGES` - there an
already exported value wins, so the node branch can be switched without touching the setup:

```sh
NODE_REV=my-leios-branch ./scripts/test_leios.sh
```

To run also the tests that are known to fail on Leios, clear the deselect file (or point it
to your own list):

```sh
./scripts/test_leios.sh DESELECT_FROM_FILE=
```

Only `VAR=VALUE` arguments are accepted; anything else is rejected with a usage message.
Results land in `run_workdir`.

## Analyzing Failures

### Locally

In Claude Code:

```text
/analyze-failures
```

### In CI

On a failed testrun the workflow runs the analysis automatically and surfaces it in

* the **run summary** of the workflow run, and a foldable `Preliminary failure analysis`
  group in the log of the `Read failure analysis into env` step,
* `failure_analysis.md` in the **`testrun-files`** artifact.

## Known Failures

`scripts/deselected_leios_tests.txt` lists the tests that are known to fail in this setup,
grouped by cause, each group with the upstream issue link (missing PlutusV2 cost model in
Dijkstra genesis, `transaction assemble` rejecting a Dijkstra `TxWitness`, missing required
signers in the Plutus script context, new Dijkstra protocol params, mandatory Leios BLS
signing keys, ...).
