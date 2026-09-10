PlutusV3 System Tests Coverage
==============================

Last updated **2024-01-30** for **cardano-node 8.7.2**  

**Legend:** |Success Badge| |Failure Badge| |Uncovered Badge|  

Smart Contract User Stories
---------------------------

.. list-table::
   :widths: 8 26 37
   :header-rows: 1

   -

      - Status for Story ID
      - Title
      - Story Overview
   -

      - |CIP-85-badge| PlutusV3 is not supported in this build so cannot use plutus-tx compiler v1.1.0
      - Sums-of-products in Plutus v3
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/04-smart-contracts.md#user-story-id-cip-85>`__
      - As a DApp developer I want to use sums-of-products instead of Scott-encoding in my Plutus scripts to get better performance.
   -

      - |CIP-101-badge| Plutus version in use doesn’t include this builtin
      - Keccak256 in Plutus v3
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/04-smart-contracts.md#user-story-id-cip-101>`__
      - As a DApp developer I want to use the Keccak hashing function to validate ECDSA signatures formatted via the EVM standard.
   -

      - |PLT-001-badge|\  Plutus version in use doesn’t include this builtin
      - Blake2b-224 in Plutus v3
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/04-smart-contracts.md#user-story-id-plt001>`__
      - As a DApp developer I want to use the Blake2b-224 hashing function to compute PubKeyHash onchain.
   -

      - |CIP-87-badge| Plutus version in use doesn’t include this builtin
      - Use bitwise operations in Plutus V3
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/04-smart-contracts.md#user-story-id-cip-87>`__
      - As a DApp developer I want to use bitwise operations so that I can work with data bytestrings in a more granular and optimized way and perform operations at the bit level.

.. |Success Badge| image:: https://img.shields.io/badge/success-green
.. |Failure Badge| image:: https://img.shields.io/badge/failure-red
.. |Uncovered Badge| image:: https://img.shields.io/badge/uncovered-grey

.. |CIP-85-badge| image:: https://img.shields.io/badge/CIP-85-grey
   :target: https://github.com/input-output-hk/antaeus/blob/cardano-node_8-7-2/e2e-tests/test/Spec.hs#L180-L203
.. |CIP-101-badge| image:: https://img.shields.io/badge/CIP-101-grey
   :target: https://github.com/input-output-hk/antaeus/pull/43
.. |PLT-001-badge| image:: https://img.shields.io/badge/PLT-001-grey
   :target: https://github.com/input-output-hk/antaeus/pull/43
.. |CIP-87-badge| image:: https://img.shields.io/badge/CIP-87-grey
   :target: https://github.com/input-output-hk/antaeus/pull/79
