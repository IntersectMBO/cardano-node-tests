# Types and levels of tests

## Node System tests

### Node Nightly CLI tests

- All the CLI end-to-end tests are run automatically, on a nightly basis, on different feature combinations, using the latest `node master` branch
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)

### Node CLI tests (on demand)

- These are functional end-to-end CLI tests
- These tests are run on-demand (per tag, etc), using a Local Cluster
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)

### Node Sync tests

- The scope of these tests is to check the sync speed, RAM, CPU, and disk usage from the end-users' perspective.
- These tests are run on Windows, macOS and Linux (tests on Mainnet are run only on Linux)
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)


## DB-Sync System tests

### DB-Sync Nightly tests

- All the CLI end-to-end tests are run automatically, on a nightly basis, using the latest `node master` and `db-sync master` branches
- All the actions are executed using the node cli and the checks are done both using node cli and db-sync
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)

### DB-Sync feature tests (on demand)

- These are functional end-to-end CLI tests
- These tests are run on-demand (per tag, etc), using different environments - shelley_qa, preprod, preview, mainnet
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)

### DB-Sync Sync tests

- The scope of these tests is to check the sync speed, RAM, CPU, and disk usage from the end-users' perspective.
- The results of the latest runs can be found [here](https://input-output-hk.github.io/cardano-node-tests/test_results.html)


## Exploratory testing

Having a lot of automated tests gives good level of confidence, but it will never be 100%.
You cannot automate everything. The more complex your software is the more obscure ways users
will find to take advantage of it.

Exploratory tests are often underestimated or misunderstood, but they are a real deal.
Reality is that we often don’t have the full requirements, and even if we have they might change.
One way or another it’s good to be involved as soon as possible.

The exploratory testing is executed by developers and test engineers from different areas and is
finalized through the User Acceptance Testing that involves the entire community.
