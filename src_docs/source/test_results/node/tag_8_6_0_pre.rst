8.6.0-pre
=========

* tag link - <https://github.com/IntersectMBO/cardano-node/releases/tag/8.6.0-pre>
* tag PR -
* tag commits - <https://github.com/IntersectMBO/cardano-node/compare/8.1.2...8.6.0-pre>


Changelogs
----------

* cardano-api: <https://github.com/IntersectMBO/cardano-node/blob/8.6.0-pre/cardano-api/ChangeLog.md>
* cardano-cli: <https://github.com/IntersectMBO/cardano-node/blob/8.6.0-pre/cardano-cli/ChangeLog.md>
* cardano-node: <https://github.com/IntersectMBO/cardano-node/blob/8.6.0-pre/cardano-node/ChangeLog.md>
* submit-api: <https://github.com/IntersectMBO/cardano-node/blob/8.6.0-pre/cardano-submit-api/CHANGELOG.md>


Regression testing on a local cluster
-------------------------------------

.. list-table:: Regression Testsuite
   :widths: 64 7
   :header-rows: 0

   * - P2P ON - Babbage with default (Babbage) TX
     - |:heavy_check_mark:|
   * - P2P OFF - Babbage with default (Babbage) TX
     - |:heavy_check_mark:|
   * - Mix P2P and Legacy - Babbage with default (Babbage) TX
     - |:heavy_check_mark:|
   * - Plutus Functionality using `Antaeus <https://github.com/input-output-hk/antaeus/tree/cardano-node_8-6-0>`__ - `Multi-era (without V3 scripts) <https://cardano-tests-reports-3-74-115-22.nip.io/antaeus/8.6.0/>`__
     - |:heavy_check_mark:|

.. list-table:: Other Testing
   :widths: 64 7
   :header-rows: 0

   * - Upgrade testing (8.1.2 to 8.6.0-pre)
     - |:heavy_check_mark:|
   * - Rollback testing
     - |:heavy_check_mark:|
   * - Block production testing on network with 10 pools, 5 of them P2P, 5 of them Legacy - `results (sqlite db) <https://cardano-tests-reports-3-74-115-22.nip.io/data/block_production_10pools.db>`__
     - |:heavy_check_mark:|
   * - Sanity checks of the submit-api REST service
     - |:heavy_check_mark:|


Release testing checklist
-------------------------

.. list-table::
   :widths: 64 7
   :header-rows: 0

   * - `8.6.0-pre` pushed to `shelley-qa`
     - |:hourglass_flowing_sand:|
   * - `8.6.0-pre` pushed to `preview`
     - |:hourglass_flowing_sand:|
   * - Grafana metrics OK
     - |:hourglass_flowing_sand:|
   * - Regression testing against `shelley-qa`
     - |:hourglass_flowing_sand:|
   * - Regression testing against `preview`
     - |:hourglass_flowing_sand:|
   * - `Sync testing ran against shelley_qa & Preview & Preprod & Mainnet (Windows, Linux, macOS) <https://tests.cardano.intersectmbo.org/test_results/sync_tests.html>`__
     - |:heavy_check_mark:|
   * - DB re-validation testing (ledger snapshots compatibility)
     - |:heavy_check_mark:|
   * - Backward compatibility testing (Node with version N-1)
     - |:heavy_check_mark:|
   * - Check build instructions changes
     - |:hourglass_flowing_sand:|
   * - Benchmarking - `Report <https://input-output-rnd.slack.com/files/U03A639T0DN/F065PQ06XRN/8.6.0_8.1.2_8.5.0_8.6.0.value-only.pdf>`__
     - |:heavy_check_mark:|
   * - Sanity check release notes
     - |:hourglass_flowing_sand:|


New functionalities in this tag
-------------------------------


Known issues
------------

`Up-to-date list of existing issues <https://github.com/IntersectMBO/cardano-node/issues?q=label%3A8.0.0+is%3Aopen>`__


New issues
----------

`Cannot delegate Plutus stake address <https://github.com/IntersectMBO/cardano-cli/issues/297>`__


Breaking changes
----------------
