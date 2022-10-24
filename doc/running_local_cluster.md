# How to start a local cluster

## What is a local cluster

A **local cluster** is a local Cardano blockchain that consists of 3 stake pools and 1 BFT node. The chain is started in Byron era and it is moved to the desired era, similarly to Mainnet, using Update Proposals. The default `testnet-magic` value is 42.

Using a local cluster, you can test/play/interact with all the Cardano blockchain functionalities - from block creation to rewards generation - based on the stake delegated to each of the stake pools, generating and sending all kinds of transactions, to interact with Plutus scripts.

### Local cluster parameters

- slotLength: 200ms
- epochLength: 1000 slots per epoch (that is 200sec)
- activeSlotsCoeff: 0.1 (that means there will be ~ 100 blocks per epoch)

## Requirements

- Python 3.8 or newer
- python3-pip
- virtualenv
- nix For instructions on how to install `nix` and how to add IOHK binary cache, see <https://github.com/input-output-hk/cardano-node/blob/master/doc/getting-started/building-the-node-using-nix.md>

## Initial setup for all Cardano components

Create a directory for storing the Cardano components repositories:

```sh
mkdir Projects
cd Projects
```

Clone the Cardano projects:

**cardano-node**:

`git clone git@github.com:input-output-hk/cardano-node.git`

**cardano-db-sync**:

`git clone git@github.com:input-output-hk/cardano-db-sync.git`

**cardano-node-tests**:

`git clone git@github.com:input-output-hk/cardano-node-tests.git`

Checkout the `db-sync` version that you want to use. `cardano-node-tests` requires the `db-sync` executable file so we need to build db-sync.

```sh
cd cardano-db-sync
git checkout tags/11.0.4
nix-build -A cardano-db-sync -o db-sync-node
<< wait for build to complete - it may take a while >>
```

Go to `cardano-node-tests`, start the virtual environment and install all dependencies:

```sh
cd ../cardano-node-tests
./setup_venv.sh
<< it may take a while >>
```

For debug purposes, at this point it might be beneficial to also install `ipython`:

```sh
pip install ipython
```

Go to `cardano-node`, update and checkout the desired commit/tag:

```sh
cd ../cardano-node
git checkout master
git pull origin master
git fetch --all --tags
git checkout tags/1.30.1
```

and start nix devops shell:

`nix develop .#devops`

Go back to the tests repo and activate the virtual environment:

```sh
cd ../cardano-node-tests
. .env/bin/activate
```

> **Note:** All the remaining steps from the sections below should be performed from the `cardano-node-tests` directory


### Starting Postgres test database

**Usage of a separate Postgres database for testing is not mandatory.**

There is a script for creating test database: `cardano-node-tests/scripts/postgres-start.sh`

**Option 1:** If you want to start a script that will use default values for Postgres
port/username and you already have a running Postgres instance on your machine
then you need to stop it first:

`sudo service postgresql stop`

Then you can start a separate Postgres instance that will be used for creating test database **dbsync0**.

`./scripts/postgres-start.sh "/home/username/Projects/tmp/postgres-qa" -k`

As a first argument, pass a directory where you would like to have your DB data to be stored.  After the script ends, `postgres-qa` directory should be created with `data` directory inside it under the specified path.

The "k" switch will kill any already running Postgres process (assuming it belongs to a regular user, not root) and remove the `data` directory (if you already used that directory in other test sessions).


**Option 2**: If you don't want to stop your local Postgres instance that is already running on port **5432** then you can simply export environment variables with values that will not collide with your default Postgres setup, like:

`export PGHOST=localhost PGPORT=5434 PGUSER=postgres_dbsync`

and then run this script:

`./scripts/postgres-start.sh "/home/username/Projects/tmp/postgres-qa" -k`

that will start a separate Postgres database for testing.

Now you are ready to run the preparation script (which is described in detail in the next section).


### Preparing local cluster scripts

Inside `cardano-node-tests/cardano_node_tests/cluster_scripts` there are folders with all the data and scripts necessary for starting the cluster in various eras and setups:

- `Alonzo` - prepare files to start the cluster in `Byron` era and transition it through update proposals to `Alonzo` era. Once the cluster is ready it will be in `Alonzo` era and the decentralization parameter is set to: `d = 0`.

- `testnets_nopools` - prepare files so the cluster will run on the real network but without setting up our own pool there.

- `testnets` - prepare files so the cluster will run on the real network and will also have a stake pool configured there. The tests that require pool presence will be run there.

**The procedure for setting up a local cluster on a real network will be described in a separate document and a link will be added here later.**


If you want to prepare the local cluster for running in the `Alonzo` era, use:

`prepare-cluster-scripts -d scripts/destination/dir -s cardano_node_tests/cluster_scripts/alonzo`

Once you run this preparation script, you will notice a new directory with template files and scripts located in:
`cardano-node-tests/scripts/destination/dir`

Those files will be used by the script that will start our local cluster.


### Adjusting some parameter values

> **Note:** This step is optional.

After running the preparation script, a new directory with template files and scripts will be created:
`cardano-node-tests/scripts/destination/dir`

You can find there a `start-cluster-hfc` script that contains all the code that starts the local cluster.

If you want, for example, to change the value of the decentralization parameter, just search for that keyword
in the code of `start-cluster-hfc` and after finding the right place edit it:

```sh
MARY_HF_PROPOSAL="$STATE_CLUSTER/shelley/update-proposal-mary.proposal"

cardano_cli_log governance create-update-proposal \
  --out-file "$MARY_HF_PROPOSAL" \
  --epoch 2 \
  "${GENESIS_VERIFICATION[@]}" \
  --decentralization-parameter 0 \
  --protocol-major-version 4 \
  --protocol-minor-version 0
```

If you would like to adjust some Alonzo-specific parameters like `maxCollateralInputs`, the  procedure
would be the same - use the search option in your editor, find the proper place and edit it according to your needs:

```sh
jq -r '
  .maxValueSize = 5000 |
  .collateralPercentage = 150 |
  .maxCollateralInputs = 3 |
  .executionPrices.prSteps = 0.0000721 |
  .executionPrices.prMem = 0.0577 |
  .lovelacePerUTxOWord = 34482 |
  .maxBlockExUnits.exUnitsMem = 50000000 |
  .maxBlockExUnits.exUnitsSteps = 40000000000 |
  .maxTxExUnits.exUnitsMem = 10000000 |
  .maxTxExUnits.exUnitsSteps = 10000000000' \
  < "$STATE_CLUSTER/shelley/genesis.alonzo.json" > "$STATE_CLUSTER/shelley/genesis.alonzo.json_jq"
```


### Start the local cluster

The last step is to export **CARDANO_NODE_SOCKET_PATH**, **DBSYNC_REPO** and **DEV_CLUSTER_RUNNING** environment variables

`export CARDANO_NODE_SOCKET_PATH=/home/username/Projects/cardano-node/state-cluster0/bft1.socket`

`export DEV_CLUSTER_RUNNING=1`

Now export the location for `db-sync` so the test tool knows its location and can start it:

`export DBSYNC_REPO="/home/username/Projects/cardano-db-sync"`

Finally, we can start our local cluster with:

`scripts/destination/dir/start-cluster-hfc`

The local cluster will start 3 stake pools and 1 BFT node.

Once the cluster is started, you can see that there is a directory created inside `cardano-node`
with all the local cluster files:
`cardano-node/state-cluster0`

#### OUTPUT example

```sh
[nix-shell:~/Projects/cardano-node-tests]$ scripts/destination/dir/start-cluster-hfc
Deleting db dbsync0
NOTICE:  database "dbsync0" does not exist, skipping
Setting up db dbsync0
Generating Pool 1 Secrets
Generating Pool 1 Metadata
Generating Pool 2 Secrets
Generating Pool 2 Metadata
Generating Pool 3 Secrets
Generating Pool 3 Metadata
Waiting 5 seconds for bft node to start
Moving funds out of Byron genesis
Transaction successfully submitted.
Waiting 202 sec for Shelley era to start
Starting db-sync
dbsync: started
Submitting update proposal to transfer to Allegra, transfering funds to pool owners, registering pools and delegations
Transaction successfully submitted.
Waiting 202 sec for Allegra era to start
Submitting update proposal to transfer to Mary, set d = 0
Transaction successfully submitted.
Waiting 202 sec for Mary era to start
Submitting update proposal to transfer to Alonzo
Transaction successfully submitted.
Waiting 15 sec before restarting the nodes
nodes:bft1: stopped
nodes:pool1: stopped
nodes:pool2: stopped
nodes:pool3: stopped
nodes:bft1: started
nodes:pool1: started
nodes:pool2: started
nodes:pool3: started
Waiting 187 sec for Alonzo era to start
Cluster started. Run `/home/artur/Projects/cardano-node-tests/scripts/destination/dir/stop-cluster-hfc` to stop
[18:50:12] (.env)
```

### Stop the local cluster

To stop the cluster, run the exact command that the last line suggests in the above log:

`<scripts/destination/dir>/stop-cluster-hfc`

### Set the TX_Era

You can even create and send different era transactions (for example the local cluster can be in the Alonzo era and you can create and send Shelley-era transactions).
You can control the transaction era dynamically, while the cluster is running, using the
following environment variable:

```sh
export TX_ERA=mary
```

Available options for `TX_ERA`:

- `shelley`
- `allegra`
- `mary`
- `alonzo`
- `babbage`

You can find the available options in this file:
 `cardano-node-tests/cardano_node_tests/utils/configuration.py`

You can now start the test (in the same shell and from `cardano-node-tests` directory)


## Run tests

The CLI/System tests are located inside `cardano-node-tests/cardano_node_tests/tests`

### Run test suite from file

You can run an entire test suite (tests covering an entire area of functionalities):

```sh
pytest cardano_node_tests/tests/test_native_tokens.py
```

### Run a single test

Open the test file, find the name of the test you would like to run eg `test_minting_unicode_asset_name`

```sh
pytest -k "test_minting_unicode_asset_name"

pytest -k "MyClass and not method"
```

This will run tests that contain names that match the given string expression (case-insensitive),
which can include Python operators that use filenames, class names and function names as variables.
The example above will run TestMyClass.test_something but not TestMyClass.test_method_simple.

For more info on how to invoke tests, see <https://docs.pytest.org/en/6.2.x/usage.html>.


## Interact with the node

Once the local cluster is running, you can interact with its nodes. You can open a new shell and export
`CARDANO_NODE_SOCKET_PATH` with the same value you used before to start the cluster:

`export CARDANO_NODE_SOCKET_PATH=/home/username/Projects/cardano-node/state-cluster0/bft1.socket`

Now you are ready to use the `node-cli`:

- check the current tip and era:

```sh
cardano-cli query tip --testnet-magic 42
{
    "epoch": 1,
    "hash": "252f032be31fd997cdc30b076979490a05edd8fae49ce7e2fdcfd713920d0e7e",
    "slot": 1112,
    "block": 1010,
    "era": "Shelley",
    "syncProgress": "100.00"
}
```

- check the protocol parameters:

```sh
cardano-cli query protocol-parameters --testnet-magic 42
{
    "txFeePerByte": 44,
    "minUTxOValue": 1,
    "stakePoolDeposit": 500000000,
    "utxoCostPerWord": null,
    "decentralization": 0.8,
    "poolRetireMaxEpoch": 18,
    "extraPraosEntropy": null,
    "collateralPercentage": null,
    "stakePoolTargetNum": 10,
    "maxBlockBodySize": 65536,
    "maxTxSize": 16384,
    "treasuryCut": 5.0e-2,
    "minPoolCost": 0,
    "maxCollateralInputs": null,
    "maxValueSize": null,
    "maxBlockExecutionUnits": null,
    "maxBlockHeaderSize": 1100,
    "costModels": {},
    "maxTxExecutionUnits": null,
    "protocolVersion": {
        "minor": 0,
        "major": 2
    },
    "txFeeFixed": 155381,
    "stakeAddressDeposit": 400000,
    "monetaryExpansion": 2.2e-3,
    "poolPledgeInfluence": 0.3,
    "executionUnitPrices": null
}

```


### Transfer funds to your address

During cluster startup, a Byron address will be created and the funds will be moved out of the genesis UTXO into a regular address.

Inside `cardano-node/state-cluster0/byron`

you can find:

- `payment-keys.000.key` - Byron payment signing key
- `address-000` - printed address of a Byron payment signing key (above) where funds from genesis were moved
- `genesis-address-000` - genesis address with funds that will be transferred to a Byron address-000
- `payment-keys.000-converted.skey` - this is a Byron payment signing key (payment-keys.000.key) converted to a corresponding Shelley-format key
- `payment-keys.000-converted.vkey` - this is a verification key that was obtained from signing key `payment-keys.000-converted.skey`
- `address-000-converted` - `address-000` converted to a corresponding Shelley-format key

Let's check the address that holds funds:

```sh
cat /home/artur/Projects/cardano-node/state-cluster0/byron/address-000-converted
2657WMsDfac7Mx1ew6MVfxGqLGwvkkExEFyuWRxDGk4rPQB86uAfrD8BGqjh6ToRj
```

and query it:

```sh
cardano-cli query utxo --address 2657WMsDfac7Mx1ew6MVfxGqLGwvkkExEFyuWRxDGk4rPQB86uAfrD8BGqjh6ToRj --testnet-magic 42
```

| TxHash                                                            | TxIx | Amount                                           |
| ----------------------------------------------------------------- | ---- | ------------                                     |
| 30bfdafa359d0258e54c80de8d00b0556572fef93ec6171ac16572f3994132c7  | 0    | 35996998496920237  lovelace + TxOutDatumHashNone |


You can use `address-000-converted` as a faucet to transfer funds to your manually created addresses. The signing key file that you need to use during the transaction sign process is located here:
`cardano-node/state-cluster0/byron/payment-keys.000-converted.skey`

**Example**:

Build tx:

```sh
cardano-cli transaction build \
--tx-in 30bfdafa359d0258e54c80de8d00b0556572fef93ec6171ac16572f3994132c7#0 \
--tx-out $(cat ~/Projects/payment.addr)+2000000000000 \
--change-address $(cat ~/Projects/cardano-node/state-cluster0/byron/address-000-converted) \
--out-file tx.raw \
--witness-override 2 \
--testnet-magic 42 \
--alonzo-era
```

where `payment.addr` is the Shelley address created manually by the user and `--witness-override 2` is used because of [this issue](https://github.com/input-output-hk/cardano-node/issues/3294).

Sign tx:

```sh
cardano-cli transaction sign \
--tx-body-file tx.raw \
--signing-key-file ~/Projects/cardano-node/state-cluster0/byron/payment-keys.000-converted.skey \
--testnet-magic 42 \
--out-file tx.signed
```

Submit tx:

```sh
cardano-cli transaction submit --tx-file tx.signed  --testnet-magic 42
Transaction successfully submitted.
```


## Debugging with IPython

- If you have not already installed the `ipython` package earlier, then do it in a shell that is not a `nix-shell` and where the virtual environment is activated

```sh
pip install ipython
```

- insert:

```sh
from IPython import embed; embed()
```

on the line above the one where the test code fails or where you want to simply investigate code:

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

- run with `pytest -s ...` and inspect the runtime state once it gives you the `ipython` shell.
Now you can enter variable names in the `IPython` console to check their values/state.

```sh
pytest -s cardano_node_tests/tests/test_native_tokens.py -k "test_minting_unicode_asset_name"
Python 3.8.5 (default, Jan 27 2021, 15:41:15)
Type 'copyright', 'credits' or 'license' for more information
IPython 7.19.0 -- An enhanced Interactive Python. Type '?' for help.
In [1]: token_mint
Out[1]: TokenRecord(token='a7eb2fe41a690735883134ce854128f07da96bed9ac056f5fc68b3301.ᘓऩ⣆nqge', amount=5, issuers_addrs=[AddressRecord(address='addr_test1vzdnfs753u3q08g5ue9t2g7c6hvtpdy5m332rxqxu9lkg3gxtejfd', vkey_file=PosixPath('token_minting_ci0_1.vkey'), skey_file=PosixPath('token_minting_ci0_1.skey'))], token_mint_addr=AddressRecord(address='addr_test1vqmhhujvsrpau0vhpkmjz0xzhxl80u4satu596eqm3zanwcxmle2u', vkey_file=PosixPath('token_minting_ci0_0.vkey'), skey_file=PosixPath('token_minting_ci0_0.skey')), script=PosixPath('test_minting_unicode_asset_name.script'))
```

- leave `IPython` interactive mode by typing `exit();`

When you run tests with:

`pytest ... --log-level=debug`

you can see what Cardano CLI commands are executed.


## Logs for `cardano-node` and `db-sync`

stake pools:

`N=1,2,3`

- `cardano-node/state-cluster0/pool<N>.stdout`
- `cardano-node/state-cluster0/pool<N>.stderr`

BFT node:

- `cardano-node/state-cluster0/bft1.stdout`
- `cardano-node/state-cluster0/bft1.stderr`

db-sync:

- `cardano-node/state-cluster0/dbsync.stdout`
- `cardano-node/state-cluster0/dbsync.stderr`


## Accessing the `db-sync` database

- If you did not change **PGPORT** and **PGUSER**:
`psql -h /path/to/your/data -U postgres -e dbsync0` </br>
Example:
`psql -h /home/artur/Projects/tmp/postgres-qa -U postgres -e dbsync0`

- If you changed **PGPORT** and **PGUSER**:
`psql -h /path/to/your/data -U USER_YOU_USED -e dbsync0 -p PORT_YOU_USED`
for values used in examples above it would be: </br>
`psql -h /home/username/Projects/tmp/postgres-qa -U postgres_dbsync -e dbsync0 -p 5433`

Use `\dt` for listing all tables:

```text
dbsync0=# \dt
```

| Schema  | Name                        | Type  | Owner     |
| ------- | --------------------------- | ----- | --------- |
| public  | ada_pots                    | table | postgres  |
| public  | admin_user                  | table | postgres  |
| public  | block                       | table | postgres  |
| public  | collateral_tx_in            | table | postgres  |
| public  | cost_models                 | table | postgres  |


Use `\d table_name` for checking table details:


```text
dbsync0=# \d collateral_tx_in
```

| Column       | Type    | Collation | Nullable | Default                                       |
| -----------  | ------- | --------- | -------- | --------------------------------------------- |
| id           | bigint  |           | not null | nextval('collateral_tx_in_id_seq'::regclass)  |
| tx_in_id     | bigint  |           | not null |                                               |
| tx_out_id    | bigint  |           | not null |                                               |
| tx_out_index | txindex |           | not null |                                               |

 ```text
Indexes:
    "collateral_tx_in_pkey" PRIMARY KEY, btree (id)
    "unique_col_txin" UNIQUE CONSTRAINT, btree (tx_in_id, tx_out_id, tx_out_index)
Foreign-key constraints:
    "collateral_tx_in_tx_in_id_fkey" FOREIGN KEY (tx_in_id) REFERENCES tx(id) ON UPDATE RESTRICT ON DELETE CASCADE
    "collateral_tx_in_tx_out_id_fkey" FOREIGN KEY (tx_out_id) REFERENCES tx(id) ON UPDATE RESTRICT ON DELETE CASCADE
```

and `\q` for leaving the database.


### Useful queries

For interesting db-sync queries, see <https://github.com/input-output-hk/cardano-db-sync/blob/master/doc/interesting-queries.md>.


## Additional resources

For more info on **cardano-node**, see <https://github.com/input-output-hk/cardano-node/tree/master/doc/getting-started>.

For more info on **cardano-db-sync**, see <https://github.com/input-output-hk/cardano-db-sync/tree/master/doc>.
