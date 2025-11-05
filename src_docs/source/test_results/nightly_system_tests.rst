Nightly system tests pipelines
==============================

These Cardano node system tests are run on a nightly basis. The tests are run on multiple instances of a local cluster.
The tests are run on the `master` branch of the `cardano-node` repository.

Several pipelines run the nightly system tests. In each pipeline, different sets of tests are run.


Test statuses
-------------

* **failed** - tests that failed assertion or that were aborted because of unhandled exception
* **broken** - tests that are affected by a real known issue; these are marked as `xfailed` (expected failure) until the issue is fixed
* **skipped** - tests that are not meant to run in the given pipeline


Nightly results
---------------

`Results on GitHub Actions <https://github.com/IntersectMBO/cardano-node-tests/actions?query=workflow%3A%22Nightly+tests%22+event%3Aschedule+branch%3Amaster++>`__

* `nightly <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly/>`__:  |nightly-badge|
   * network in Conway era
   * protocol version 10
   * P2P network topology
   * Constitutional Commitee has 5 members
   * cluster starts directly in Conway era
* `nightly-dbsync <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-dbsync/>`__:  |nightly-dbsync-badge|
   * network in Conway era
   * protocol version 10
   * P2P network topology
   * Constitutional Commitee has 5 members
   * cluster starts directly in Conway era
   * DB Sync testing enabled
* `nightly-cli <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-cli/>`__:  |nightly-cli-badge|
   * latest `cardano-cli` master
   * network in Conway era
   * protocol version 10
   * P2P network topology
   * Constitutional Commitee has 5 members
   * cluster starts directly in Conway era

Nightly upgrade testing
^^^^^^^^^^^^^^^^^^^^^^^

* `Step 1 <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-upgrade/step1/>`__:  |nightly-upgrade-step1-badge|
   * use the `latest cardano-node release <https://github.com/IntersectMBO/cardano-node-tests/blob/master/.github/env_nightly_upgrade>`__ for Mainnet
   * network in Conway era
   * protocol version 10
   * Constitutional Commitee has 5 members
   * legacy network topology
   * smoke tests
   * governance info action test
* `Step 2 <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-upgrade/step2/>`__:  |nightly-upgrade-step2-badge|
   * upgrade all nodes except one to the latest cardano-node master
   * mixed network topology (half nodes P2P, half nodes legacy topology)
   * smoke tests
* `Step 3 <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-upgrade/step3/>`__:  |nightly-upgrade-step3-badge|
   * upgrade the last remaining node to latest cardano-node master
   * P2P network topology
   * smoke tests
   * governance treasury withdrawal action test

.. |nightly-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly%2Fbadge.json
   :target: https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly/

.. |nightly-dbsync-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly-dbsync%2Fbadge.json
   :target: https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-dbsync/

.. |nightly-cli-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly-cli%2Fbadge.json
   :target: https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-cli/

.. |nightly-upgrade-step1-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly-upgrade%2Fstep1%2Fbadge.json
   :target: https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-upgrade/step1/

.. |nightly-upgrade-step2-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly-upgrade%2Fstep2%2Fbadge.json
   :target: https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-upgrade/step2/

.. |nightly-upgrade-step3-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly-upgrade%2Fstep3%2Fbadge.json
   :target: https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-upgrade/step3/
