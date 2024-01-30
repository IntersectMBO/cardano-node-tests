System Tests Coverage
=====================

The page is updated about once a week.

Latest update: **2024-01-30**  

**Legend:** |Success Badge| |Failure Badge| |Uncovered Badge|  

CLI User Stories
----------------

.. list-table::
   :widths: 11 26 34
   :header-rows: 1

   -

      - Status for Story ID
      - Title
      - Story Overview
   -

      - |image-CLI1|
      - Obtain constitution hash for verification (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI001>`__
      - **As** an Ada holder, **I want** to obtain the hash of the off-chain text of a Constitution, **so that** I can compare it against the hash registered on-chain to verify its authenticity.
   -

      - |image-CLI2|
      - Generate hash of the off-chain constitution (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI002>`__
      - **As** an Ada holder, **I want** to generate the hash of the off-chain text for a proposed Constitution, **so that** the hash can be utilized in a governance action.
   -

      - |image-CLI3|
      - Generate Committee member cold key pair (CCM)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI003>`__
      - **As** a potential Constitutional Committee member, **I want** to generate COLD key pair, **so that** I can be proposed for the Committee in a Governance action.
   -

      - |image-CLI4|
      - Generate committee member hot key pair (CCM)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI004>`__
      - **As** potential Constitutional Committee member, **I want** to generate HOT key pair, **so that** I can authorise the Hot key to sign votes on behalf of the Cold key.
   -

      - |image-CLI5|
      - Authorization certificate (CCM)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI005>`__
      - **As** a Constitutional Committee member, **I want** to issue a authorization certificate from my cold key to a hot key, **so that** I can sign my votes using the hot key and keep the cold key in cold storage and can authorise a new hot key in case the original one is compromised.
   -

      - |image-CLI6|
      - Generate committee member key hash (CCM)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI006>`__
      - **As** a potential Constitutional Committee member, **I want** to generate the key hashes for my cold and hot keys, **so that** they can be used by third parties to propose me as a new Constitutional Committee member and for identification purposes once I’ve been elected as Constitutional Committee member.
   -

      - |image-CLI7|
      - Committee member resignation certificate (CCM)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI007>`__
      - **As** a Constitutional Committee member, **I want** to be able to generate a resignation certificate, **so that** I can submit it to the chain on a transaction to signal to the Ada holders that I’m resigning from my duties as CC member.
   -

      - |image-CLI8|
      - Generate DRep keys (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI008>`__
      - **As** an Ada holder, **I want** to generate Ed25519 keys, **so that** I can register as a DRep.
   -

      - |image-CLI9|
      - Generate DRep ID (DRep)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI009>`__
      - **As** a DRep, **I want** to generate a DRep Id, **so that** Ada holder can use it to delegate their votes to me and my voting record can be tracked.
   -

      - |image-CLI10|
      - DRep Registration Certificate Generation (DRep)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI010>`__
      - **As** a DRep, **I want** to generate a DRep registration certificate, **so that** I can submit it on a transaction and the Ada holders can delegate their votes to me.
   -

      - |image-CLI11|
      - DRep Retirement Certificate Generation (DRep)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI011>`__
      - **As** a DRep, **I want** to generate a DRep retirement (unregistration) certificate, **so that** I can submit it on a transaction and can get my DRep deposit back.
   -

      - |image-CLI12|
      - DRep Metadata Hash Generation (DRep)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI012>`__
      - **As** a DRep, **I want** to generate the hash of my DRep metadata, **so that** I can supply it when registering as DRep.
   -

      - |image-CLI13|
      - Create Update Constitution Governance Action (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI013>`__
      - **As** an Ada holder, **I want** to create a governance action that updates the constitution, **so that** it can be submitted to the chain and be voted by the governance bodies.
   -

      - |image-CLI14|
      - Create Update Constitutional Committee Governance Action (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI014>`__
      - **As** an Ada holder, **I want** to create a governance action that updates the Constitutional Committee, **so that** it can be submitted to the chain and be voted by the governance bodies.
   -

      - |image-CLI15|
      - Create Treasury Withdrawal Governance Action (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI015>`__
      - **As** an Ada holder, **I want** to create a governance action to withdraw funds from the treasury, **so that** it can be submitted to the chain and be voted by the governance bodies.
         Command: ``cardano-cli conway governance action create-treasury-withdrawal``.
   -

      - |image-CLI16|
      - Create info governance action (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI016>`__
      - **As** an Ada holder, **I want** to create an info governance action, **so that** it can be submitted to the chain and be voted by the governance bodies.
        Command: ``cardano-cli conway governance action create-info``.
   -

      - |image-CLI17|
      - Create update protocol parameters governance action (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI017>`__
      - **As** an Ada holder, **I want** to create a governance action to update protocol parameters, **so that** it can be submitted to the chain and be voted by the governance bodies.
        Command: ``cardano-cli conway governance action create-protocol-parameters-update``.
   -

      - |image-CLI18|
      - Create no-confidence governance action (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI018>`__
      - **As** an Ada holder, **I want** to create a no-confidence governance action, **so that** it can be submitted to the chain and be voted by the governance bodies.
        Command: ``cardano-cli conway governance action create-no-confidence``.
   -

      - |image-CLI19|
      - Create Hard-fork initiation governance action (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI019>`__
      - **As** an Ada holder, **I want** to create a governance action to initiate a hardfork, **so that** it can be submitted to the chain and be voted by the governance bodies.
        Command: ``cardano-cli conway governance action create-hf-init``.
   -

      - |image-CLI20|
      - View governance action file (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI020>`__
      - **As** an Ada holder, **I want** to inspect the contents of a governance action file, **so that** I can verify it is correct before submitting it in a transaction.
        Command: ``cardano-cli conway governance action view``.
   -

      - |image-CLI21|
      - Create a governance action vote (DRep/SPO/CCM)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI021>`__
      - **As** a DRep, SPO or CC member, **I want** to create a vote for a governance action, **so that** I can include it in a transaction and submit it to the chain.
        Command: ``cardano-cli conway governance vote create``.
   -

      - |image-CLI22|
      - View vote file (DRep/SPO/CCM)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI022>`__
      - **As** a DRep, SPO or CC member, **I want** to inspect the contents of a vote file, **so that** I can verify it is correct before submitting it in a transaction.
        Command: ``cardano-cli conway governance vote view``.
   -

      - |image-CLI23|
      - Build a transaction with to submit proposal (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI023>`__
      - **As** an Ada holder, **I want** to build a transaction that includes a proposal (containing a governance action), **so that** I can later sign and submit to the chain.
        Command: ``transaction build``.
   -

      - |image-CLI24|
      - Build transaction for proposal vote (DRep, SPO, CCM)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI024>`__
      - **As** a DRep, SPO or CC member, **I want** to build a transaction that includes my vote on a particular governance action, **so that** I can later sign and submit to the chain.
         Command: ``transaction build``.
   -

      - |image-CLI25|
      - Build RAW transaction for proposal vote (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI025>`__
      - **As** an Ada holder, **I want** to build a transaction that includes a proposal (containing a governance action), **so that** I can later sign and submit to the chain.
        Command: ``transaction build-raw``.
   -

      - |image-CLI26|
      - Build RAW transaction for proposal vote (DRep/SPO/CCM)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI026>`__
      - **As** a DRep, SPO or CC member, **I want** to build a transaction that includes my vote on a particular governance action, **so that** I can later sign and submit to the chain.
         Command: ``transaction build-raw``.
   -

      - |image-CLI27|
      - Create stake registration certificate (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI027>`__
      - **As** an Ada holder, **I want** to create a conway cddl-compliant stake registration certificate.
   -

      - |image-CLI28|
      - Create stake deregistration certificate (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI028>`__
      - **As** an Ada holder, **I want** to create a conway cddl-compliant stake deregistration certificate to get my deposit back.
   -

      - |image-CLI29|
      - Delegate vote to DRep (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI029>`__
      - **As** an Ada holder, **I want** to delegate my votes to a DRep (registered or default), **so that** my stake is counted when the DRep votes.
   -

      - |image-CLI30|
      - Delegate stake to SPO and votes to DRep with a single certificate (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI030>`__
      - **As** an Ada holder, **I want** to delegate my stake to a stake pool AND my votes to a DRep (registered or default) with a single certificate.
   -

      - |image-CLI31|
      - Query governance state (ANY)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI031>`__
      - **As** any persona, **I want** to query the nodes for the currentGovernance state, **so that** I can inform my decisions.
   -

      - |image-CLI32|
      - Query committee state (CCM)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI032>`__
      - **As** a CC member, **I want** to query the Constitutional Committee state, **so that** I can find my expiration term and whether my hot key authorization certificate has been recorded on chain.
   -

      - |image-CLI33|
      - Query DRep state (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI033>`__
      - **As** an Ada holder, **I want** to query the DRep state, **so that** I can find detailed information about registered DReps.
   -

      - |image-CLI34|
      - Query DRep stake distribution (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI034>`__
      - **As** an Ada holder and DRep, **I want** to query the DRep stake distribution, **so that** I can find the weight (of the votes) of each DRep.
   -

      - |image-CLI35|
      - Expand query stake-address-info to show deposits and vote delegation (HOLDER)
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI035>`__
      - **As** an Ada holder, **I want** to query my stake address information, **so that** I can learn to which pool and DRep I’m delegating to and the value in lovelace of my deposits for delegating and for submitting governance actions.
   -

      - |image-CLI36|
      - Register script based DReps.
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI036>`__
      -
   -

      - |image-CLI37|
      - Unregister script based DReps.
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI037>`__
      -
   -

      - |image-CLI38|
      - Script based CC GA. ``--add`` ``--remove``.
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md#CLI038>`__
      -

CIP1694 User Stories
--------------------

.. list-table::
   :widths: 11 26 34
   :header-rows: 1

   -

      - Status for Story ID
      - Title
      - Story Overview
   -

      - |image-CIP1|
      - Hash value of the off-chain Constitution is recorded on-chain
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP001>`__
      - **As** an Ada holder, **I want** the ledger state to record the hash of the current constitution, **so that** I can verify the authenticity of the off-chain document.
   -

      - |image-CIP2|
      - Node records Committee member key hashes, terms and status
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP002>`__
      - **As** an Ada holder, **I want** the key hash of active and expired Committee Members and their terms to be registered on-chain, **so that** the system can count their votes.
   -

      - |image-CIP3|
      - Authorization Certificate
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP003>`__
      - **As** a Committee Member, **I want** to submit a Cold to Hot key Authorization certificate, **so that** I can sign my votes using the hot key and keep my cold keys safely in cold storage.
   -

      - |image-CIP4|
      - Record cold credentials and authorization certificates on chain
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP004>`__
      - **As** a committee member, **I want** node’s ledger state to accurately maintain the record of key-hashes, terms, and cold to hot key authorization maps for active and expired members, **so that** only votes from active Committee members are considered.
   -

      - |image-CIP5|
      - Replacing the constitutional committee via a governance action
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP005>`__
      - **As** an Ada holder, **I want** to be able to submit a governance action to replace all or part of the current constitutional committee, **so that** committee members that have lost confidence of Ada holders can be removed from their duties.
   -

      - |image-CIP6|
      - Size of the constitutional committee
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP006>`__
      - **As** an Ada holder, **I want** the size of the constitutional committee to be adjustable (a protocol parameter), **so that** I can propose a different size via a governance action.
   -

      - |image-CIP7|
      - Committee voting threshold (quorum) can be modified
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP007>`__
      - **As** an Ada holder, **I want** that the committee threshold (the fraction of committee required to ratify a gov action) is not fixed, **so that** I can propose a different threshold via a governance action.
   -

      - |image-CIP8|
      - Electing an empty committee
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP008>`__
      - **As** an Ada holder, **I want** to be able to elect an empty committee if the community wishes to abolish the constitutional committee entirely, **so that** governance actions don’t need the votes of a constitutional committee to be ratified.
   -

      - |image-CIP9|
      - Constitutional committee members have a limited term
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP009>`__
      - **As** an Ada holder, **I want** each committee member to have an individual term, **so that** the system can have a rotation scheme.
   -

      - |image-CIP10|
      - Tracking committee member expirations
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP010>`__
      - **As** an Ada holder, **I want** the system to keep track of the expiration epoch of each committee member, **so that** the information is publicly available in the ledger and can be consumed by anyone interested.
   -

      - |image-CIP11|
      - Automatically expire committee members that have completed their
         terms
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP011>`__
      - **As** an Ada holder, **I want** the system to automatically expire committee members that have reached their term, **so that** only active committee members can vote.
   -

      - |image-CIP12|
      - Resign as committee member
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP012>`__
      - **As** a committee member, **I want** to be able to resign my responsibilities, **so that** I can stop my responsibilities with the Cardano Community while minimizing the effects on the system.
   -

      - |image-CIP13|
      - State of no-confidence
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP013>`__
      - **As** an Ada holder, **I want** to submit a governance action to depose the current constitutional committee and put the system in a no-confidence-state, **so that** the community must elect a new constitutional committee.
   -

      - |image-CIP14|
      - Automatically enter a state of no-confidence
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP014>`__
      - **As** an Ada holder, **I want** the system to automatically enter a state of no-confidence when the number of non-expired committee members falls below the minimal size of the committee, **so that** only update-committee governance actions can be ratified.
   -

      - |image-CIP15|
      - Proposal policy
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP015>`__
      - **As** an Ada holder, **I want** a supplementary script to the constitution, **so that** some proposal types are automatically restricted.
   -

      - |image-CIP16|
      - Delegate representatives
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP016>`__
      - **As** an Ada holder, **I want** stake credentials to delegate voting rights to a registered delegate representative (DRep), **so that** I can participate in the governance of the system.
   -

      - |image-CIP17|
      - Delegate to always abstain
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP017>`__
      - **As** an Ada holder or an exchange, **I want** to delegate my stake to the predefined option ‘Abstain’, **so that** my stake is marked as not participating in governance.
   -

      - |image-CIP18|
      - Delegate to no-confidence
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP018>`__
      - **As** an Ada holder, **I want** to delegate my stake to the predefined DRep ‘No Confidence’, **so that** my stake is counted as a ‘Yes’ vote on every ‘No Confidence’ action and a ‘No’ vote on every other action.
   -

      - |image-CIP19|
      - Inactive DReps
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP019>`__
      - **As** an Ada holder, **I want** DReps to be considered inactive if they don’t vote for ``drepActivity``-many epochs, **so that** their delegated stake does not count towards the active voting stake, this to avoid leaving the system in a state where no governance action can pass.
   -

      - |image-CIP20|
      - DRep credentials
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP020>`__
      - **As** a DRep, **I want** to be identified by a credential (A verification key (Ed2559) or a Native or Plutus Script), **so that** I can register and vote on governance actions.
   -

      - |image-CIP21|
      - DRep registration certificate
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP021>`__
      - **As** a DRep, **I want** to generate a registration certificate, **so that** the system recognizes my credentials and counts my votes on governance actions.
   -

      - |image-CIP22|
      - Vote delegation certificate
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP022>`__
      - **As** an Ada holder, **I want** to generate a vote delegation certificate, **so that** I can delegate my voting rights.
   -

      - |image-CIP23|
      - DRep retirement certificate
         `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md#CIP023>`__
      - **As** a DRep, **I want** to generate a retirement certificate, **so that** the system and Ada holders (delegators) know that I’m no longer voting on governance actions and they should redelegate.

.. |Success Badge| image:: https://img.shields.io/badge/success-green
.. |Failure Badge| image:: https://img.shields.io/badge/failure-red
.. |Uncovered Badge| image:: https://img.shields.io/badge/uncovered-grey
.. |image-CLI1| image:: https://img.shields.io/badge/CLI001-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L416
.. |image-CLI2| image:: https://img.shields.io/badge/CLI002-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L217
.. |image-CLI3| image:: https://img.shields.io/badge/CLI003-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_committee.py#L119
.. |image-CLI4| image:: https://img.shields.io/badge/CLI004-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_committee.py#L119
.. |image-CLI5| image:: https://img.shields.io/badge/CLI005-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_committee.py#L119
.. |image-CLI6| image:: https://img.shields.io/badge/CLI006-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_committee.py#L119
.. |image-CLI7| image:: https://img.shields.io/badge/CLI007-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_committee.py#L162
.. |image-CLI8| image:: https://img.shields.io/badge/CLI008-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L264
.. |image-CLI9| image:: https://img.shields.io/badge/CLI009-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L264
.. |image-CLI10| image:: https://img.shields.io/badge/CLI010-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L264
.. |image-CLI11| image:: https://img.shields.io/badge/CLI011-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L316
.. |image-CLI12| image:: https://img.shields.io/badge/CLI012-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L259
.. |image-CLI13| image:: https://img.shields.io/badge/CLI013-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L228
.. |image-CLI14| image:: https://img.shields.io/badge/CLI014-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L585
.. |image-CLI15| image:: https://img.shields.io/badge/CLI015-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L1433
.. |image-CLI16| image:: https://img.shields.io/badge/CLI016-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L1990
.. |image-CLI17| image:: https://img.shields.io/badge/CLI017-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L1166
.. |image-CLI18| image:: https://img.shields.io/badge/CLI018-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L1726
.. |image-CLI19| image:: https://img.shields.io/badge/CLI019-grey
   :target: https://github.com/CLI019-404
.. |image-CLI20| image:: https://img.shields.io/badge/CLI020-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L437
.. |image-CLI21| image:: https://img.shields.io/badge/CLI021-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L2057
.. |image-CLI22| image:: https://img.shields.io/badge/CLI022-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L2143
.. |image-CLI23| image:: https://img.shields.io/badge/CLI023-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L2010
.. |image-CLI24| image:: https://img.shields.io/badge/CLI024-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L2109
.. |image-CLI25| image:: https://img.shields.io/badge/CLI025-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L2229
.. |image-CLI26| image:: https://img.shields.io/badge/CLI026-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L2329
.. |image-CLI27| image:: https://img.shields.io/badge/CLI027-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L554
.. |image-CLI28| image:: https://img.shields.io/badge/CLI028-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L602
.. |image-CLI29| image:: https://img.shields.io/badge/CLI029-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L563
.. |image-CLI30| image:: https://img.shields.io/badge/CLI030-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L751
.. |image-CLI31| image:: https://img.shields.io/badge/CLI031-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L2031
.. |image-CLI32| image:: https://img.shields.io/badge/CLI032-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_committee.py#L148
.. |image-CLI33| image:: https://img.shields.io/badge/CLI033-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L297
.. |image-CLI34| image:: https://img.shields.io/badge/CLI034-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L675
.. |image-CLI35| image:: https://img.shields.io/badge/CLI035-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L642
.. |image-CLI36| image:: https://img.shields.io/badge/CLI036-grey
   :target: https://github.com/CLI036-404
.. |image-CLI37| image:: https://img.shields.io/badge/CLI037-grey
   :target: https://github.com/CLI037-404
.. |image-CLI38| image:: https://img.shields.io/badge/CLI038-grey
   :target: https://github.com/CLI038-404
.. |image-CIP1| image:: https://img.shields.io/badge/CIP001-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L416
.. |image-CIP2| image:: https://img.shields.io/badge/CIP002-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_committee.py#L148
.. |image-CIP3| image:: https://img.shields.io/badge/CIP003-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_committee.py#L119
.. |image-CIP4| image:: https://img.shields.io/badge/CIP004-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_committee.py#L148
.. |image-CIP5| image:: https://img.shields.io/badge/CIP005-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L660
.. |image-CIP6| image:: https://img.shields.io/badge/CIP006-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L1166
.. |image-CIP7| image:: https://img.shields.io/badge/CIP007-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_committee.py#L251
.. |image-CIP8| image:: https://img.shields.io/badge/CIP008-grey
   :target: https://github.com/CIP008-404
.. |image-CIP9| image:: https://img.shields.io/badge/CIP009-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L952
.. |image-CIP10| image:: https://img.shields.io/badge/CIP010-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L952
.. |image-CIP11| image:: https://img.shields.io/badge/CIP011-grey
   :target: https://github.com/CIP011-404
.. |image-CIP12| image:: https://img.shields.io/badge/CIP012-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_committee.py#L162
.. |image-CIP13| image:: https://img.shields.io/badge/CIP013-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_voting.py#L1726
.. |image-CIP14| image:: https://img.shields.io/badge/CIP014-grey
   :target: https://github.com/CIP014-404
.. |image-CIP15| image:: https://img.shields.io/badge/CIP015-grey
   :target: https://github.com/CIP015-404
.. |image-CIP16| image:: https://img.shields.io/badge/CIP016-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L551
.. |image-CIP17| image:: https://img.shields.io/badge/CIP017-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L551
.. |image-CIP18| image:: https://img.shields.io/badge/CIP018-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L551
.. |image-CIP19| image:: https://img.shields.io/badge/CIP019-grey
   :target: https://github.com/CIP019-404
.. |image-CIP20| image:: https://img.shields.io/badge/CIP020-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L669
.. |image-CIP21| image:: https://img.shields.io/badge/CIP021-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L264
.. |image-CIP22| image:: https://img.shields.io/badge/CIP022-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L563
.. |image-CIP23| image:: https://img.shields.io/badge/CIP023-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/7f8be0b70dc78b28ddf8d98aa7906b2a1687d4eb/cardano_node_tests/tests/tests_conway/test_drep.py#L316
