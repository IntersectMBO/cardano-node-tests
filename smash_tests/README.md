# Local SMASH service tests

Tests for local `SMASH` service.
In the futre there might be extracted separate classes for things like test data (Pool), SMASH service (that will hide some request paramaters) and database connection manager.
For now because of small number of tests and uncomplicated logic the structure is simplified and test data is being kept in common data object defined in `conftest.py` which is
available for all test.

</br> 

Before running tests following services must be started on one of blockchain networks (mainnet, preprod, preview or shelley-qa). There are scripts that can easily achieve this goal and the whole process is described in detailed howvever in case user desires to run them manually here are links to instructions:

- `cardano-node` - instruction are located [here](https://github.com/input-output-hk/cardano-node/blob/master/doc/getting-started/install.md)
- `cardano-db-sync` for which instructions can be found [here](https://github.com/input-output-hk/cardano-db-sync/blob/master/doc/building-running.md)
- `SMASH` - instruction are [here](https://github.com/input-output-hk/cardano-db-sync/blob/master/doc/smash.md#installation)

If you named your databases different than it is stated in pgpass files located [here](https://github.com/input-output-hk/cardano-db-sync/tree/master/config)
then you need to adjust code in [conftest.py](https://github.com/input-output-hk/cardano-node-tests/blob/smash_tests/smash_tests/conftest.py#L24-L32) and also
add your `username` in [here](https://github.com/input-output-hk/cardano-node-tests/blob/smash_tests/smash_tests/conftest.py#L47) (and password if you decided to use one for those databases) and [here too](https://github.com/input-output-hk/cardano-node-tests/blob/smash_tests/smash_tests/conftest.py#L88)


## Scripts

There are already scripts prepared for starting all those services. 
They are located [here](https://github.com/input-output-hk/cardano-node-tests/tree/smash_tests/.github/workflows)

- `start_node.sh`
- `start_db_sync_and_smash.sh`

</br>

## How to start local cardano services from scripts

Create new directory:

```
mkdir smash-tests
cd smash-tests
```


Copy there tests scripts from `.github/workflows` folder:
- `start_node.sh`
- `start_db_sync_and_smash.sh`

Clone there `cardano-db-sync` and checkout the tag you want to test:

```sh
git clone https://github.com/input-output-hk/cardano-db-sync.git
git checkout tags/13.1.0.2
```

You directory structure should look like this:

```sh
smash-tests
.
├── cardano-db-sync
├── start_db_sync_and_smash.sh
├── start_node.sh
```

Now start cardano-node with:

```sh
./start_node.sh -e shelley-qa -t 8.1.1 -v 8.1.1

<< Script Output >>
We are here: /home/artur/Downloads/smash-tests, script name is ./start_node.sh

Creating cardano-node directory and entering it ...

Downloading cardano-node & cli archive:

Unpacking and removing archive ...

Downloading node configuration files from book.world.dev.cardano.org for shelley-qa  ...

Node configuration files located in /home/artur/Downloads/smash-tests/cardano-node/shelley-qa:

total 40
-rw-rw-r-- 1 artur artur 9459 sty  1  1970 alonzo-genesis.json
-rw-rw-r-- 1 artur artur 2698 sty  1  1970 byron-genesis.json
-rw-rw-r-- 1 artur artur 2919 sty  1  1970 config.json
-rw-rw-r-- 1 artur artur   22 sty  1  1970 conway-genesis.json
-rw-rw-r-- 1 artur artur 2527 sty  1  1970 db-sync-config.json
-rw-rw-r-- 1 artur artur 1552 sty  1  1970 shelley-genesis.json
-rw-rw-r-- 1 artur artur 2546 sty  1  1970 submit-api-config.json
-rw-rw-r-- 1 artur artur  131 sty  1  1970 topology.json

Node version: 

cardano-node 8.1.1 - linux-x86_64 - ghc-8.10
git rev 6f79e5c3ea109a70cd01910368e011635767305a

CLI version: 

cardano-cli 8.1.1 - linux-x86_64 - ghc-8.10
git rev 6f79e5c3ea109a70cd01910368e011635767305a


Starting node.
Listening on http://127.0.0.1:12798
Node configuration: NodeConfiguration {ncSocketConfig = SocketConfig {ncNodeIPv4Addr = Last {getLast = Nothing}, ncNodeIPv6Addr = Last {getLast = Nothing}, ncNodePortNumber = Last {getLast = Just 0}, ncSocketPath = Last {getLast = Just "shelley-qa/node.socket"}}, ncConfigFile = "shelley-qa/config.json", ncTopologyFile = "shelley-qa/topology.json", ncDatabaseFile = "shelley-qa/db" 
...
```

</br>

### Important

Change the name `runner` inside `conftest.py` which is used by GitHub Actions VM to your proper database user name - it is used in two places - then you can proceed to next step.

</br>

The next step is to start `cardano-db-sync` and `SMASH`:

```sh
./start_db_sync_and_smash.sh -e shelley-qa -v 13.1.0.2 -t 1000
We are here: /home/artur/Downloads/smash-tests, script name is ./start_db_sync_and_smash.sh

Entering cardano-db-sync directory

Downloading cardano-db-sync & SMASH archive:

Unpacking and removing archive ...

db-sync version: 

cardano-db-sync 13.1.0.2 - linux-x86_64 - ghc-8.10
git revision 1f3a8e341a0b6c66dbdc8b2e3ef22bc22a7fe9ff

SMASH version: 

cardano-smash-server 13.1.0.2 - linux-x86_64 - ghc-8.10
git revision 1f3a8e341a0b6c66dbdc8b2e3ef22bc22a7fe9ff
psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: FATAL:  database "shelley-test" does not exist
All good!
[db-sync-node:Info:6] [2023-07-12 17:27:47.09 UTC] Version number: 13.1.0.2
[db-sync-node:Info:6] [2023-07-12 17:27:47.09 UTC] Git hash: 1f3a8e341a0b6c66dbdc8b2e3ef22bc22a7fe9ff
[db-sync-node:Info:6] [2023-07-12 17:27:47.09 UTC] Option disable-ledger: False
[db-sync-node:Info:6] [2023-07-12 17:27:47.09 UTC] Option disable-cache: False
[db-sync-node:Info:6] [2023-07-12 17:27:47.09 UTC] Option disable-epoch: False
[db-sync-node:Info:6] [2023-07-12 17:27:47.09 UTC] Option skip-plutus-data-fix: False
[db-sync-node:Info:6] [2023-07-12 17:27:47.09 UTC] Option only-plutus-data-fix: False
[db-sync-node:Info:6] [2023-07-12 17:27:47.09 UTC] Option force-indexes: False

<< SKIPPED >>

Cache Statistics:
  Stake Addresses: cache size: 6, hit rate: 40%, hits: 6, misses: 9
  Pools: cache size: 3, hit rate: 16%, hits: 184, misses: 903
  Multi Assets: cache capacity: 250000, cache size: 0, hits: 0, misses: 0
  Previous Block: hit rate: 49%, hits: 1771, misses: 1773
[db-sync-node:Info:66] [2023-07-12 17:27:49.93 UTC] Starting epoch 5
[db-sync-node:Info:66] [2023-07-12 17:27:49.93 UTC] epochPluginInsertBlockDetails: epoch 4
[db-sync-node:Info:66] [2023-07-12 17:27:49.93 UTC] Insert Shelley Block: epoch 5, slot 22320, block 1772, hash 1ed4dc012bb27f30edcd7ac9e09879db350c8f47e8b90f80c01b997cd0c8bcb1
[db-sync-node:Info:66] [2023-07-12 17:27:49.93 UTC] Inserted 3 EpochStake for EpochNo 5


Starting SMASH...
[smash-server:Info:6] [2023-07-12 17:27:52.09 UTC] SMASH listening on port 3100


Waiting for db-sync to sync 1000 epochs ...

Latest epoch: 154
Latest epoch: 236
<< SKIPPED >>
Latest epoch: 938
Latest epoch: 1023
Latest synced epoch before starting SMASH tests: 1023
```


Once the script reaches expected epoch you can navigate to `smash-tests` directory and start tests.

```sh
cardano-node-tests/smash_tests
.
├── conftest.py
├── Pipfile
├── Pipfile.lock
├── README.md
├── test_smash_local_server.py*
```

</br>

```sh
pytest -svv test_smash_local_server.py --environment 'shelley-qa'
```

# Requirements 

- Python >= 3.10 and pip

- Install pipenv

```sh
pip install pipenv
```

- Create virtual environment

```sh
pipenv shell

Creating a virtualenv for this project...
Pipfile: /home/artur/Downloads/SMASH_FIX_TEST/cardano-node-tests/smash_tests/Pipfile
Using /usr/bin/python3 (3.10.6) to create virtualenv...
⠹ Creating virtual environment...created virtual environment CPython3.10.6.final.0-64 in 169ms
  creator CPython3Posix(dest=/home/artur/.local/share/virtualenvs/smash_tests-nrR8E8tB, clear=False, no_vcs_ignore=False, global=False)
  seeder FromAppData(download=False, pip=bundle, setuptools=bundle, wheel=bundle, via=copy, app_data_dir=/home/artur/.local/share/virtualenv)
    added seed packages: pip==23.1.2, setuptools==67.8.0, wheel==0.40.0
  activators BashActivator,CShellActivator,FishActivator,NushellActivator,PowerShellActivator,PythonActivator

✔ Successfully created virtual environment!

```

- Install dependencies

```sh
pipenv install --dev


Installing dependencies from Pipfile.lock (fc27ce)...
  🐍   ▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉ 18/18 — 00:00:09

```

-  Run tests on particular network (mainnet, preprod, preview or shelley-qa) by passing `--environment network_name` parameter.

```sh
pytest -svv test_smash_local_server.py --environment 'shelley-qa'
========================================================================================================================= test session starts =========================================================================================================================
platform linux -- Python 3.10.6, pytest-7.4.0, pluggy-1.2.0 -- /home/artur/.local/share/virtualenvs/smash_tests-nrR8E8tB/bin/python
cachedir: .pytest_cache
rootdir: /home/artur/Downloads/SMASH_FIX_TEST/cardano-node-tests
configfile: pytest.ini
collected 18 items                                                                                                                                                                                                                                                    
test_smash_local_server.py::test_health_status PASSED
test_smash_local_server.py::test_fetch_metadata_by_pool_hash PASSED
test_smash_local_server.py::test_fetch_metadata_by_pool_view PASSED
test_smash_local_server.py::test_delist_by_pool_hash PASSED
test_smash_local_server.py::test_delist_already_delisted_pool_by_pool_view PASSED
test_smash_local_server.py::test_whitelist_by_pool_hash PASSED
test_smash_local_server.py::test_whitelist_already_whitelisted_pool_by_pool_view PASSED
test_smash_local_server.py::test_reserve_ticker_no_characters PASSED
test_smash_local_server.py::test_reserve_ticker_too_short PASSED
test_smash_local_server.py::test_reserve_ticker_too_long PASSED
test_smash_local_server.py::test_reserve_ticker PASSED
test_smash_local_server.py::test_already_reserved_ticker PASSED
test_smash_local_server.py::test_pool_rejection_errors_by_pool_hash PASSED
test_smash_local_server.py::test_pool_rejection_errors_by_pool_view_with_time_filter PASSED
test_smash_local_server.py::test_pool_rejection_errors_with_future_time_filter PASSED
test_smash_local_server.py::test_pool_unregistrations PASSED
test_smash_local_server.py::test_fetch_policies PASSED
test_smash_local_server.py::test_fetch_policies_invalid_smash_url PASSED

==================================================================== 18 passed in 0.68s =====================================================================
```

</br>

# Github Actions

Tests can be run through GitHub Actions [SMASH workflow](https://github.com/input-output-hk/cardano-node-tests/actions/workflows/smash_tests.yaml)

![image](https://github.com/input-output-hk/cardano-node-tests/assets/2938515/5476e725-b9f7-475d-86c4-b7e60ef5bdd0)


Use workflow from `smash_tests` branch.
Workflow has predefined parameters for `shelley-qa` required epochs sync time.
For `preprod` use value of 60 epochs.

Particular epoch needs to be reached in order to get data for some endpoints like retired pools.
Tests try to use pools that retired first as test data but certain epoch needs to be reached in order for endpoint to return some data.

</br>

### Executables

Workflow just as scripts requires you to specify particular version of exeutables for `cardano-node` and `cardano-db-sync`. 


#### cardano-node

`cardano-node` executable is downloaded from [Releases](https://github.com/input-output-hk/cardano-node/releases) section of it's repository. 

The URL has following structure:

https://github.com/input-output-hk/cardano-node/releases/download/${tag-name}/cardano-node-${node-version}-linux.tar.gz

In this case it is important to know that sometimes tag name is not only numeric but can have some suffixes like for pre releases. In such case tag name can be `8.1.0-pre` and node version will be numeric `8.1.0`.

</br>

Tag name can be checked here (blue rectangle on the left):

![image](https://github.com/input-output-hk/cardano-node-tests/assets/2938515/a4aa3b09-0c80-48b5-9630-53d6207506cc)


</br>

#### cardano-db-sync

For now `cardano-db-sync` executable is located on AWS S3:
https://update-cardano-mainnet.iohk.io/cardano-db-sync/index.html#


In the near future executables can be migrated to Hydra CI System.







