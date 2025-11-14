Testnet Bootstrap
=================

Configuration
-------------

* Copy and rename this directory, for example to `preview_bootstrap`.
* Visit <https://book.world.dev.cardano.org/environments.html>
  and download the genesis and configuration files.
* Replace the empty placeholder files in this directory with the downloaded files, preserving the original file names.
* Ensure that `config-relay1.json` references the correct genesis file names (these typically differ from the downloaded names).

Nix Shell
---------

All commands below assume you are running inside the DevOps Nix shell.

Faucet
------

* If you already have a testnet address, create `shelley/faucet.addr` with the address,
  and add `shelley/faucet.vkey` and `shelley/faucet.skey` with the corresponding keys.
* **Or** run the `faucet_setup.sh` script. Export `TESTNET_NAME` and optionally `APIKEY` before running it.

Running the Node
----------------

* Run the `run_relay1.sh` script.
* Wait until the node is fully synced (you can monitor progress from another terminal).

Running db-sync
---------------

* Set the `CARDANO_NODE_SOCKET_PATH`:

    ```sh
    export CARDANO_NODE_SOCKET_PATH=$PWD/relay1.socket
    ```

* If you **do not** already have a database and snapshot for the given testnet, start Postgres with a clean database:

    ```sh
    /path/to/cardano-node-tests-repo/scripts/postgres-start.sh ~/tmp/postgres-for-testnet/ -k
    ./postgres-setup.sh
    ```

* If you **do** already have a database and snapshot, start Postgres using the existing data:

    ```sh
    /path/to/cardano-node-tests-repo/scripts/postgres-start.sh ~/tmp/postgres-for-testnet/
    ```

* Start db-sync **only after the node is fully synced**:

    ```sh
    ./run_dbsync.sh
    ```

* Wait until db-sync has fully synced.

Running Tests
-------------

Once the node and (optionally) db-sync are fully synced, you can stop them and run the tests.
The testing framework will start fresh node and db-sync processes automatically, and will reuse
the synced data from the bootstrap directory.

* Open another terminal.
* Change to the `cardano-node-tests` repository.
* Run the tests:

    ```sh
    NODE_REV=10.5.1 BOOTSTRAP_DIR=~/path/to/preview_bootstrap ./.github/regression.sh
    ```
