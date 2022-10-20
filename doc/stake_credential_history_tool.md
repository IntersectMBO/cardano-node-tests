# Stake credential history tool


The stake credential history tool is a useful utility that produces a linear history of events for a given stake credential.
It works with all blockchain networks including local testnets.


## Documentation

The stake credential history tool is located in the `cardano-node` repository.

Full documentation for building, running, and description of event types is [in its description](https://github.com/input-output-hk/cardano-node/blob/master/cardano-client-demo/Stake-Credential-History.md).


## Example of usage

We will use a [local test cluster](https://github.com/input-output-hk/cardano-node-tests/blob/master/doc/running_local_cluster.md) to show the stake credential history tool in action.


First, let's place a breakpoint


```python
from IPython import embed; embed()
```

in one of the tests in `test_mir_certs.py` named `test_pay_stake_addr_from_both` that sends funds from the reserves and treasury pots to a stake address, and run it with:


```sh
pytest -sv cardano_node_tests/tests/test_mir_certs.py -k 'test_pay_stake_addr_from_both'
```

Once the test stops, we will get the stake address we are interested in by using:

```python
cluster.g_query.get_stake_addr_info(registered_user.stake.address)
```

Here is a step-by-step example:


```sh
[nix-shell:~/Playground/test_framework/cardano-node-tests]$ pytest -sv cardano_node_tests/tests/test_mir_certs.py -k 'test_pay_stake_addr_from_both'
==================================================================================================================================== test session starts ====================================================================================================================================
platform linux -- Python 3.8.10, pytest-6.2.5, py-1.11.0, pluggy-1.0.0 -- /home/artur/Playground/test_framework/cardano-node-tests/.env/bin/python3
cachedir: .pytest_cache
hypothesis profile 'default' -> database=DirectoryBasedExampleDatabase('/home/artur/Playground/test_framework/cardano-node-tests/.hypothesis/examples')
metadata: {'Python': '3.8.10', 'Platform': 'Linux-5.4.0-91-generic-x86_64-with-glibc2.29', 'Packages': {'pytest': '6.2.5', 'py': '1.11.0', 'pluggy': '1.0.0'}, 'Plugins': {'hypothesis': '6.31.6', 'xdist': '2.5.0', 'forked': '1.4.0', 'allure-pytest': '2.9.45', 'html': '3.1.1', 'order': '1.0.0', 'metadata': '1.11.0'}, 'cardano-node': '1.31.0', 'cardano-node rev': '2cbe363874d0261bc62f52185cf23ed492cf4859', 'ghc': 'ghc-8.10', 'cardano-node-tests rev': '950bcf9c5417388cc50aeff09b5cb078c8dd1df7', 'cardano-node-tests url': 'https://github.com/input-output-hk/cardano-node-tests/tree/950bcf9c5417388cc50aeff09b5cb078c8dd1df7', 'CARDANO_NODE_SOCKET_PATH': '/home/artur/Playground/test_framework/cardano-node/state-cluster0/bft1.socket', 'cardano-cli exe': '/nix/store/k07rnxyzyka72678qjjpnnng9z0hwcak-cardano-cli-exe-cardano-cli-1.31.0/bin/cardano-cli', 'HAS_DBSYNC': 'True', 'db-sync': '12.0.0', 'db-sync rev': '9d0180571482ee4c6acb6fbc6bf55b5a4e2ee833', 'db-sync ghc': 'ghc-8.10', 'db-sync exe': '/nix/store/ykfw353myymhmv8v70x61kb37n5pm4ps-cardano-db-sync-exe-cardano-db-sync-12.0.0/bin/cardano-db-sync'}
rootdir: /home/artur/Playground/test_framework/cardano-node-tests, configfile: pytest.ini
plugins: hypothesis-6.31.6, xdist-2.5.0, forked-1.4.0, allure-pytest-2.9.45, html-3.1.1, order-1.0.0, metadata-1.11.0
collected 16 items / 15 deselected / 1 selected

cardano_node_tests/tests/test_mir_certs.py::TestMIRCerts::test_pay_stake_addr_from_both
-------------------------------------------------------------------------------------------------------------------------------------- live log setup ---------------------------------------------------------------------------------------------------------------------------------------
INFO     cardano_node_tests.tests.conftest:conftest.py:136 Changed CWD to '/run/user/1000/pytest-of-artur/pytest-0'.
--------------------------------------------------------------------------------------------------------------------------------------- live log call ---------------------------------------------------------------------------------------------------------------------------------------
INFO     cardano_clusterlib.clusterlib:clusterlib.py:3291 Waiting for 18.40 sec for slot no 7005.
INFO     cardano_node_tests.tests.test_mir_certs:test_mir_certs.py:659 Submitting MIR cert for tranferring funds from treasury to 'stake_test1uzkhjy8tnvx5n9exsh0j63aq83laylzl7c8gq2kmf4t9hyqg2thkv' in epoch 7 on cluster instance 0
INFO     cardano_node_tests.tests.test_mir_certs:test_mir_certs.py:672 Submitting MIR cert for tranferring funds from reserves to 'stake_test1uzkhjy8tnvx5n9exsh0j63aq83laylzl7c8gq2kmf4t9hyqg2thkv' in epoch 7 on cluster instance 0
INFO     cardano_clusterlib.clusterlib:clusterlib.py:3291 Waiting for 184.20 sec for slot no 8005.
Python 3.8.10 (default, Nov 26 2021, 20:14:08)
Type 'copyright', 'credits' or 'license' for more information
IPython 7.21.0 -- An enhanced Interactive Python. Type '?' for help.

In [1]: cluster.g_query.get_stake_addr_info(registered_user.stake.address)
Out[1]: StakeAddrInfo(address='stake_test1uzkhjy8tnvx5n9exsh0j63aq83laylzl7c8gq2kmf4t9hyqg2thkv', delegation='', reward_account_balance=100000000)

In [2]: exit
```

We have our stake address `stake_test1uzkhjy8tnvx5n9exsh0j63aq83laylzl7c8gq2kmf4t9hyqg2thkv`.
Now let's run `stake-credential-history` to see all events for this stake address:


```sh
./tools/stake-credential-history -c ../cardano-node/state-cluster0/config-bft1.json -s ../cardano-node/state-cluster0/bft1.socket --stake-address-bech32 stake_test1uzkhjy8tnvx5n9exsh0j63aq83laylzl7c8gq2kmf4t9hyqg2thkv

NEW-ERA ----------- EpochNo 1, SlotNo 1000, shelley
NEW-ERA ----------- EpochNo 2, SlotNo 2000, allegra
NEW-ERA ----------- EpochNo 3, SlotNo 3013, mary
NEW-ERA ----------- EpochNo 4, SlotNo 4010, alonzo
REGISTRATION ------ EpochNo 6, SlotNo 6885
BALANCE ----------- EpochNo 7, SlotNo 7017, balance: Lovelace 0
MIR --------------- EpochNo 7, SlotNo 7031, TreasuryMIR, Lovelace 50000000
MIR --------------- EpochNo 7, SlotNo 7080, ReservesMIR, Lovelace 50000000
BALANCE ----------- EpochNo 8, SlotNo 8014, balance: Lovelace 100000000
BALANCE ----------- EpochNo 9, SlotNo 9000, balance: Lovelace 100000000
BALANCE ----------- EpochNo 10, SlotNo 10003, balance: Lovelace 100000000
BALANCE ----------- EpochNo 11, SlotNo 11006, balance: Lovelace 100000000
BALANCE ----------- EpochNo 12, SlotNo 12000, balance: Lovelace 100000000
BALANCE ----------- EpochNo 13, SlotNo 13014, balance: Lovelace 100000000
BALANCE ----------- EpochNo 14, SlotNo 14001, balance: Lovelace 100000000
BALANCE ----------- EpochNo 15, SlotNo 15012, balance: Lovelace 100000000
BALANCE ----------- EpochNo 16, SlotNo 16010, balance: Lovelace 100000000
BALANCE ----------- EpochNo 17, SlotNo 17070, balance: Lovelace 100000000
```

With the stake credential history tool, we can see events like registrations, de-registrations, delegations, instantaneous rewards, reward withdrawals, mentions inside pool parameter registrations, and also per-epoch active stake and rewards.


```sh
./tools/stake-credential-history -c ../cardano-node/state-cluster0/config-bft1.json -s ../cardano-node/state-cluster0/bft1.socket --stake-address-bech32 stake_test1upfgrjq2wpd2k9lgw5744d3dlh0c88m7vev9qng5m7khtag5l8cmw

NEW-ERA ----------- EpochNo 1, SlotNo 1000, shelley
NEW-ERA ----------- EpochNo 2, SlotNo 2000, allegra
NEW-ERA ----------- EpochNo 3, SlotNo 3013, mary
NEW-ERA ----------- EpochNo 4, SlotNo 4010, alonzo
REGISTRATION ------ EpochNo 10, SlotNo 10022
MIR --------------- EpochNo 10, SlotNo 10046, ReservesMIR, Lovelace 50000000000000
WDRL -------------- EpochNo 10, SlotNo 10069, Lovelace 0
DE-REGISTRATION --- EpochNo 10, SlotNo 10086
MIR --------------- EpochNo 12, SlotNo 12027, ReservesMIR, Lovelace 50000000000000
```
