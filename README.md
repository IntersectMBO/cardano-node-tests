# README for cardano-node-tests

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)

System and end-to-end (E2E) tests for cardano-node.

Check this [documentation](https://input-output-hk.github.io/cardano-node-tests) for more details.

## Running tests using Github Actions

The easiest way to run the tests is by using Github Actions.

* fork this repository
* enable Github Actions in your fork ("Settings" / "Actions" / "General" / "Actions permissions", check "Allow all actions and reusable workflows")
* go to "Actions", select "01 Regression tests" (or "02 Regression tests with db-sync")
* select "Run workflow"

![Run workflow](https://user-images.githubusercontent.com/2352619/209117914-ef3afb38-2b8b-4a4f-a03f-c9b52bccc5ba.png)

## Running tests using Nix

* install and configure nix, follow [cardano-node documentation](https://github.com/input-output-hk/cardano-node/blob/master/doc/getting-started/building-the-node-using-nix.md)
* clone this repo
* run `./.github/regression.sh`

---
**NOTE** <!-- markdownlint-disable-line MD036 -->

It takes ~ 30 mins for local cluster instance to get from Byron to Babbage. If it seems that tests are stuck, they are likely just waiting for local cluster instances to be fully started.

---

To start local cluster directly in Babbage, set `CI_FAST_CLUSTER=1`.

The execution can be configured using env variables described in the section below.

In addition, you can use

* `NODE_REV` - revison of `cardano-node` (default: master HEAD)
* `CI_FAST_CLUSTER` - start local cluster directly in Babbage era, without going from Byron -> ... -> Babbage (same effect as `SCRIPTS_DIRNAME=babbage_fast`)
* `CI_ENABLE_DBSYNC` - specifies if tests will setup db-sync and perform db-sync checks (default: unset)
* `DBSYNC_REV` - revison of `cardano-db-sync` (default: master HEAD)

## Variables for configuring testrun

* `SCHEDULING_LOG` – specifies the path to the file where log messages for tests and cluster instance scheduler are stored
* `PYTEST_ARGS` – specifies additional arguments for pytest (default: unset)
* `MARKEXPR` – specifies marker expression for pytest (default: unset)
* `TEST_THREADS` – specifies the number of pytest workers (default: 20)
* `CLUSTERS_COUNT` – number of cluster instances that will be started (default: 9)
* `CLUSTER_ERA` – cluster era for Cardano node – used for selecting the correct cluster start script (default: babbage)
* `TX_ERA` – era for transactions – can be used for creating Shelley-era (Allegra-era, ...) transactions (default: unset)
* `NUM_POOLS` – number of stake pools created in each cluster instance (default: 3)
* `ENABLE_P2P` – use P2P networking instead of the default legacy networking (default: unset)
* `MIXED_P2P` – use mix of P2P and legacy networking; half of stake pools using legacy and the other half P2P (default: unset)
* `DB_BACKEND` – 'mem' or 'lmdb', default is 'mem' (or legacy) backend if unset (default: unset)
* `SCRIPTS_DIRNAME` – path to a dir with local cluster start/stop scripts and configuration files (default: unset)
* `BOOTSTRAP_DIR` – path to a bootstrap dir for the given testnet (genesis files, config files, faucet data) (default: unset)

For example:

```sh
SCHEDULING_LOG=testrun_20221224_1.log NUM_POOLS=6 MIXED_P2P=1 ./.github/regression.sh
```

or

```sh
SCHEDULING_LOG=testrun_20221224_1.log TEST_THREADS=15 CLUSTER_ERA=babbage TX_ERA=alonzo SCRIPTS_DIRNAME=babbage_fast PYTEST_ARGS="-k 'test_stake_pool_low_cost or test_reward_amount'" MARKEXPR="not long" make tests
```

or

```sh
SCHEDULING_LOG=testrun_20221224_1.log CLUSTER_ERA=babbage BOOTSTRAP_DIR=~/tmp/shelley_qa_config/ make testnets
```

## Local installation (e.g. for tests development)

Install and configure nix, follow [cardano-node documentation](https://github.com/input-output-hk/cardano-node/blob/master/doc/getting-started/building-the-node-using-nix.md).
Install and configure poetry, follow [Poetry documentation](https://python-poetry.org/docs/#installation).

Create a Python virtual environment (requires Python v3.8 or newer) and install this package together with development requirements:

```sh
./setup_venv.sh
```

## Local usage

Preparing the environment:

```sh
# cd to cardano-node repo
cd <your path to cardano-node repo>
# update and checkout the desired commit/tag
git checkout master
git pull origin master
git fetch --all --tags
git checkout tags/<tag>
# launch devops shell
nix develop .#devops

# cd to tests repo
cd <your path to cardano-node-test repo>
# activate virtual env
poetry shell
# add virtual env to PYTHONPATH
export PYTHONPATH="$(echo $VIRTUAL_ENV/lib/python3*/site-packages)":$PYTHONPATH
# set env variables
export CARDANO_NODE_SOCKET_PATH=<your path to cardano-node repo>/state-cluster0/bft1.socket
```

Check that the environment is correctly set up by running:

```text
$ ./check_env.sh
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
cluster era: babbage
transaction era: babbage
using dbsync (optional): ✔
dbsync available: ✔
P2P network (optional): -
```

Running tests on a local cluster (local cluster instances will be started automatically during test run setup):

```sh
make tests
```

Running tests on one of the testnets:

```sh
# set env variables
export CARDANO_NODE_SOCKET_PATH=<your path to cardano-node repo>/state-cluster0/relay1.socket
# run tests
BOOTSTRAP_DIR=<your path to bootstrap dir> make testnets
```

Running individual tests:

```sh
pytest -k "test_name1 or test_name2" cardano_node_tests
```

Running linter:

```sh
# activate virtual env
poetry shell
# run linter
make lint
```

## Tests development

When running tests, the testing framework starts and stops cluster instances as needed. That is not ideal for test development, as starting a cluster instance takes several epochs (to get from Byron to Babbage). To keep the Cardano cluster running in between test runs, one needs to start it in 'development mode':

```sh
# activate virtual env
poetry shell
# prepare cluster scripts
prepare-cluster-scripts -d <destination dir>/babbage -s cardano_node_tests/cluster_scripts/babbage/
# set env variables
export CARDANO_NODE_SOCKET_PATH=<your path to cardano-node repo>/state-cluster0/bft1.socket DEV_CLUSTER_RUNNING=1
# start the cluster instance in development mode
<destination dir>/babbage/start-cluster-hfc
```

After the cluster starts, keys and configuration files are available in the `<your path to cardano-node repo>/state-cluster0` directory. The pool-related files and keys are located in the `nodes` subdirectory, genesis keys in the `shelley` and `byron` subdirectories, and payment address with initial funds and related keys in the `byron` subdirectory. The local faucet address and related key files are stored in the `addrs_data` subdirectory.

To restart the cluster (eg, after upgrading `cardano-node` and `cardano-cli` binaries), run:

```sh
./scripts/restart_dev_cluster.sh
```

## Test coverage of Cardano CLI commands

To get test coverage of Cardano CLI commands, run tests as usual (`make tests`) and generate the coverage report JSON file with:

```sh
cardano-cli-coverage -i .cli_coverage/cli_coverage_*.json -o .cli_coverage/coverage_report.json
```

## Building documentation

```sh
# activate virtual env
poetry shell
# build and deploy documentation
./deploy_doc.sh
```

## Contributing

Install this package and its dependencies as described above.

Run `pre-commit install` to set up the Git hook scripts that will check your changes before every commit. Alternatively, run `make lint` manually before pushing your changes.

Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html), with the exception that formatting is handled automatically by [Black](https://github.com/psf/black) (through the `pre-commit` command).

See the [CONTRIBUTING](https://github.com/input-output-hk/cardano-node-tests/blob/master/CONTRIBUTING.md) document for more details.
