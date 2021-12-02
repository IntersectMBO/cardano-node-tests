# Local SMASH service tests

Tests for local `SMASH` service.
In the futre there might be extracted separate classes for things like test data (Pool), SMASH service (that will hide some request paramaters) and database connection manager.
For now because of time constraints, small number of tests and uncomplicated logic the structure is simplified and test data is being kept in common data object defined in `conftest.py` which is
available for all test.

</br> 

Before running tests following services must be started on one of blockchain networks (mainnet, testnet, shelley_qa):

- `cardano-db-sync` for which instructions can be found [here](https://github.com/input-output-hk/cardano-db-sync/blob/master/doc/building-running.md)
- `SMASH` - instruction are [here](https://github.com/input-output-hk/cardano-db-sync/blob/master/doc/smash.md#installation)

If you named your databases different than it is stated in pgpass files located [here](https://github.com/input-output-hk/cardano-db-sync/tree/master/config)
then you need to adjust code in [conftest.py](https://github.com/input-output-hk/cardano-node-tests/blob/smash_tests/smash_tests/conftest.py#L24-L30) and also
add your [username](https://github.com/input-output-hk/cardano-node-tests/blob/smash_tests/smash_tests/conftest.py#L45) (and password if you decided to use one for those databases)


### Requirements 

- Python >= 3.8 and pip

- Install pipenv

```sh
pip install pipenv
```

- Create virtual environment

```sh
pipenv shell

Creating a virtualenv for this project...
Pipfile: /home/artur/Software/advanced_smash_tests_final/Pipfile
Using /usr/bin/python3.8 (3.8.10) to create virtualenv...
⠹ Creating virtual environment...created virtual environment CPython3.8.10.final.0-64 in 122ms
  creator CPython3Posix(dest=/home/artur/.local/share/virtualenvs/advanced_smash_tests_final-0JlMRir1, clear=False, no_vcs_ignore=False, global=False)
  seeder FromAppData(download=False, pip=bundle, setuptools=bundle, wheel=bundle, via=copy, app_data_dir=/home/artur/.local/share/virtualenv)
    added seed packages: pip==21.3.1, setuptools==58.4.0, wheel==0.37.0
  activators BashActivator,CShellActivator,FishActivator,NushellActivator,PowerShellActivator,PythonActivator

✔ Successfully created virtual environment!
```

- Install dependencies

```sh
pipenv install --dev


Installing dependencies from Pipfile.lock (fc27ce)...
  🐍   ▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉ 18/18 — 00:00:09

```

-  Run tests on particular network (mainnet, testnet, shelley_qa) by passing `--environment network_name` parameter.

```sh
pytest -svv test_smash_local_server.py --environment testnet
==================================================================== test session starts ====================================================================
platform linux -- Python 3.8.10, pytest-6.2.5, py-1.11.0, pluggy-1.0.0 -- /usr/bin/python3
cachedir: .pytest_cache
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
