[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)

README for cardano-node-tests
=============================

Integration tests for cardano-node.

## <a id="Installation"></a> Installation

```sh
# create and activate virtual env
$ python3 -m venv .env
$ . .env/bin/activate
# install this package together with dev requirements
$ make install
```

Requires Python 3.8 or newer.

## <a id="Usage"></a> Usage

Preparing env:

```sh
# cd to cardano-node repo
$ cd /path/to/cardano-node
# update and checkout the desired commit/tag
$ git checkout master
$ git pull origin master
$ git fetch --all --tags
$ git checkout tags/<tag>
# launch devops shell
$ nix-shell -A devops
# cd to tests repo
$ cd /path/to/cardano-node-tests
# activate virtual env
$ . .env/bin/activate
```

Running tests:

```sh
# run tests
$ make tests
```

Running tests on one of the testnets:

```sh
# run tests
$ BOOTSTRAP_DIR=/path/to/bootstrap/dir make testnets
```

Running individual tests:

```sh
$ pytest -k "test_name1 or test_name2" cardano_node_tests
```

Running linter:

```sh
# activate virtual env
$ . .env/bin/activate
# run linter
$ make lint
```

Variables for `make tests` and `make testnets`
----------------------------------------------

* `SCHEDULING_LOG` - specifies path to file where log messages for tests and cluster instance scheduler are stored
* `PYTEST_ARGS` - specifies additional arguments for pytest
* `TEST_THREADS` - specifies number of pytest workers
* `CLUSTERS_COUNT` - number of cluster instances that will be started
* `CLUSTER_ERA` - cluster era for cardano node - used for selecting correct cluster start script
* `TX_ERA` - era for transactions - can be used for creating Shelley-era (Allegra-era, ...) transactions
* `NOPOOLS` - when running tests on testnet, a cluster with no staking pools will be created
* `BOOTSTRAP_DIR` - path to a bootstrap dir for given testnet (genesis files, config files, faucet data)

E.g.
```sh
$ SCHEDULING_LOG=testrun_20201208_1.log TEST_THREADS=3 CLUSTER_ERA=mary TX_ERA=shelley PYTEST_ARGS="-k 'test_stake_pool_low_cost or test_reward_amount'" make tests
```

## <a id="Tests_development"></a> Tests development

When running tests, the framework start and stop cluster instances as needed. That is not ideal for tests development, as starting a cluster instance takes several epochs (to get from Byron to Mary). To keep cardano cluster running in-between test runs, one needs to start it in "development mode".

```sh
# activate virtual env
$ . .env/bin/activate
# prepare cluster scripts
$ prepare-cluster-scripts -d scripts/destination/dir -s cardano_node_tests/cluster_scripts/mary/
# set env variables
$ export CARDANO_NODE_SOCKET_PATH=/path/to/cardano-node/state-cluster0/bft1.socket DEV_CLUSTER_RUNNING=1
# start the cluster instance in development mode
$ scripts/destination/dir/start-cluster-hfc
```

Enabling db-sync supporting
---------------------------

In order to enable **db-sync** support for local cluster one needs to:

- setup **db-sync**. The easiest way is to build **db-sync** with **nix**

 ```sh
git clone https://github.com/input-output-hk/cardano-db-sync
cd cardano-db-sync
nix-build -A cardano-db-sync -o db-sync-node
```

 Detailed Instructions can be found [here](https://github.com/input-output-hk/cardano-db-sync/blob/master/doc/building-running.md)


- follow steps related to virtual environment and package [Installation](#Installation) and run commands from [Usage](#Usage) section

- export **ENVIRONMENT VARIABLES**

 ```sh
export PGHOST=localhost PGUSER=postgres DB_SYNC_REPO="/path/to/cardano-db-sync-repo" PGPASSFILE="/path/to/postgres/pgpass" CLUSTER_COUNT=1 FORBID_RESTART=1
```
 **Note**: There is no need to manually create pgpass file - script will do that, provide only desired location for it.

- From terminal with **nix-shell** move to `cardano-node-tests` and run:

 ```sh
./scripts/postgres-start.sh "path/to/postgres"
#e.g.
./scripts/postgres-start.sh "/tmp/postgres-local"
```

 At the moment of writing this (19.V.2021) running `postgres-start.sh` may result in following output:

 ```sh
initdb: invalid locale name "en_US.UTF-8"
```

 If you encounter such error edit `cardano-node-tests/scripts/postgres-start.sh` and change following line:

 ```sh
initdb -D "$POSTGRES_DIR/data" --encoding=UTF8 --locale=en_US.UTF-8 -A trust -U "$PGUSER"
```

 to:

 ```sh
initdb -D "$POSTGRES_DIR/data" --encoding=UTF8 --no-locale -A trust -U "$PGUSER"
```

 and run script again. The successful output should look like this:

 ```sh
 2021-05-19 15:26:02.624 CEST [8454] LOG:  listening on IPv4 address "127.0.0.1", port 5432
2021-05-19 15:26:02.628 CEST [8454] LOG:  listening on Unix socket "path/to/postgres/.s.PGSQL.5432"
2021-05-19 15:26:02.639 CEST [8456] LOG:  database system was shut down at 2021-05-19 15:26:02 CEST
2021-05-19 15:26:02.642 CEST [8454] LOG:  database system is ready to accept connections
UID          PID    PPID  C STIME TTY          TIME CMD
username    8454    8378  0 15:26 pts/0    00:00:00 postgres -D path/to/postgres/data -k path/to/postgres
```

- Create database:
```sh
./scripts/postgres-setup.sh --createdb
All good!
```

- **db-sync** is enabled. One can start local cluster in [Tests Development](#Tests_development) mode with **db-sync** support. Any test with `@pytest.mark.dbsync` can be run now.

 ```sh
# example - test suite
pytest -k "test_native_tokens" cardano_node_tests

 # example - single test using db-sync checkup
pytest cardano_node_tests/tests/test_native_tokens.py -k "test_minting_unicode_asset_name"
```


 ### Troubleshooting:

 If after running script there is following error:
 ```sh
 postgres: could not access the server configuration file "path/to/postgres/data/postgresql.conf": No such file or directory
 ```
 make sure that chosen directory has proper permissions and file can be created there. It is safe to use `/tmp` directory as the destination for your postgres installation: `/tmp/postgres-local`

 If script returns with output:

 ```sh
2021-05-19 15:14:46.706 CEST [7394] LOG:  could not bind IPv4 address "127.0.0.1": Address already in use
2021-05-19 15:14:46.706 CEST [7394] HINT:  Is another postmaster already running on port 5432? If not, wait a few seconds and retry.
2021-05-19 15:14:46.706 CEST [7394] WARNING:  could not create listen socket for "localhost"
2021-05-19 15:14:46.719 CEST [7394] FATAL:  could not create any TCP/IP sockets
2021-05-19 15:14:46.720 CEST [7394] LOG:  database system is shut down
 ```
It means that `postgres` is already running on your machine. `postgres-start.sh` can kill existing `postgres` processes that were started by normal user only. In such situation one can kill `postgres` started as system process with:
```sh
systemctl stop postgresql
```
or export **PGPORT** to another port that is not occupied.

Debugging with IPython
----------------------

- Install `ipython` package in a shell that is not a `nix-shell` and where there was activated virtual environment

 ```sh
 pip install ipython
 ```

- insert:
 ```sh
from IPython import embed; embed()
```
to the line of code above the one where it fails in the test

 ```python
def test_minting_unicode_asset_name(
    self,
    cluster: clusterlib.ClusterLib,
    issuers_addrs: List[clusterlib.AddressRecord],
):

    token_mint = clusterlib_utils.TokenRecord(
        token=token,
        amount=amount,
        issuers_addrs=[issuer_addr],
        token_mint_addr=token_mint_addr,
        script=script,
    )
    from IPython import embed; embed()
```

- run with `pytest -s ...` and inspect the runtime state once it gives you the `ipython` shell

 ```sh
pytest -s cardano_node_tests/tests/test_native_tokens.py -k "test_minting_unicode_asset_name"

 Python 3.8.5 (default, Jan 27 2021, 15:41:15)
Type 'copyright', 'credits' or 'license' for more information
IPython 7.19.0 -- An enhanced Interactive Python. Type '?' for help.

 In [1]: token_mint
Out[1]: TokenRecord(token='a7eb2fe41a690735883134ce854128f07da96bed9ac056f5fc68b3301.ᘓऩ⣆nqge', amount=5, issuers_addrs=[AddressRecord(address='addr_test1vzdnfs753u3q08g5ue9t2g7c6hvtpdy5m332rxqxu9lkg3gxtejfd', vkey_file=PosixPath('token_minting_ci0_1.vkey'), skey_file=PosixPath('token_minting_ci0_1.skey'))], token_mint_addr=AddressRecord(address='addr_test1vqmhhujvsrpau0vhpkmjz0xzhxl80u4satu596eqm3zanwcxmle2u', vkey_file=PosixPath('token_minting_ci0_0.vkey'), skey_file=PosixPath('token_minting_ci0_0.skey')), script=PosixPath('test_minting_unicode_asset_name.script'))
```

- leave `IPython` interactive mode by typing `exit();`



Test coverage of cardano-cli commands
-------------------------------------

To get test coverage of cardano-cli commands, run tests as usual (`make tests`) and generate the coverage report JSON file with

```
$ cardano-cli-coverage -i .cli_coverage/cli_coverage_*.json -o .cli_coverage/coverage_report.json
```


Publishing testing results
--------------------------

Clone https://github.com/mkoura/cardano-node-tests-reports and see its [README](https://github.com/mkoura/cardano-node-tests-reports/blob/main/README.md).


Building documentation
----------------------

To build documentation using Sphinx, run

```
$ make doc
```

The documentation is generated to `docs/build/html`.

To publish documentation to https://input-output-hk.github.io/cardano-node-tests/, run:

```sh
# checkout the "github_pages" branch
$ git checkout github_pages
# copy/move content of docs/build/html to docs
$ mv docs/build/html/* docs/
# stage changes
$ git add docs
# commit changes
$ git commit
# push to origin/github_pages (upstream/github_pages)
$ git push origin github_pages
```



Contributing
------------

Install this package and its dependencies as described above.

Run `pre-commit install` to set up the git hook scripts that will check you changes before every commit. Alternatively run `make lint` manually before pushing your changes.

Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html), with the exception that formatting is handled automatically by [Black](https://github.com/psf/black) (through `pre-commit` command).
