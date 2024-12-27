# README for cardano-node-tests

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

System and end-to-end (E2E) tests for cardano-node.

Check this [documentation](https://tests.cardano.intersectmbo.org) for more details.

## Running tests using Github Actions

The easiest way to run the tests is by using Github Actions.

1. fork this repository
1. enable Github Actions in your fork ("Settings" / "Actions" / "General" / "Actions permissions", check "Allow all actions and reusable workflows")
1. go to "Actions", select "01 Regression tests" (or "02 Regression tests with db-sync")
1. select "Run workflow"

![Run workflow](https://user-images.githubusercontent.com/2352619/209117914-ef3afb38-2b8b-4a4f-a03f-c9b52bccc5ba.png)

## Running tests using Nix

1. install and configure nix, follow [cardano-node documentation](https://github.com/IntersectMBO/cardano-node/blob/master/doc/getting-started/building-the-node-using-nix.md)

1. clone this repo

1. run the tests

    ```sh
    ./.github/regression.sh
    ```

---
**NOTE** <!-- markdownlint-disable-line MD036 -->

When using `CI_BYRON_CLUSTER`, it takes ~ 30 minutes for local cluster instance to get from Byron to Conway. If it seems that tests are stuck, they are likely just waiting for local cluster instances to be fully started.

---

## Running individual tests on persistent local cluster using Nix

Sometimes it is useful to run individual tests and keep the local cluster running in between test runs.

1. run nix shell that has all the needed dependencies

    ```sh
    nix flake update --accept-flake-config --override-input cardano-node "github:IntersectMBO/cardano-node/master"  # change `master` to rev you want
    nix develop --accept-flake-config
    ```

1. prepare testing environment

    ```sh
    source ./prepare_test_env.sh conway
    ```

1. start the cluster instance

    ```sh
    ./dev_workdir/conway_fast/start-cluster
    ```

1. run some test

    ```sh
    pytest -s -k test_minting_one_token cardano_node_tests/tests/tests_plutus
    # or run some tests and see all the executed `cardano-cli` commands
    pytest -s --log-level=debug -k test_minting_one_token cardano_node_tests/tests/tests_plutus
    ```

1. stop the cluster instance

    ```sh
    ./dev_workdir/conway_fast/stop-cluster
    ```

To reuse the existing testing environment in another nix shell, source the `.source` file that was generated during setup:

```sh
source ./dev_workdir/.source
```

## Variables for configuring testrun

Tests execution can be configured using env variables.

* `SCHEDULING_LOG` – specifies the path to the file where log messages for tests and cluster instance scheduler are stored
* `PYTEST_ARGS` – specifies additional arguments for pytest (default: unset)
* `MARKEXPR` – specifies marker expression for pytest (default: unset)
* `TEST_THREADS` – specifies the number of pytest workers (default: 20)
* `CLUSTERS_COUNT` – number of cluster instances that will be started (default: 9)
* `CLUSTER_ERA` – cluster era for Cardano node – used for selecting the correct cluster start script (default: conway)
* `COMMAND_ERA` – era for cardano-cli commands – can be used for creating Shelley-era (Allegra-era, ...) transactions (default: unset)
* `NUM_POOLS` – number of stake pools created in each cluster instance (default: 3)
* `ENABLE_LEGACY` – use legacy networking instead of the default P2P networking (default: unset)
* `MIXED_P2P` – use mix of P2P and legacy networking; half of stake pools using legacy and the other half P2P (default: unset)
* `UTXO_BACKEND` – 'mem' or 'disk', default is 'mem' (or legacy) backend if unset (default: unset)
* `SCRIPTS_DIRNAME` – path to a dir with local cluster start/stop scripts and configuration files (default: unset)
* `BOOTSTRAP_DIR` – path to a bootstrap dir for the given testnet (genesis files, config files, faucet data) (default: unset)
* `KEEP_CLUSTERS_RUNNING` – don't stop cluster instances after testrun is finished
  (WARNING: this implies interactive behavior when running tests using the `./.github/regression.sh` script)
* `PORTS_BASE` - base port number for ports range used by cluster instances (default: 23000)

When running tests using the `./.github/regression.sh` script, you can also use

* `CI_BYRON_CLUSTER` - start local cluster in Byron era, and progress to later eras by HFs (same effect as `SCRIPTS_DIRNAME=conway`)
* `NODE_REV` - revison of `cardano-node` (default: 'master')
* `DBSYNC_REV` - revison of `cardano-db-sync` (default: unset; db-sync is not used by default)
* `CARDANO_CLI_REV` - revison of `cardano-cli` (default: unset; cardano-cli bundled in cardano-node repo is used by default)
* `PLUTUS_APPS_REV` - revison of `plutus-apps` (default: 'main')

For example:

* running tests on local cluster intances, each with 6 stake pools and mix of P2P and legacy networking

    ```sh
    NUM_POOLS=6 MIXED_P2P=1 ./.github/regression.sh
    ```

* running tests on local cluster intances using 15 pytest workers, Conway cluster era, cluster scripts that start a cluster directly in Conway era, and selecting only tests without 'long' marker that also match the given `-k` pytest argument

    ```sh
    TEST_THREADS=15 CLUSTER_ERA=conway SCRIPTS_DIRNAME=conway_fast PYTEST_ARGS="-k 'test_stake_pool_low_cost or test_reward_amount'" MARKEXPR="not long" ./.github/regression.sh
    ```

* running tests on Shelley-qa testnet with '8.0.0' release of `cardano-node`

    ```sh
    NODE_REV=8.0.0 BOOTSTRAP_DIR=~/tmp/shelley_qa_config/ ./.github/regression.sh
    ```

## Local usage for tests development (useful only for tests developers)

Install and configure nix, follow [cardano-node documentation](https://github.com/IntersectMBO/cardano-node/blob/master/doc/getting-started/building-the-node-using-nix.md).
Install and configure poetry, follow [Poetry documentation](https://python-poetry.org/docs/#installation).

### Preparing Python virtual environment

Create a Python virtual environment and install this package together with development requirements:

```sh
./setup_dev_venv.sh
```

### Running development cluster

When running tests, the testing framework starts and stops cluster instances as needed. That is not ideal for test development, as starting a cluster instance can take up to several epochs (to get from Byron to Conway). To keep the Cardano cluster running in between test runs, one needs to start it in 'development mode':

1. cd to 'cardano-node' repo

    ```sh
    cd ../cardano-node
    ```

1. update and checkout the desired commit/tag

    ```sh
    git checkout master
    git pull origin master
    git fetch --all --tags
    git checkout tags/<tag>
    ```

1. launch devops shell

    ```sh
    nix develop .#devops
    ```

1. run fresh login shell on top of the current nix shell (to get the correct environment variables)

    ```sh
    /bin/bash --login
    ```

1. cd back to 'cardano-node-tests' repo

    ```sh
    cd ../cardano-node-tests
    ```

1. activate virtual env

    ```sh
    poetry shell
    ```

1. add virtual env to `PYTHONPATH`

    ```sh
    export PYTHONPATH="$(echo $VIRTUAL_ENV/lib/python3*/site-packages)":$PYTHONPATH
    ```

1. set env variables

    ```sh
    export CARDANO_NODE_SOCKET_PATH="$PWD/dev_workdir/state-cluster0/bft1.socket" DEV_CLUSTER_RUNNING=1
    mkdir -p "${CARDANO_NODE_SOCKET_PATH%/*}"
    ```

1. prepare cluster scripts for starting local cluster directly in Conway era

    ```sh
    prepare-cluster-scripts -c -d dev_workdir/conway_fast -s cardano_node_tests/cluster_scripts/conway_fast/
    ```

1. start the cluster instance in development mode

    ```sh
    ./dev_workdir/conway_fast/start-cluster
    ```

After the cluster starts, keys and configuration files are available in the `./dev_workdir/state-cluster0` directory. The pool-related files and keys are located in the `nodes` subdirectory, genesis keys in the `shelley` and `byron` subdirectories, and payment address with initial funds and related keys in the `byron` subdirectory. The local faucet address and related key files are stored in the `addrs_data` subdirectory.

### Restarting development cluster

To restart the running cluster (eg, after upgrading `cardano-node` and `cardano-cli` binaries), run:

```sh
./scripts/restart_dev_cluster.sh
```

---
**NOTE** <!-- markdownlint-disable-line MD036 -->

Restaring the running development cluster is useful mainly when using the "conway" start scripts (not the "conway_fast" version). It takes ~ 30 minutes for the local cluster instance to get from Byron to Conway. Starting local cluster using the "conway_fast" version takes less than 1 minute.

---

### Checking the development environment

To check that the development environment was correctly setup, run the `./check_dev_env.sh` script.

```text
$ ./check_dev_env.sh
'cardano-node' available: ✔
'cardano-cli' available: ✔
'python' available: ✔
'pytest' available: ✔
'nix-shell' available: ✔
'jq' available: ✔
'supervisord' available: ✔
'supervisorctl' available: ✔
'bech32' available: ✔
inside nix shell: ✔
in repo root: ✔
DEV cluster: ✔
python works: ✔
in python venv: ✔
venv in PYTHONPATH: ✔
cardano-node-tests installed: ✔
pytest works: ✔
same version of node and cli: ✔
socket path set: ✔
socket path correct: ✔
socket path exists: ✔
cluster era: default
command era: latest
using dbsync (optional): ✔
dbsync available: ✔
'psql' available: ✔
P2P network (optional): -
```

### Running individual tests

Example:

```sh
pytest -k "test_name1 or test_name2" cardano_node_tests
pytest -m "not long" cardano_node_tests
pytest -m smoke cardano_node_tests/tests/test_governance.py
```

### Running linters

It is sufficient to activate the Python virtual environment before running linters, development cluster is not needed:

1. activate virtual env

    ```sh
    poetry shell
    ```

1. run linters

    ```sh
    make lint
    ```

### Installing `cardano-clusterlib` in development mode

Sometimes it is useful to test local changes made to [cardano-clusterlib](https://github.com/IntersectMBO/cardano-clusterlib-py).
To install cardano-clusterlib in development mode:

1. activate virtual env

    ```sh
    poetry shell
    ```

1. update virtual env (answer 'y' to question "Install into the current virtual env? [y/N]")

    ```sh
    ./setup_dev_venv.sh
    ```

1. uninstall `cardano-clusterlib` installed by poetry

    ```sh
    pip uninstall cardano-clusterlib
    ```

1. cd to 'cardano-clusterlib-py' repo

    ```sh
    cd ../cardano-clusterlib-py
    ```

1. install `cardano-clusterlib` in development mode

    ```sh
    pip install -e . --config-settings editable_mode=compat
    ```

1. cd back to 'cardano-node-tests' repo

    ```sh
    cd -
    ```

1. check that you are really using cardano-clusterlib files from your local repo

    ```sh
    python -c 'from cardano_clusterlib import clusterlib_klass; print(clusterlib_klass.__file__)'
    ```

Note that after you run `poetry install` (eg through running `./setup_dev_venv.sh`), poetry will reinstall `cardano-clusterlib`. If you want to keep using cardano-clusterlib in development mode, you'll need to repeat the steps above.

### Updating dependencies using poetry

Edit `pyproject.toml` and run

```sh
./poetry_update_deps.sh
```

### Building documentation

Build and deploy documentation:

```sh
./deploy_doc.sh
```

## Contributing

Install this package and its dependencies as described above.

Run `pre-commit install` to set up the Git hook scripts that will check your changes before every commit. Alternatively, run `make lint` manually before pushing your changes.

Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html), with the exception that formatting is handled automatically by [Ruff](https://github.com/astral-sh/ruff) (through the `pre-commit` command).

See the [CONTRIBUTING](https://github.com/IntersectMBO/cardano-node-tests/blob/master/CONTRIBUTING.md) document for more details.
