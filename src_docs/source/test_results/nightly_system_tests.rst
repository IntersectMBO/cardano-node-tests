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

* `nightly <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly/>`__:  |nightly-badge|
   * network in Babbage era
   * Babbage transaction era
   * default (legacy) network topology
* `nightly-dbsync <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-dbsync/>`__:  |nightly-dbsync-badge|
   * network in Babbage era
   * Babbage transaction era
   * default (legacy) network topology
   * DB Sync testing enabled
* `nightly-p2p <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-p2p/>`__:  |nightly-p2p-badge|
   * network in Babbage era
   * Babbage transaction era
   * P2P network topology
* `nightly-alonzo-tx <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-alonzo-tx/>`__:  |nightly-alonzo-tx-badge|
   * network in Babbage era
   * Alonzo transaction era
   * default (legacy) network topology
   * skip long-running tests
* `nightly-mary-tx <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-mary-tx/>`__:  |nightly-mary-tx-badge|
   * network in Babbage era
   * Mary transaction era
   * default (legacy) network topology
   * skip long-running tests
* `nightly-shelley-tx <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-shelley-tx/>`__:  |nightly-shelley-tx-badge|
   * network in Babbage era
   * Shelley transaction era
   * default (legacy) network topology
   * skip long-running tests


.. |nightly-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly%2Fbadge.json
   :target: https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly/

.. |nightly-dbsync-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly-dbsync%2Fbadge.json
   :target: https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-dbsync/

.. |nightly-p2p-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly-p2p%2Fbadge.json

.. |nightly-alonzo-tx-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly-alonzo-tx%2Fbadge.json
   :target: https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-alonzo-tx/

.. |nightly-mary-tx-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly-mary-tx%2Fbadge.json
   :target: https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-mary-tx/

.. |nightly-shelley-tx-badge| image:: https://img.shields.io/endpoint?url=https%3A%2F%2Fcardano-tests-reports-3-74-115-22.nip.io%2Fcardano-node-tests-nightly-shelley-tx%2Fbadge.json
   :target: https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-shelley-tx/
