# README for cardano-node-tests

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)

System/E2E tests for cardano-node.

Check [documentation](https://input-output-hk.github.io/cardano-node-tests) for more details.

## Installation

Create a python virtual env and install this package together with dev requirements:

```sh
./setup_venv.sh
```

Requires Python 3.8 or newer.

## Usage

Preparing env:

```sh
# cd to cardano-node repo
$ cd <your path to cardano-node repo>
# update and checkout the desired commit/tag
$ git checkout master
$ git pull origin master
$ git fetch --all --tags
$ git checkout tags/<tag>
# launch devops shell
$ nix-shell -A devops
# cd to tests repo
$ cd <your path to cardano-node-test repo>
# activate virtual env
$ . .env/bin/activate
```

Running tests on local cluster (local cluster instances will be started automatically during test run setup):

```sh
# set env variables
$ export CARDANO_NODE_SOCKET_PATH=<your path to cardano-node repo>/state-cluster0/bft1.socket
# run tests
$ make tests
```

Running tests on one of the testnets:

```sh
# set env variables
$ export CARDANO_NODE_SOCKET_PATH=<your path to cardano-node repo>/state-cluster0/relay1.socket
# run tests
$ BOOTSTRAP_DIR=<your path to bootstrap dir> make testnets
```

Running individual tests:

```sh
pytest -k "test_name1 or test_name2" cardano_node_tests
```

Running linter:

```sh
# activate virtual env
$ . .env/bin/activate
# run linter
$ make lint
```

## Variables for `make tests` and `make testnets`

* `SCHEDULING_LOG` - specifies path to file where log messages for tests and cluster instance scheduler are stored
* `PYTEST_ARGS` - specifies additional arguments for pytest
* `MARKEXPR` - specifies marker expression for pytest
* `TEST_THREADS` - specifies number of pytest workers
* `CLUSTERS_COUNT` - number of cluster instances that will be started
* `CLUSTER_ERA` - cluster era for cardano node - used for selecting correct cluster start script
* `TX_ERA` - era for transactions - can be used for creating Shelley-era (Allegra-era, ...) transactions
* `NOPOOLS` - when running tests on testnet, a cluster with no staking pools will be created
* `BOOTSTRAP_DIR` - path to a bootstrap dir for given testnet (genesis files, config files, faucet data)
* `SCRIPTS_DIRNAME` - path to a dir with local cluster start / stop scripts and configuration files

E.g.

```sh
SCHEDULING_LOG=testrun_20211012_1.log TEST_THREADS=3 CLUSTER_ERA=alonzo TX_ERA=mary SCRIPTS_DIRNAME=cardano_node_tests/cluster_scripts/alonzo/ PYTEST_ARGS="-k 'test_stake_pool_low_cost or test_reward_amount'" MARKEXPR="not long" make tests
```


## Tests development

When running tests, the testing framework starts and stops cluster instances as needed. That is not ideal for tests development, as starting a cluster instance takes several epochs (to get from Byron to Alonzo). To keep cardano cluster running in-between test runs, one needs to start it in "development mode".

```sh
# activate virtual env
$ . .env/bin/activate
# prepare cluster scripts
$ prepare-cluster-scripts -d <destination dir>/alonzo -s cardano_node_tests/cluster_scripts/alonzo/
# set env variables
$ export CARDANO_NODE_SOCKET_PATH=<your path to cardano-node repo>/state-cluster0/bft1.socket DEV_CLUSTER_RUNNING=1
# start the cluster instance in development mode
$ <destination dir>/alonzo/start-cluster-hfc
```

After starting the cluster, keys and configuration files are available in the `<your path to cardano-node repo>/state-cluster0` directory. The pools-related files and keys are located in the `nodes` subdirectory, genesis keys in the `shelley` and `byron` subdirectories, payment address with initial funds and related keys in the `byron` subdirectory. Local faucet address and related key files are stored in the `addrs_data` subdirectory.


## Test coverage of cardano-cli commands

To get test coverage of cardano-cli commands, run tests as usual (`make tests`) and generate the coverage report JSON file with

```sh
cardano-cli-coverage -i .cli_coverage/cli_coverage_*.json -o .cli_coverage/coverage_report.json
```


## Building documentation

Install Sphinx into your virtual env

```sh
make install_doc
```

Build and deploy documentation

```sh
./deploy_doc.sh
```


## Contributing

Install this package and its dependencies as described above.

Run `pre-commit install` to set up the git hook scripts that will check you changes before every commit. Alternatively run `make lint` manually before pushing your changes.

Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html), with the exception that formatting is handled automatically by [Black](https://github.com/psf/black) (through `pre-commit` command).
