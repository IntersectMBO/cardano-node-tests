# Types and levels of tests

## Node system tests

### Node nightly CLI tests

- All the CLI end-to-end tests are run automatically on a nightly basis using different feature combinations on the latest `node master` branch
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)

### Node CLI tests (on demand)

- These are functional end-to-end CLI tests
- These tests are run on demand (eg, per tag) using a local cluster
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)

### Node sync tests

- The scope of these tests is to check the synchronization speed, RAM, CPU, and disk usage from end-users' perspective
- These tests are run on Windows, macOS, and Linux (tests on mainnet are run on Linux only)
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)

## DB Sync system tests

### DB Sync nightly tests

- All the CLI end-to-end tests are run automatically on a nightly basis using the latest `node master` and `db-sync master` branches
- All the actions are executed using the node CLI and the checks are done using both the node CLI and DB Sync
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)

### DB Sync feature tests (on demand)

- These are functional end-to-end CLI tests
- These tests are run on demand (eg, per tag) using different environments – shelley_qa, pre-production, preview, mainnet
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)

### DB Sync sync tests

- The scope of these tests is to check the synchronization speed, RAM, CPU, and disk usage from end-users' perspective
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)

## Exploratory testing

Developers and test engineers from different areas execute exploratory testing, which is finalized through user acceptance testing involving the entire community.

Having a lot of automated tests provides a good level of confidence, but it will never guarantee 100% correctness. It is impossible to automate everything – the more complex the software is, the more obscure ways users will find to take advantage of it.

Exploratory tests are often underestimated or misunderstood, but they provide good results. It is common that full requirements aren't in place from the beginning, and even if they are, they might change. Therefore, it’s good to get involved as soon as possible.
