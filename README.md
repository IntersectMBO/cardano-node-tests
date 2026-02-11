<!-- markdownlint-disable MD029 -->
# cardano-node-tests

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> **System and end-to-end (E2E) tests for cardano-node.**

📘 Check the [documentation site](https://tests.cardano.intersectmbo.org) for full usage guides, setup instructions, and details.

---

## 🚀 Running Tests with GitHub Actions

Run tests easily using GitHub Actions:

1. **Fork** this repository.

1. Enable GitHub Actions in your fork:

   * Go to `Settings` ➝ `Actions` ➝ `General` ➝ `Actions permissions`
   * Check ✅ `Allow all actions and reusable workflows`

1. Navigate to the `Actions` tab, then choose:

   * `01 Regression tests`, or
   * `02 Regression tests with db-sync`, or
   * `03 Upgrade tests`

1. Click `Run workflow` to start testing.

---

## 🛠️ Running Tests Locally with Nix

1. Install and configure Nix using the [official guide](https://github.com/input-output-hk/cardano-node-wiki/wiki/building-the-node-using-nix).

2. Clone this repository.

3. Run the regression test suite:

  ```sh
  ./.github/regression.sh
  ```

  Or run the upgrade test suite:

  ```sh
  ./.github/load-gh-env.sh .github/env_nightly_upgrade CI_BYRON_CLUSTER=false .github/node_upgrade.sh
  ```

---

## 🧪 Running Individual Tests with Custom Binaries

1. Add your custom `cardano-cli` / `cardano-node` binaries to the `.bin` directory.

2. Run a specific test:

  ```sh
  TEST_THREADS=0 CLUSTERS_COUNT=1 PYTEST_ARGS="-k 'test_minting_and_burning_sign[asset_name-build_raw-submit_cli]'" ./.github/regression.sh
  ```

3. Clean up by removing binaries from `.bin` after tests complete.

> ℹ️ **Pro Tip:** Enable full CLI command logging:

  ```sh
  PYTEST_ARGS="... --log-level=debug" ./.github/regression.sh
  ```

---

## 🔁 Persistent Local Testnet for Repeated Testing

For workflows requiring repeated test runs on a persistent testnet cluster:

1. Start a Nix shell:

  ```sh
  nix flake update --accept-flake-config --override-input cardano-node github:IntersectMBO/cardano-node/master
  nix develop --accept-flake-config
  ```

2. Set up the local test environment:

  ```sh
  make test-env
  ```

3. Activate the environment:

  ```sh
  source ./dev_workdir/activate
  ```

4. Launch the local testnet cluster:

  ```sh
  ./dev_workdir/conway_fast/start-cluster
  ```

5. Run your tests:

  ```sh
  pytest -s -k test_minting_one_token cardano_node_tests/tests/tests_plutus
  pytest -s --log-level=debug -k test_minting_one_token cardano_node_tests/tests/tests_plutus
  ```

6. Stop the testnet cluster:

  ```sh
  ./dev_workdir/conway_fast/stop-cluster
  ```

> ℹ️ **Pro Tip:** Next time, you can omit step 2 if the environment is already set up.

---

## ⚙️ Test Configuration Variables

You can fine-tune test runs using these environment variables:

| Variable                        | Description                                         |
| ------------------------------- | --------------------------------------------------- |
| `BOOTSTRAP_DIR`                 | Bootstrap testnet directory.                        |
| `CLUSTERS_COUNT`                | Number of clusters to launch (default: 9).          |
| `CLUSTER_ERA`                   | Cluster era (default: `conway`).                    |
| `COMMAND_ERA`                   | CLI command target era.                             |
| `KEEP_CLUSTERS_RUNNING`         | Don't shut down clusters after tests.               |
| `MARKEXPR`                      | Marker expression for pytest filtering.             |
| `MAX_TESTS_PER_CLUSTER`         | Max tests per cluster (default: 8).                 |
| `NUM_POOLS`                     | Number of stake pools (default: 3).                 |
| `PORTS_BASE`                    | Starting port number for cluster services.          |
| `SCHEDULING_LOG`                | Path to scheduler log output.                       |
| `TESTNET_VARIANT`               | Name of the testnet variant to use.                 |
| `UTXO_BACKEND`                  | Backend type: `mem`, `disk`, `disklmdb` or `empty`. |
| `MIXED_UTXO_BACKENDS`           | List of UTXO backends for mixed setup.              |
| `ALLOW_UNSTABLE_ERROR_MESSAGES` | Allow tests to pass with unstable error messages.   |

### Additional for `regression.sh`

| Variable           | Description                                       |
| ------------------ | ------------------------------------------------- |
| `CARDANO_CLI_REV`  | `cardano-cli` version.                            |
| `CI_BYRON_CLUSTER` | Run cluster from Byron ➝ Conway (slow start).     |
| `DBSYNC_REV`       | `cardano-db-sync` version.                        |
| `NODE_REV`         | `cardano-node` version (default: `master`).       |
| `PYTEST_ARGS`      | Extra options passed to pytest.                   |
| `TEST_THREADS`     | Number of pytest workers (default: `20`).         |
| `PROTOCOL_VERSION` | Cardano protocol version to use (default: `10`).  |

### 💡 Usage Examples

Run with 6 pools and mixed networking:

```sh
NUM_POOLS=6 ./.github/regression.sh
```

Run selective tests with filtering:

```sh
TEST_THREADS=15 PYTEST_ARGS="-k 'test_stake_pool_low_cost or test_reward_amount'" MARKEXPR="not long" ./.github/regression.sh
```

Run on preview testnet with specific node revision:

```sh
NODE_REV=10.5.1 BOOTSTRAP_DIR=~/tmp/preview_config/ ./.github/regression.sh
```

---

## 💻 Local Development for Test Authors

While the setup described in [Persistent Local Testnet for Repeated Testing](#-persistent-local-testnet-for-repeated-testing) is sufficient for most test development, test authors may require a more customizable environment, such as editable installs, different testnet variants, or multiple testnet cluster instances.

> Install [Nix](https://github.com/input-output-hk/cardano-node-wiki/wiki/building-the-node-using-nix) and [uv](https://docs.astral.sh/uv/getting-started/installation/) before proceeding.

### Set Up Python Environment

```sh
make install
```

### Activate Dev Environment

```sh
cd ../cardano-node
git checkout <tag>
cd ../cardano-node-tests
make update-node-bins repo=../cardano-node
source .source.dev
```

### Validate Dev Environment

```sh
make check-dev-env
```

### Start Development Testnet Cluster

```sh
prepare-cluster-scripts -c -d dev_workdir/conway_fast -t conway_fast
./dev_workdir/conway_fast/start-cluster
```

> Keys and configs are stored under `/var/tmp/cardonnay/state-cluster0`.

### Run Individual Tests

```sh
pytest -k "test_missing_tx_out or test_multiple_same_txins" cardano_node_tests
pytest -m smoke cardano_node_tests/tests/test_cli.py
```

### Run Linters

```sh
make lint
```

> ℹ️ **Pro Tip:** Run `make init-lint` to initialize linters and activate Git hooks.

### Reinstall `cardano-clusterlib` in Editable Mode

```sh
make reinstall-editable repo=../cardano-clusterlib-py
```

> ⚠️ After each dependencies update, repeat the step above to retain dev mode.

### Update uv Lockfile

This step is required after modifying dependencies in `pyproject.toml`.

```sh
make update-lockfile
```

### Build and Deploy Documentation

```sh
make doc
```

---

## 🤝 Contributing

* Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).
* See [CONTRIBUTING.md](https://github.com/IntersectMBO/cardano-node-tests/blob/master/CONTRIBUTING.md) for full guidelines.
