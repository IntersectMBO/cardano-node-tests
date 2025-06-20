<!-- markdownlint-disable MD029 -->
# cardano-node-tests

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> 📡 **System and end-to-end (E2E) tests for cardano-node.**

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
   * `02 Regression tests with db-sync`

1. Click `Run workflow` to start testing.

---

## 🛠️ Running Tests Locally with Nix

1. Install and configure Nix using the [official guide](https://github.com/input-output-hk/cardano-node-wiki/wiki/building-the-node-using-nix).

2. Clone this repository.

3. Run the test suite:

  ```sh
  ./.github/regression.sh
  ```

> ℹ️ **NOTE:** Using `CI_BYRON_CLUSTER` will cause the local cluster to progress from Byron ➝ Conway, which takes approximately 40 minutes.

---

## 🧪 Running Individual Tests with Custom Binaries

1. Add your custom `cardano-cli` / `cardano-node` binaries to the `.bin` directory.

2. Run a specific test:

  ```sh
  TEST_THREADS=0 CLUSTERS_COUNT=1 PYTEST_ARGS="-k 'test_minting_and_burning_sign[asset_name-build_raw-submit_cli]'" ./.github/regression.sh
  ```

3. Enable full CLI command logging:

  ```sh
  PYTEST_ARGS="... --log-level=debug" ./.github/regression.sh
  ```

4. Clean up by removing binaries from `.bin` after tests complete.

---

## 🔁 Persistent Local Cluster for Repeated Testing

For workflows requiring repeated test runs on a persistent cluster:

1. Start a Nix shell:

  ```sh
  nix flake update --accept-flake-config --override-input cardano-node github:IntersectMBO/cardano-node/master
  nix develop --accept-flake-config
  ```

2. Prepare the test environment:

  ```sh
  source ./prepare_test_env.sh conway
  ```

3. Launch the cluster:

  ```sh
  ./dev_workdir/conway_fast/start-cluster
  ```

4. Run your tests:

  ```sh
  pytest -s -k test_minting_one_token cardano_node_tests/tests/tests_plutus
  pytest -s --log-level=debug -k test_minting_one_token cardano_node_tests/tests/tests_plutus
  ```

5. Stop the cluster:

  ```sh
  ./dev_workdir/conway_fast/stop-cluster
  ```

To reuse the same environment in another shell:

```sh
source ./dev_workdir/.source
```

---

## ⚙️ Test Configuration Variables

You can fine-tune test runs using these environment variables:

| Variable                | Description                                |
| ----------------------- | ------------------------------------------ |
| `SCHEDULING_LOG`        | Path to scheduler log output.              |
| `PYTEST_ARGS`           | Extra options passed to pytest.            |
| `MARKEXPR`              | Marker expression for pytest filtering.    |
| `TEST_THREADS`          | Number of pytest workers (default: 20).    |
| `MAX_TESTS_PER_CLUSTER` | Max tests per cluster (default: 8).        |
| `CLUSTERS_COUNT`        | Number of clusters to launch (default: 9). |
| `CLUSTER_ERA`           | Cluster era (default: `conway`).           |
| `COMMAND_ERA`           | CLI command target era.                    |
| `NUM_POOLS`             | Number of stake pools (default: 3).        |
| `ENABLE_LEGACY`         | Use legacy networking.                     |
| `MIXED_P2P`             | Use a mix of P2P and legacy networking.    |
| `UTXO_BACKEND`          | Backend type: `mem` or `disk`.             |
| `TESTNET_VARIANT`       | Name of the testnet variant to use.        |
| `BOOTSTRAP_DIR`         | Bootstrap testnet directory.               |
| `KEEP_CLUSTERS_RUNNING` | Don't shut down clusters after tests.      |
| `PORTS_BASE`            | Starting port number for cluster services. |

### ▶️ Additional for `regression.sh`

| Variable           | Description                                 |
| ------------------ | ------------------------------------------- |
| `CI_BYRON_CLUSTER` | Run cluster from Byron ➝ Conway (slow).     |
| `NODE_REV`         | `cardano-node` version (default: `master`). |
| `DBSYNC_REV`       | `cardano-db-sync` version.                  |
| `CARDANO_CLI_REV`  | `cardano-cli` version.                      |
| `PLUTUS_APPS_REV`  | `plutus-apps` version (default: `main`).    |

### 💡 Usage Examples

Run with 6 pools and mixed networking:

```sh
NUM_POOLS=6 MIXED_P2P=1 ./.github/regression.sh
```

Run selective tests with filtering:

```sh
TEST_THREADS=15 PYTEST_ARGS="-k 'test_stake_pool_low_cost or test_reward_amount'" MARKEXPR="not long" ./.github/regression.sh
```

Run on preview testnet with specific node revision:

```sh
NODE_REV=10.4.1 BOOTSTRAP_DIR=~/tmp/preview_config/ ./.github/regression.sh
```

---

## 💻 Local Development for Test Authors

> Install [Nix](https://github.com/input-output-hk/cardano-node-wiki/wiki/building-the-node-using-nix) and [Poetry](https://python-poetry.org/docs/#installation) before proceeding.

### 🐍 Set Up Python Environment

```sh
make install
```

### 🧱 Start Development Cluster

```sh
cd ../cardano-node
git checkout <tag>
nix develop .#devops
/bin/bash --login  # fresh shell needed
cd ../cardano-node-tests
source "$(poetry env info --path)"/bin/activate
export PYTHONPATH="$(echo $VIRTUAL_ENV/lib/python3*/site-packages):$PYTHONPATH"
export CARDANO_NODE_SOCKET_PATH="$PWD/dev_workdir/state-cluster0/bft1.socket" DEV_CLUSTER_RUNNING=1
mkdir -p "${CARDANO_NODE_SOCKET_PATH%/*}"
prepare-cluster-scripts -c -d dev_workdir/conway_fast -t conway_fast
./dev_workdir/conway_fast/start-cluster
```

> Keys and configs are stored under `./dev_workdir/state-cluster0`.

### ✅ Validate Environment

```sh
make check_dev_env
```

### 🧪 Run Individual Tests

```sh
pytest -k "test_name1 or test_name2" cardano_node_tests
pytest -m "not long" cardano_node_tests
pytest -m smoke cardano_node_tests/tests/test_governance.py
```

### 🧹 Run Linters

```sh
source "$(poetry env info --path)"/bin/activate
make lint
```

### 🧰 Use `cardano-clusterlib` in Dev Mode

```sh
source "$(poetry env info --path)"/bin/activate
make install
pip uninstall cardano-clusterlib
cd ../cardano-clusterlib-py
pip install -e . --config-settings editable_mode=compat
cd -
python -c 'from cardano_clusterlib import clusterlib_klass; print(clusterlib_klass.__file__)'
```

> ⚠️ After each dependencies update, repeat the steps above to retain dev mode.

### 📦 Update Poetry Dependencies

```sh
./poetry_update_deps.sh
```

### 📚 Build Documentation

```sh
make doc
```

---

## 🤝 Contributing

* Run `pre-commit install` to activate Git hooks.
* Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).
* Use [Ruff](https://github.com/astral-sh/ruff) (via `pre-commit`) for formatting.
* See [CONTRIBUTING.md](https://github.com/IntersectMBO/cardano-node-tests/blob/master/CONTRIBUTING.md) for full guidelines.
