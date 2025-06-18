Testnet Bootstrap
=================

Configuration
-------------

* Rename the directory to eg. `preview_bootstrap`
* Go to <https://book.world.dev.cardano.org/environments.html>
  and download genesis and configuration files
* Replace the empty placeholder files with the downloaded files, so the file names are preserved
* Make sure the `config-relay1.json` points to correct names of the genesis files (the file names differ from the downloaded ones)

Nix shell
---------

The assumption is you are running all the following commands in the DevOps nix-shell.

Faucet
------

* If you already have an address on the testnet, create a `shelley/faucet.addr` file with the address,
  and `shelley/faucet.vkey` and `shelley/faucet.skey` with the corresponding keys
* OR run the `faucet_setup.sh` script. Export `TESTNET_NAME` and optionally `APIKEY`

Running the node
----------------

* Run the `run_relay1.sh` script
* Wait until the node is synced (check in another terminal window)

Running the db-sync
--------------------

* Set the `CARDANO_NODE_SOCKET_PATH`: `export CARDANO_NODE_SOCKET_PATH=$PWD/relay1.socket`
* When you don't have db and snapshot for the given testnet available, start & setup postgres with clean db

    ```sh
    /path/to/cardano-node-tests-repo/scripts/postgres-start.sh ~/tmp/postgres-for-testnet/ -k
    ./postgres-setup.sh
    ```

* When you already have db and snapshot for the given testnet available, start postgres with correct data

    ```sh
    /path/to/cardano-node-tests-repo/scripts/postgres-start.sh ~/tmp/postgres-for-testnet/
    ```

* Start db-sync ONLY AFTER the node is fully synced:

    ```sh
    ./run-cardano-dbsync
    ```

* Wait until the db-sync is fully synced

Running tests
-------------

Once the node and optionally db-sync are fully synced, you can stop them and start the tests.
Note that the testing framework will start node and db-sync processes automatically. It will use the synced states of both node and db-sync if available in the bootstrap directory.

* open another terminal
* cd to `cardano-node-tests` repository
* run the tests

    ```sh
    NODE_REV=10.4.1 BOOTSTRAP_DIR=~/path/to/preview_bootstrap ./.github/regression.sh
    ```
