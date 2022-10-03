Nightly System Tests Pipelines
==============================

Cardano node nightly system tests are run on a nightly basis. The tests are run on multiple instances of local cluster. The tests are run on the `master` branch of the `cardano-node` repository.

There are several pipelines that run the nightly system tests. In each pipeline, different sets of tests are run. The tests that are not suitable for given pipeline are reported as skipped in testing report.


Nightly Results
---------------

* `nightly <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly/>`__
   * network in Babbage era
   * Babbage transaction era
   * default (legacy) network topology
* `nightly-dbsync <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-dbsync/>`__
   * network in Babbage era
   * Babbage transaction era
   * default (legacy) network topology
   * DB Sync testing enabled
* `nightly-alonzo-tx-on-babbage <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-alonzo-tx-on-babbage/>`__
   * network in Babbage era
   * Alonzo transaction era
   * default (legacy) network topology
* `nightly-mary-tx <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-mary-tx/>`__
   * network in Babbage era
   * Mary transaction era
   * default (legacy) network topology
* `nightly-shelley-tx <https://cardano-tests-reports-3-74-115-22.nip.io/cardano-node-tests-nightly-shelley-tx/>`__
   * network in Babbage era
   * Shelley transaction era
   * default (legacy) network topology
