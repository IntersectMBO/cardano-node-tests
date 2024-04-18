System Tests Coverage
=====================

The page is updated every Tuesday and Friday until the coverage is complete.

Latest update: **2024-04-16**  

**Legend:** |Success Badge| |Failure Badge| |Partial Coverage Badge| |Uncovered Badge|  

CLI User Stories
----------------

.. list-table::
   :widths: 8 26 37
   :header-rows: 1

   -

      - Status for Story ID
      - Title
      - Story Overview
   -

      - |image-CLI1|
      - Obtain constitution hash for verification (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI001>`__
      - **As** an Ada holder, **I want** to obtain the hash of the off-chain text of a Constitution, **so that** I can compare it against the hash registered on-chain to verify its authenticity.
   -

      - |image-CLI2|
      - Generate hash of the off-chain constitution (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI002>`__
      - **As** an Ada holder, **I want** to generate the hash of the off-chain text for a proposed Constitution, **so that** the hash can be utilized in a governance action.
   -

      - |image-CLI3|
      - Generate Committee member cold key pair (CCM)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI003>`__
      - **As** a potential Constitutional Committee member, **I want** to generate COLD key pair, **so that** I can be proposed for the Committee in a Governance action.
   -

      - |image-CLI4|
      - Generate committee member hot key pair (CCM)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI004>`__
      - **As** potential Constitutional Committee member, **I want** to generate HOT key pair, **so that** I can authorise the Hot key to sign votes on behalf of the Cold key.
   -

      - |image-CLI5|
      - Authorization certificate (CCM)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI005>`__
      - **As** a Constitutional Committee member, **I want** to issue a authorization certificate from my cold key to a hot key, **so that** I can sign my votes using the hot key and keep the cold key in cold storage and can authorise a new hot key in case the original one is compromised.
   -

      - |image-CLI6|
      - Generate committee member key hash (CCM)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI006>`__
      - **As** a potential Constitutional Committee member, **I want** to generate the key hashes for my cold and hot keys, **so that** they can be used by third parties to propose me as a new Constitutional Committee member and for identification purposes once I’ve been elected as Constitutional Committee member.
   -

      - |image-CLI7|
      - Committee member resignation certificate (CCM)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI007>`__
      - **As** a Constitutional Committee member, **I want** to be able to generate a resignation certificate, **so that** I can submit it to the chain on a transaction to signal to the Ada holders that I’m resigning from my duties as CC member.
   -

      - |image-CLI8|
      - Generate DRep keys (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI008>`__
      - **As** an Ada holder, **I want** to generate Ed25519 keys, **so that** I can register as a DRep.
   -

      - |image-CLI9|
      - Generate DRep ID (DRep)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI009>`__
      - **As** a DRep, **I want** to generate a DRep Id, **so that** Ada holder can use it to delegate their votes to me and my voting record can be tracked.
   -

      - |image-CLI10|
      - DRep Registration Certificate Generation (DRep)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI010>`__
      - **As** a DRep, **I want** to generate a DRep registration certificate, **so that** I can submit it on a transaction and the Ada holders can delegate their votes to me.
   -

      - |image-CLI11|
      - DRep Retirement Certificate Generation (DRep)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI011>`__
      - **As** a DRep, **I want** to generate a DRep retirement (unregistration) certificate, **so that** I can submit it on a transaction and can get my DRep deposit back.
   -

      - |image-CLI12|
      - DRep Metadata Hash Generation (DRep)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI012>`__
      - **As** a DRep, **I want** to generate the hash of my DRep metadata, **so that** I can supply it when registering as DRep.
   -

      - |image-CLI13|
      - Create Update Constitution Governance Action (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI013>`__
      - **As** an Ada holder, **I want** to create a governance action that updates the constitution, **so that** it can be submitted to the chain and be voted by the governance bodies.
   -

      - |image-CLI14|
      - Create Update Constitutional Committee Governance Action (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI014>`__
      - **As** an Ada holder, **I want** to create a governance action that updates the Constitutional Committee, **so that** it can be submitted to the chain and be voted by the governance bodies.
   -

      - |image-CLI15|
      - Create Treasury Withdrawal Governance Action (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI015>`__
      - **As** an Ada holder, **I want** to create a governance action to withdraw funds from the treasury, **so that** it can be submitted to the chain and be voted by the governance bodies.
        Command: ``cardano-cli conway governance action create-treasury-withdrawal``.
   -

      - |image-CLI16|
      - Create info governance action (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI016>`__
      - **As** an Ada holder, **I want** to create an info governance action, **so that** it can be submitted to the chain and be voted by the governance bodies.
        Command: ``cardano-cli conway governance action create-info``.
   -

      - |image-CLI17|
      - Create update protocol parameters governance action (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI017>`__
      - **As** an Ada holder, **I want** to create a governance action to update protocol parameters, **so that** it can be submitted to the chain and be voted by the governance bodies.
        Command: ``cardano-cli conway governance action create-protocol-parameters-update``.
   -

      - |image-CLI18|
      - Create no-confidence governance action (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI018>`__
      - **As** an Ada holder, **I want** to create a no-confidence governance action, **so that** it can be submitted to the chain and be voted by the governance bodies.
        Command: ``cardano-cli conway governance action create-no-confidence``.
   -

      - |image-CLI19|
      - Create Hard-fork initiation governance action (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI019>`__
      - **As** an Ada holder, **I want** to create a governance action to initiate a hardfork, **so that** it can be submitted to the chain and be voted by the governance bodies.
        Command: ``cardano-cli conway governance action create-hf-init``.
   -

      - |image-CLI20|
      - View governance action file (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI020>`__
      - **As** an Ada holder, **I want** to inspect the contents of a governance action file, **so that** I can verify it is correct before submitting it in a transaction.
        Command: ``cardano-cli conway governance action view``.
   -

      - |image-CLI21|
      - Create a governance action vote (DRep/SPO/CCM)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI021>`__
      - **As** a DRep, SPO or CC member, **I want** to create a vote for a governance action, **so that** I can include it in a transaction and submit it to the chain.
        Command: ``cardano-cli conway governance vote create``.
   -

      - |image-CLI22|
      - View vote file (DRep/SPO/CCM)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI022>`__
      - **As** a DRep, SPO or CC member, **I want** to inspect the contents of a vote file, **so that** I can verify it is correct before submitting it in a transaction.
        Command: ``cardano-cli conway governance vote view``.
   -

      - |image-CLI23|
      - Build a transaction with to submit proposal (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI023>`__
      - **As** an Ada holder, **I want** to build a transaction that includes a proposal (containing a governance action), **so that** I can later sign and submit to the chain.
        Command: ``transaction build``.
   -

      - |image-CLI24|
      - Build transaction for proposal vote (DRep, SPO, CCM)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI024>`__
      - **As** a DRep, SPO or CC member, **I want** to build a transaction that includes my vote on a particular governance action, **so that** I can later sign and submit to the chain.
        Command: ``transaction build``.
   -

      - |image-CLI25|
      - Build RAW transaction for proposal vote (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI025>`__
      - **As** an Ada holder, **I want** to build a transaction that includes a proposal (containing a governance action), **so that** I can later sign and submit to the chain.
        Command: ``transaction build-raw``.
   -

      - |image-CLI26|
      - Build RAW transaction for proposal vote (DRep/SPO/CCM)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI026>`__
      - **As** a DRep, SPO or CC member, **I want** to build a transaction that includes my vote on a particular governance action, **so that** I can later sign and submit to the chain.
        Command: ``transaction build-raw``.
   -

      - |image-CLI27|
      - Create stake registration certificate (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI027>`__
      - **As** an Ada holder, **I want** to create a conway cddl-compliant stake registration certificate.
   -

      - |image-CLI28|
      - Create stake deregistration certificate (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI028>`__
      - **As** an Ada holder, **I want** to create a conway cddl-compliant stake deregistration certificate to get my deposit back.
   -

      - |image-CLI29|
      - Delegate vote to DRep (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI029>`__
      - **As** an Ada holder, **I want** to delegate my votes to a DRep (registered or default), **so that** my stake is counted when the DRep votes.
   -

      - |image-CLI30|
      - Delegate stake to SPO and votes to DRep with a single certificate (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI030>`__
      - **As** an Ada holder, **I want** to delegate my stake to a stake pool AND my votes to a DRep (registered or default) with a single certificate.
   -

      - |image-CLI31|
      - Query governance state (ANY)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI031>`__
      - **As** any persona, **I want** to query the nodes for the currentGovernance state, **so that** I can inform my decisions.
   -

      - |image-CLI32|
      - Query committee state (CCM)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI032>`__
      - **As** a CC member, **I want** to query the Constitutional Committee state, **so that** I can find my expiration term and whether my hot key authorization certificate has been recorded on chain.
   -

      - |image-CLI33|
      - Query DRep state (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI033>`__
      - **As** an Ada holder, **I want** to query the DRep state, **so that** I can find detailed information about registered DReps.
   -

      - |image-CLI34|
      - Query DRep stake distribution (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI034>`__
      - **As** an Ada holder and DRep, **I want** to query the DRep stake distribution, **so that** I can find the weight (of the votes) of each DRep.
   -

      - |image-CLI35|
      - Expand query stake-address-info to show deposits and vote delegation (HOLDER)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI035>`__
      - **As** an Ada holder, **I want** to query my stake address information, **so that** I can learn to which pool and DRep I’m delegating to and the value in lovelace of my deposits for delegating and for submitting governance actions.
   -

      - |image-CLI36|
      - Query constitution.
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI036>`__
      - **As** any persona, **I want** to query the on-chain constitution, **so that** I can know the url where it is stored and the document hash, **so that** I can verify authenticity.
   -

      - |image-CLI37|
      - Register script based DReps.
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI037>`__
      -
   -

      - |image-CLI38|
      - Unregister script based DReps.
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI038>`__
      -
   -

      - |image-CLI39|
      - Script based CC GA. ``--add`` ``--remove``.
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI039>`__
      -
   -

      - |image-CLI40|
      - Script based CC GA. ``--add`` ``--remove``.
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/02-cardano-cli.md#CLI040>`__
      -

CIP1694 User Stories
--------------------

.. list-table::
   :widths: 8 26 37
   :header-rows: 1

   -

      - Status for Story ID
      - Title
      - Story Overview
   -

      - |image-CIP1a|
      - Constitution
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP001a>`__
      - **As** a Stakeholder, **I want** the ledger to maintain a record of the hash value of the current constitution together with a URL hosting the off-chain document, **so that** I can verify the authenticity of the off-chain document.
   -

      - |image-CIP1b|
      - Hash value of the off-chain Constitution is recorded on-chain
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP001b>`__
      - **As** a Stakeholder, **I want** the ledger to maintain a record of the hash value of the current constitution together with a URL hosting the off-chain document, **so that** I can verify the authenticity of the off-chain document.
   -

      - |image-CIP2|
      - Node records Committee member key hashes, terms and status
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP002>`__
      - **As** a Stakeholder, **I want** the key hash of active and expired Committee Members and their terms to be registered on-chain, **so that** the system can count their votes.
   -

      - |image-CIP3|
      - Authorization Certificate
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP003>`__
      - **As** a Committee Member, **I want** to generate and submit a Cold to Hot Credential Authorization certificate, **so that** I can sign votes using the hot credential and keep the cold credential in safe storage.
   -

      - |image-CIP4|
      - Record cold credentials and authorization certificates on chain
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP004>`__
      - **As** a committee member, **I want** the ledger to accurately maintain the record of key-hashes, terms, and cold to hot credentials authorization maps for active and expired members, **so that** only votes from active Committee members count.
   -

      - |image-CIP5|
      - Replacing the constitutional committee via a governance action
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP005>`__
      - **As** a Governance Actor, **I want** to submit a governance action to replace all or part of the current constitutional committee, **so that** committee members who have lost the confidence of stakeholders can be removed from their role.
   -

      - |image-CIP6|
      - Size of the constitutional committee
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP006>`__
      - **As** a Stakeholder, **I want** the minimal size of the Constitutional Committee to be a protocol parameter, **so that** it can be adjusted via a governance action.
   -

      - |image-CIP7|
      - Committee voting threshold (quorum) can be modified
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP007>`__
      - **As** a Stakeholder, **I want** the committee quorum (the fraction of committee required to ratify a gov action) to be not fixed, **so that** it can be modified via a governance action.
   -

      - |image-CIP8|
      - Electing an empty committee
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP008>`__
      - **As** a Stakeholder, **I want** to have the option of electing an empty committee, **so that** governance actions don’t need the votes of a Constitutional Committee to be ratified.
   -

      - |image-CIP9|
      - Constitutional committee members have a limited term
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP009>`__
      - **As** a Stakeholder and as a Committee Member, **I want** each Committee Member to have an individual term, **so that** the system can have a rotation scheme.
   -

      - |image-CIP10|
      - Tracking committee member expirations
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP010>`__
      - **As** a Stakeholder, **I want** the system to keep track of the expiration epoch of each committee member, **so that** the information is publicly available in the ledger and the community can plan ahead and agree on new CC member.
   -

      - |image-CIP11|
      - Automatically expire committee members that have completed their terms
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP011>`__
      - **As** a Stakeholder and as a Committee Member, **I want** the system to automatically expire committee members that have reached their term, **so that** only votes from active committee members count towards ratification.
   -

      - |image-CIP12|
      - Resign as committee member
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP012>`__
      - **As** a committee member, **I want** to be able to resign my responsibilities, **so that** I can stop my responsibilities with the Cardano Community while minimizing the effects on the system.
   -

      - |image-CIP13|
      - State of no-confidence
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP013>`__
      - **As** a Stakeholder, **I want** to submit a governance action to depose the current Constitutional Committee and put the system in a no-confidence state, **so that** the community must elect a new Constitutional Committee.
   -

      - |image-CIP14|
      - Constitutional Committee below committeeMinSize
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP014>`__
      - **As** a Stakeholder, I want, when the number of non-expired committee members falls below the minimal size of the committee, only update-committee and no-confidence governance actions can be ratified.
   -

      - |image-CIP15|
      - Proposal policy
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP015>`__
      - **As** a Stakeholder, **I want** the option for the constitution to be accompanied by a script, **so that** governance actions proposing parameter changes or treasury withdrawals that violate accepted limits are automatically restricted.
   -

      - |image-CIP16|
      - Delegate votes to a registered Delegate Representatives
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP016>`__
      - **As** a Stakeholder, **I want** to delegate voting rights to a registered Delegate Representative (DRep), **so that** I can participate in the governance of the system backing up votes with my stake.
   -

      - |image-CIP17|
      - Delegate to always abstain
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP017>`__
      - **As** a Stakeholder, **I want** to delegate my stake to the predefined option 'Abstain', **so that** my stake is marked as not participating in governance.
   -

      - |image-CIP18|
      - Delegate to no-confidence
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP018>`__
      - **As** a Stakeholder, **I want** to delegate my stake to the predefined DRep 'No Confidence', **so that** my stake is counted as a 'Yes' vote on every 'No Confidence' action and a 'No' vote on every other action.
   -

      - |image-CIP19|
      - Inactive DReps
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP019>`__
      - **As** an Ada holder, **I want** DReps to be considered inactive if they don’t vote for ``drepActivity``-many epochs, **so that** their delegated stake does not count towards the active voting stake, this to avoid leaving the system in a state where no governance action can pass.
   -

      - |image-CIP20|
      - DRep credentials
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP020>`__
      - **As** a DRep, **I want** to be identified by a credential that can be a verification key (Ed25519) or a Native or Plutus Script, **so that** I can register and vote on governance actions with a signing key or with the evaluation of a script logic.
   -

      - |image-CIP21|
      - DRep registration certificate
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP021>`__
      - **As** a DRep, **I want** to generate and submit a registration certificate, **so that** the system recognizes my credentials and counts my votes on governance actions proportionally to the voting stake delegated to me.
   -

      - |image-CIP22|
      - Vote delegation certificate
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP022>`__
      - **As** a Stakeholder, **I want** to generate a vote delegation certificate, enabling me to delegate my voting rights to either a default or a registered DRep.
   -

      - |image-CIP23|
      - DRep retirement certificate
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP023>`__
      - **As** a DRep, **I want** to generate and submit a retirement certificate, **so that** the system and stakeholders know that I’m no longer voting on governance actions and that stakeholders should re-delegate.
   -

      - |image-CIP24|
      - DRep retirement certificate is applied immediately after being accepted on-chain
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP024>`__
      - **As** a DRep, **I want** my retirement certificate to be applied immediately upon acceptance on-chain, with the DRep deposit returned in the same transaction, ensuring no waiting time.
   -

      - |image-CIP25|
      - per-DRep stake distribution
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP025>`__
      - **As** an Ada Holder, **I want** the system to calculate the stake distribution per DRep, **so that** each DRep's vote is weighted according to the actual stake delegated to them. This per-DRep stake distribution should use the stake snapshot from the last epoch boundary.
   -

      - |image-CIP26|
      - Bootstrapping phase
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP026>`__
      -
   -

      - |image-CIP27|
      - Block rewards withdrawals for stake credentials that are not delegating to a DRep
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP027>`__
      - **As** a Stakeholder, **I want** that when bootstrapping phase ends, the system blocks rewards withdrawals for stake credentials that are not delegating to a DRep.
   -

      - |image-CIP28|
      - Types of governance actions
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP028>`__
      - **As** a Stakeholder, **I want** the governance system to allow 7 different types of governance actions:

        1. Motion of no-confidence A motion to create a state of no-confidence in the current Constitutional Committee
        2. New Constitutional Committee and/or threshold and/or terms Changes to the members of the Constitutional Committee and/or to its signature threshold and/or terms
        3. Update to the Constitution or proposal policy A modification to the Constitution or proposal policy, recorded as on-chain hashes
        4. Hard-Fork Initiation Triggers a non-backwards compatible upgrade of the network; requires a prior software upgrade
        5. Protocol Parameter Changes Any change to one or more updatable protocol parameters, excluding changes to major protocol versions ("hard forks")
        6. Treasury Withdrawals from the treasury
        7. Info
   -

      - |image-CIP29|
      - Governance action initiation
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP029>`__
      - **As** a Stakeholder, **I want** any stakeholder to be able to submit a governance action without restrictions, beyond those necessary for a transaction of this type to be considered valid.
   -

      - |image-CIP30|
      - Governance action initiation
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP030>`__
      - **As** a Stakeholder, **I want** Governance Actors to be required to provide a deposit in lovelace, **so that** I can prevent the network from being spammed with meaningless governance actions. This deposit should be returned once the action is either ratified or expired.
   -

      - |image-CIP31a|
      - Contents of governance actions
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP031a>`__
      - **As** a Governance Actor, **I want** every governance action to contain the following elements:

        - a deposit amount
        - a reward address to receive the deposit back
        - an anchor for any metadata
        - a hash digest value of the last enacted governance action of the same type (except for Treasury withdrawals and Info), to ensure the action can be processed by the node, accepted on-chain, and considered by the governance bodies.
   -

      - |image-CIP31b|
      - New committee/threshold GA additional data
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP031b>`__
      - **As** a Governance actor creating a New Committee governance action, **I want** to specify the following additional data:

        - The set of verification key hash digests for members to be removed.
        - A map of verification key hash digests to epoch numbers for new members - and their term limit in epochs.
        - A fraction representing the quorum threshold. So that I can create a governance action that aligns with the Conway CDDL ensuring it is comprehensible and can be accurately processed by the ledger.
   -

      - |image-CIP31c|
      - Update the constitution GA additional data
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP031c>`__
      - **As** a Governance actor creating a Update to the constitution GA, **I want** to include an anchor to the Constitution and an optional script hash of the proposal policy.
   -

      - |image-CIP31d|
      - Hardfork initiation GA additional data
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP031d>`__
      - **As** a Governance actor creating a hardfork initiation governance action, **I want** to include the new (greater) major protocol version.
   -

      - |image-CIP31e|
      - Protocol parameter changes GA additional data
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP031e>`__
      - **As** a Governance actor creating a protocol parameter change GA, **I want** to include the parameter to change and their new values.
   -

      - |image-CIP31f|
      - Treasury withdrawal GA additional data
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP031f>`__
      - **As** a governance actor creating a treasury withdrawal GA, **I want** to include a map from stake credentials to a positive number of Lovelace.
   -

      - |image-CIP32|
      - Governance action maximum lifetime
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP032>`__
      - **As** a Stakeholder, **I want** governance actions submitted in a transaction and admitted to the chain to remain active for up to govActionLifetime epochs, **so that** these actions are checked for ratification at every epoch boundary within their govActionLifetime. If an action gathers enough 'yes' votes to meet the thresholds of the governing bodies, it is ratified; otherwise, if it fails to gather sufficient 'yes' votes during the active period, the proposal expires and is removed.
   -

      - |image-CIP33|
      - Enactment of ratified actions
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP033>`__
      - **As** a Stakeholder, **I want** ratified actions to be automatically enacted at the next epoch transition following their ratification.
   -

      - |image-CIP34|
      - Governance action deposit returns
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP034>`__
      - **As** a Governance Actor, **I want** governance action deposits to be returned immediately after ratification or expiration.
   -

      - |image-CIP35|
      - Deposits count towards voting power (stake)
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP035>`__
      - Governance action deposits are added to the deposit pot and count towards the stake of the reward address to which they will be returned, to ensure that the proposer can back their own action with their voting power.
   -

      - |image-CIP36|
      - Proposal policy
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP036>`__
      - **As** a Stakeholder, **I want** governance actions that attempt to change protocol parameters or involve treasury withdrawals to include the supplementary script from the constitution in the witness set, either directly or via reference inputs, whenever such a script exists.
   -

      - |image-CIP37|
      - Multiple protocol parameter updates
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP037>`__
      - **As** a Governance Actor, **I want** a governance action to allow multiple protocol parameter changes at once.
   -

      - |image-CIP38|
      - Delay of ratification
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP038>`__
      - **As** a Stakeholder, **I want** the ratification of all other governance actions to be delayed until the first epoch following the enactment of a successful motion of no-confidence, the election of a new Constitutional Committee, a constitutional change, or a hard-fork.
   -

      - |image-CIP39|
      - Motion of no confidence, requirements for ratification
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP039>`__
      - **As** a Stakeholder, **I want** that the ratification of a Motion of no confidence governance action requires:

        - DRep votes to be >= than DrepVotingThreshold for NoConfidence as a percentage of active voting stake.
        - SPO votes to be >= than PoolVotingThreshold for NoConfidence as a percentage of the total delegated active stake for the epoch
   -

      - |image-CIP40|
      - New committee/threshold (normal state), requirements for ratification
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP040>`__
      - **As** a Stakeholder, **I want** that the ratification of a New committee/threshold (normal state) governance action requires:

        - DRep votes to be >= than DrepVotingThreshold for CommitteeNormalState as a percentage of active voting stake.
        - SPO votes to be >= than PoolVotingThreshold for CommitteeNormalState as a percentage of the total delegated active stake for the epoch
   -

      - |image-CIP41|
      - New committee/threshold (state of no-confidence), requirements for ratification
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP041>`__
      - **As** a Stakeholder, **I want** that the ratification of a New committee/threshold (state of no-confidence) governance action requires:

        - DRep votes to be >= than DrepVotingThreshold dvtCommitteeNoConfidence as a percentage of active voting stake.
        - SPO votes to be >= than pvtCommitteeNoConfidence as a percentage of the total delegated active stake for the epoch
   -

      - |image-CIP42|
      - Update to the Constitution or proposal policy, requirements for ratification
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP042>`__
      - **As** a Stakeholder, **I want** that the ratification of a Update to the Constitution or proposal policy governance action requires:

        - A minimum of CommitteeThreshold members must approve the Governance action
        - DRep votes to be >= than DrepVotingThreshold for UpdateToConstitution as a percentage of active voting stake.
   -

      - |image-CIP43|
      - Hard-fork initiation, requirements for ratification
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP043>`__
      - **As** a Stakeholder, **I want** that the ratification of a Hard-fork initiation governance action requires:

        - A minimum of CommitteeThreshold members must approve the Governance action
        - DRep votes to be >= than DrepVotingThreshold for HardForkInitiation as a percentage of active voting stake.
        - SPO votes to be >= than PoolVotingThreshold for HardForkInitiation as a percentage of the total delegated active stake for the epoch
   -

      - |image-CIP44|
      - Protocol parameter changes, network group
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP044>`__
      - **As** a Stakeholder, **I want** that the ratification of a network group protocol parameter change requires:

        - A minimum of CommitteeThreshold members must approve the Governance action
        - DRep votes to be >= than DrepVotingThreshold for PPNetworkGroup as a percentage of active voting stake
   -

      - |image-CIP45|
      - Protocol parameter changes, economic group
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP045>`__
      - **As** a Stakeholder, **I want** that the ratification of a economic group protocol parameter change requires:

        - A minimum of CommitteeThreshold members must approve the Governance action
        - DRep votes to be >= than DrepVotingThreshold for PPEconomicGroup as a percentage of active voting stake
   -

      - |image-CIP46|
      - Protocol parameter changes, technical group
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP046>`__
      - **As** a Stakeholder, **I want** that the ratification of a technical group protocol parameter change requires:

        - A minimum of CommitteeThreshold members must approve the Governance action
        - DRep votes to be >= than `DrepVotingThreshold` for PPTechnicalGroup as a percentage of active voting stake
   -

      - |image-CIP47|
      - Protocol parameter changes, governance group
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP047>`__
      - **As** a Stakeholder, **I want** that the ratification of a governance group protocol parameter change requires:

        - A minimum of CommitteeThreshold members must approve the Governance action
        - DRep votes to be >= than DrepVotingThreshold PPGovGroup as a percentage of active voting stake
   -

      - |image-CIP48|
      - Treasury withdrawal, requirements for ratification
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP048>`__
      - **As** a Stakeholder, **I want** that the ratification of a Treasury withdrawal governance action requires:

        - A minimum of CommitteeThreshold members must approve the Governance action
        - DRep votes to be >= than DrepVotingThreshold for TreasuryWithdrawal as a percentage of active voting stake
   -

      - |image-CIP49|
      - The network group protocol parameters
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP049>`__
      - **As** a Stakeholder, **I want** the network group consist of:

        - maximum block body size (maxBBSize)
        - maximum transaction size (maxTxSize)
        - maximum block header size (maxBHSize)
        - maximum size of a serialized asset value (maxValSize)
        - maximum script execution units in a single transaction (maxTxExUnits)
        - maximum script execution units in a single block (maxBlockExUnits)
        - maximum number of collateral inputs (maxCollateralInputs)
   -

      - |image-CIP50|
      - The economic group protocol parameters
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP050>`__
      - **As** a Stakeholder, **I want** that the economic group consist of:

        - minimum fee coefficient (minFeeA)
        - minimum fee constant (minFeeB)
        - delegation key Lovelace deposit (keyDeposit)
        - pool registration Lovelace deposit (poolDeposit)
        - monetary expansion (rho)
        - treasury expansion (tau)
        - minimum fixed rewards cut for pools (minPoolCost)
        - minimum Lovelace deposit per byte of serialized UTxO (coinsPerUTxOByte)
        - prices of Plutus execution units (prices)
   -

      - |image-CIP51|
      - The technical group protocol parameters
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP051>`__
      - **As** a Stakeholder, **I want** that the technical group consist of:

        - pool pledge influence (a0)
        - pool retirement maximum epoch (eMax)
        - desired number of pools (nOpt)
        - Plutus execution cost models (costModels)
        - proportion of collateral needed for scripts (collateralPercentage)
   -

      - |image-CIP52|
      - The governance group protocol parameters
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP052>`__
      - **As** a Stakeholder, **I want** that the governance group consist of:

        - governance voting thresholds
        - governance action maximum lifetime in epochs (govActionLifetime)
        - governance action deposit (govActionDeposit)
        - DRep deposit amount (drepDeposit)
        - DRep activity period in epochs (drepActivity)
        - minimal constitutional committee size (ccMinSize)
        - maximum term length (in epochs) for the constitutional committee members (ccMaxTermLength)
   -

      - |image-CIP53|
      - Thresholds for Info is set to 100%
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP053>`__
      - **As** a Stakeholder, **I want** the two thresholds for the Info action be set to 100% since setting it any lower would result in not being able to poll above the threshold.
   -

      - |image-CIP54|
      - Preventing accidental clash of actions of the same type
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP054>`__
      - **As** a Stakeholder, **I want** all governance actions, except for Treasury withdrawals and Infos, to include the governance action ID of the most recently enacted action of the same type, **so that** accidental clashes between actions can be prevented.
   -

      - |image-CIP55|
      - Governance action enactment prioritization
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP055>`__
      - **As** a Stakeholder, **I want** actions that have been ratified in the current epoch to be prioritized for enactment in the following order:

        - Motion of no-confidence
        - New committee/threshold
        - Update to the Constitution or proposal policy
        - Hard Fork initiation
        - Protocol parameter changes
        - Treasury withdrawals
        - Info
   -

      - |image-CIP56|
      - Governance action order of enactment
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP056>`__
      - **As** a Stakeholder, **I want** governance actions to be enacted in the order of their acceptance to the chain.
   -

      - |image-CIP57|
      - Governance actions automatic enactment
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP057>`__
      - **As** a Stakeholder, **I want** ratified actions to be automatically enacted at the next epoch boundary.
   -

      - |image-CIP58|
      - No duplicate committee members
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP058>`__
      - **As** a Stakeholder, **I want** each pair of credentials in a committee to be unique, ensuring no duplicate committee members.
   -

      - |image-CIP59|
      - Governance action ID
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP059>`__
      - **As** a Stakeholder, **I want** the transaction ID and index of the transaction that submits the governance action to the chain to serve as the governance action ID, **so that** this ID shall would be used for casting votes.
   -

      - |image-CIP60|
      - Vote transactions contents
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP060>`__
      - **As** a Stakeholder, **I want** each vote transaction to consist of the following elements:

        - a governance action ID
        - a role (Constitutional Committee member, DRep, or SPO)
        - a governance credential witness for the role
        - an optional anchor for information relevant to the vote (as defined above)
        - a 'Yes'/'No'/'Abstain' vote.
   -

      - |image-CIP61|
      - SPO and DREP votes are proportional to the stake delegated to them
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP061>`__
      - For SPOs and DReps, the number of votes cast ('Yes', 'No', or 'Abstain') shall be proportional to the amount of Lovelace delegated to them at the time the action is checked for ratification.
   -

      - |image-CIP62|
      - CC votes
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP062>`__
      - **As** a Stakeholder, **I want** each current committee member to have one vote.
   -

      - |image-CIP63|
      - Active voting stake
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP063>`__
      - **As** a Stakeholder, **I want** the active voting stake to be the total registered stake minus the abstain votes stake (both credential DReps and AlwaysAbstain).
   -

      - |image-CIP64|
      - Unregistered stake behaves like Abstain vote
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP064>`__
      - **As** a Stakeholder, **I want** unregistered stake to be treated as an abstain vote, **so that** it should not count towards the active voting stake.
   -

      - |image-CIP65|
      - Registered stake that did not vote behaves like a 'No' vote
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP065>`__
      - **As** a Stakeholder, **I want** any registered stake that did not submit a vote, whether through its DRep or SPO, to be counted as a 'No' vote.
   -

      - |image-CIP66|
      - New Plutus script purpose for scripts
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP066>`__
      - **As** a Stakeholder, **I want** a new voting purpose for Plutus scripts.
   -

      - |image-CIP67|
      - Any new vote overrides any older vote for the same credential and role
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP067>`__
      - **As** a Stakeholder, **I want** new votes on a governance action to override any previous votes for the same credential and role, **so that** individuals could change their minds.
   -

      - |image-CIP68|
      - Voting ends when an action is ratified and transactions containing further votes are invalid
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP068>`__
      - **As** a Stakeholder, **I want** the voting period to terminate immediately after an action is ratified or expires.
   -

      - |image-CIP69|
      - Governance state tracking governance action progress
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP069>`__
      - **As** a Stakeholder, **I want** the governance state section of the ledger to track the progress of governance actions to include: capturing votes, tracking the expiration epoch, and other relevant information until the actions are either ratified or expired.
   -

      - |image-CIP70|
      - Remove MIR certificates
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP070>`__
      - **As** a Stakeholder, **I want** MIR certificates to be removed, **so that** the only way to withdraw funds from the treasury is through a ratified Treasury Withdrawal governance action.
   -

      - |image-CIP71|
      - Remove genesis certificates
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP071>`__
      - **As** a Stakeholder, **I want** genesis certificates to be removed. In Conway era these are no longer useful or required.
   -

      - |image-CIP72|
      - Changes to the existing ledger rules
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP072>`__
      - **As** a Stakeholder, **I want** the ledger to adjust its rules to accommodate for the governance features, i.e. Delegations, Certificates, Proposals, Votes, Ratification, Enactment.
   -

      - |image-CIP73|
      - Changes to the local state-query protocol
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP073>`__
      - **As** a Stakeholder, **I want** the ledger to adjust the local state query protocol to accommodate for new queries that provide insights about governance, at least:

        - Governance actions currently staged for enactment
        - Governance actions under ratification, with the total and percentage of yes stake, no stake and abstain stake
        - The current constitutional committee, and constitution hash digest
   -

      - |image-CIP74|
      - Ratification of Security related parameters
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP074>`__
      - The security relevant protocol parameters require the approval of the three governing bodies.

        - maxBBSize
        - maxTxSize
        - maxBHSize
        - maxValSize
        - maxBlockExUnits
        - minFeeA
        - minFeeB
        - coinsPerUTxOByte
        - govActionDeposit
        - minFeeRefScriptsCoinsPerByte
   -

      - |image-CIP75|
      - Auditor review of current network parameters
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP075>`__
      - **As** an Auditor, **I want** to audit the current state of the network parameters, **so that** I can ensure they align with the governance decisions.
   -

      - |image-CIP76|
      - Auditor review of current technical parameters
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP076>`__
      - **As** an Auditor, **I want** to audit the current technical parameters, including consenus and cost models, **so that** I can ensure their compliance with the network parameters specified.
   -

      - |image-CIP77|
      - Auditor review of current economic parameters
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP077>`__
      - **As** an Auditor, **I want** to audit the current economic parameters, including parameters affecting transaction fees, taxes, and staking rewards, **so that** I can assess their impact on the network's economy.
   -

      - |image-CIP78|
      - Auditor review of current governance parameters and voting thresholds
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP078>`__
      - **As** an Auditor, **I want** to audit the current governance parameters and voting thresholds for governance actions to fail or ratify, **so that** I can verify their appropriateness and adherence to governance rules, adherence to the constitution, and enforcement of voting thresholds.
   -

      - |image-CIP79|
      - Auditor review of current state of the treasury
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP079>`__
      - **As** an Auditor, **I want** to audit the current state of the treasury, including the total amount of Ada, **so that** I can assess the current balance and the system's financial health.
   -

      - |image-CIP80|
      - Auditor needs access to historical proposals affecting network parameters
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP080>`__
      - **As** an Auditor, **I want** to access and review the history of proposals related to network parameters, including their outcomes, **so that** I can track governance effectiveness over time.
   -

      - |image-CIP81|
      - Auditor needs access to historical proposals affecting technical parameters
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP081>`__
      - **As** an Auditor, **I want** to access and review the history of proposals related to technical parameters, including both ratified and failed proposals, **so that** I can understand technical evolution and parameter change impact.
   -

      - |image-CIP82|
      - Auditor needs access to historical proposals affecting economic parameters
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP082>`__
      - **As** an Auditor, **I want** to access and review the history of proposals related to economic parameters, focusing on their ratification status, **so that** I can evaluate economic policy changes.
   -

      - |image-CIP83|
      - Auditor needs access to the historical record of all governance proposals and voting thresholds
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP083>`__
      - **As** an Auditor, **I want** to access history changes to governance parameters, the proposals, and the voting thresholds, **so that** I can audit the changes made over time and verify compliance with governance rules, and evaluate the impact of these changes on governance actions' outcomes, with the primary purpose to verify voting thresholds were enforced.
   -

      - |image-CIP84|
      - Auditor needs access to the history of treasury withdrawals
        `→ <https://github.com/IntersectMBO/cardano-test-plans/blob/750a10833121e7d79ee97758b110050f1e04a5a6/docs/user-stories/01-cip1694.md#CIP084>`__
      - **As** an Auditor, **I want** to audit the history of treasury withdrawals, including amounts, dates, and recipient wallet addresses, **so that** I can ensure transparency and accountability.

.. |Success Badge| image:: https://img.shields.io/badge/success-green
.. |Failure Badge| image:: https://img.shields.io/badge/failure-red
.. |Partial Coverage Badge| image:: https://img.shields.io/badge/partial_coverage-yellow
.. |Uncovered Badge| image:: https://img.shields.io/badge/uncovered-grey

.. |image-CLI1| image:: https://img.shields.io/badge/CLI001-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_constitution.py#L203
.. |image-CLI2| image:: https://img.shields.io/badge/CLI002-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_constitution.py#L85
.. |image-CLI3| image:: https://img.shields.io/badge/CLI003-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L115
.. |image-CLI4| image:: https://img.shields.io/badge/CLI004-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L115
.. |image-CLI5| image:: https://img.shields.io/badge/CLI005-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L115
.. |image-CLI6| image:: https://img.shields.io/badge/CLI006-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L115
.. |image-CLI7| image:: https://img.shields.io/badge/CLI007-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L163
.. |image-CLI8| image:: https://img.shields.io/badge/CLI008-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L259
.. |image-CLI9| image:: https://img.shields.io/badge/CLI009-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L259
.. |image-CLI10| image:: https://img.shields.io/badge/CLI010-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L259
.. |image-CLI11| image:: https://img.shields.io/badge/CLI011-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L316
.. |image-CLI12| image:: https://img.shields.io/badge/CLI012-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L254
.. |image-CLI13| image:: https://img.shields.io/badge/CLI013-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_constitution.py#L91
.. |image-CLI14| image:: https://img.shields.io/badge/CLI014-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L464
.. |image-CLI15| image:: https://img.shields.io/badge/CLI015-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_treasury_withdrawals.py#L92
.. |image-CLI16| image:: https://img.shields.io/badge/CLI016-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_info.py#L69
.. |image-CLI17| image:: https://img.shields.io/badge/CLI017-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L640
.. |image-CLI18| image:: https://img.shields.io/badge/CLI018-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_no_confidence.py#L97
.. |image-CLI19| image:: https://img.shields.io/badge/CLI019-grey
   :target: https://github.com/CLI019-404
.. |image-CLI20| image:: https://img.shields.io/badge/CLI020-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_constitution.py#L250
.. |image-CLI21| image:: https://img.shields.io/badge/CLI021-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_info.py#L131
.. |image-CLI22| image:: https://img.shields.io/badge/CLI022-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_info.py#L224
.. |image-CLI23| image:: https://img.shields.io/badge/CLI023-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_info.py#L90
.. |image-CLI24| image:: https://img.shields.io/badge/CLI024-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_info.py#L177
.. |image-CLI25| image:: https://img.shields.io/badge/CLI025-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_treasury_withdrawals.py#L426
.. |image-CLI26| image:: https://img.shields.io/badge/CLI026-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_treasury_withdrawals.py#L515
.. |image-CLI27| image:: https://img.shields.io/badge/CLI027-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L555
.. |image-CLI28| image:: https://img.shields.io/badge/CLI028-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L603
.. |image-CLI29| image:: https://img.shields.io/badge/CLI029-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L564
.. |image-CLI30| image:: https://img.shields.io/badge/CLI030-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L750
.. |image-CLI31| image:: https://img.shields.io/badge/CLI031-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_info.py#L111
.. |image-CLI32| image:: https://img.shields.io/badge/CLI032-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L149
.. |image-CLI33| image:: https://img.shields.io/badge/CLI033-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L292
.. |image-CLI34| image:: https://img.shields.io/badge/CLI034-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L676
.. |image-CLI35| image:: https://img.shields.io/badge/CLI035-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L643
.. |image-CLI36| image:: https://img.shields.io/badge/CLI036-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_constitution.py#L230
.. |image-CLI37| image:: https://img.shields.io/badge/CLI037-grey
   :target: https://github.com/CLI037-404
.. |image-CLI38| image:: https://img.shields.io/badge/CLI038-grey
   :target: https://github.com/CLI038-404
.. |image-CLI39| image:: https://img.shields.io/badge/CLI039-grey
   :target: https://github.com/CLI038-404
.. |image-CLI40| image:: https://img.shields.io/badge/CLI040-grey
   :target: https://github.com/CLI038-404

.. |image-CIP1a| image:: https://img.shields.io/badge/CIP001a-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_constitution.py#L203
.. |image-CIP1b| image:: https://img.shields.io/badge/CIP001b-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_constitution.py#L203
.. |image-CIP2| image:: https://img.shields.io/badge/CIP002-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L149
.. |image-CIP3| image:: https://img.shields.io/badge/CIP003-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L115
.. |image-CIP4| image:: https://img.shields.io/badge/CIP004-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L149
.. |image-CIP5| image:: https://img.shields.io/badge/CIP005-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L533
.. |image-CIP6| image:: https://img.shields.io/badge/CIP006-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L640
.. |image-CIP7| image:: https://img.shields.io/badge/CIP007-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L251
.. |image-CIP8| image:: https://img.shields.io/badge/CIP008-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L1174
.. |image-CIP9| image:: https://img.shields.io/badge/CIP009-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L830
.. |image-CIP10| image:: https://img.shields.io/badge/CIP010-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L830
.. |image-CIP11| image:: https://img.shields.io/badge/CIP011-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L783
.. |image-CIP12| image:: https://img.shields.io/badge/CIP012-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L163
.. |image-CIP13| image:: https://img.shields.io/badge/CIP013-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_no_confidence.py#L97
.. |image-CIP14| image:: https://img.shields.io/badge/CIP014-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_no_confidence.py#L353
.. |image-CIP15| image:: https://img.shields.io/badge/CIP015-grey
   :target: https://github.com/CIP015-404
.. |image-CIP16| image:: https://img.shields.io/badge/CIP016-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L552
.. |image-CIP17| image:: https://img.shields.io/badge/CIP017-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L552
.. |image-CIP18| image:: https://img.shields.io/badge/CIP018-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L552
.. |image-CIP19| image:: https://img.shields.io/badge/CIP019-grey
   :target: https://github.com/CIP019-404
.. |image-CIP20| image:: https://img.shields.io/badge/CIP020-yellow
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L670
.. |image-CIP21| image:: https://img.shields.io/badge/CIP021-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L259
.. |image-CIP22| image:: https://img.shields.io/badge/CIP022-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L564
.. |image-CIP23| image:: https://img.shields.io/badge/CIP023-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L316
.. |image-CIP24| image:: https://img.shields.io/badge/CIP024-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L340
.. |image-CIP25| image:: https://img.shields.io/badge/CIP025-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_drep.py#L676
.. |image-CIP26| image:: https://img.shields.io/badge/CIP026-grey
   :target: https://github.com/CIP026-404
.. |image-CIP27| image:: https://img.shields.io/badge/CIP027-grey
   :target: https://github.com/CIP027-404
.. |image-CIP28| image:: https://img.shields.io/badge/CIP028-yellow
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_no_confidence.py#L97
.. |image-CIP29| image:: https://img.shields.io/badge/CIP029-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_no_confidence.py#L97
.. |image-CIP30| image:: https://img.shields.io/badge/CIP030-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_no_confidence.py#L97
.. |image-CIP31a| image:: https://img.shields.io/badge/CIP031a-yellow
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L251
.. |image-CIP31b| image:: https://img.shields.io/badge/CIP031b-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L464
.. |image-CIP31c| image:: https://img.shields.io/badge/CIP031c-yellow
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_constitution.py#L91
.. |image-CIP31d| image:: https://img.shields.io/badge/CIP031d-grey
   :target: https://github.com/CIP031d-404
.. |image-CIP31e| image:: https://img.shields.io/badge/CIP031e-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L640
.. |image-CIP31f| image:: https://img.shields.io/badge/CIP031f-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_treasury_withdrawals.py#L92
.. |image-CIP32| image:: https://img.shields.io/badge/CIP032-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_no_confidence.py#L258
.. |image-CIP33| image:: https://img.shields.io/badge/CIP033-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_treasury_withdrawals.py#L310
.. |image-CIP34| image:: https://img.shields.io/badge/CIP034-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_no_confidence.py#L278
.. |image-CIP35| image:: https://img.shields.io/badge/CIP035-grey
   :target: https://github.com/CIP035-404
.. |image-CIP36| image:: https://img.shields.io/badge/CIP036-grey
   :target: https://github.com/CIP036-404
.. |image-CIP37| image:: https://img.shields.io/badge/CIP037-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L1019
.. |image-CIP38| image:: https://img.shields.io/badge/CIP038-yellow
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L774
.. |image-CIP39| image:: https://img.shields.io/badge/CIP039-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_no_confidence.py#L184
.. |image-CIP40| image:: https://img.shields.io/badge/CIP040-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L688
.. |image-CIP41| image:: https://img.shields.io/badge/CIP041-green
   :target: https://github.com/CIP41-404
.. |image-CIP42| image:: https://img.shields.io/badge/CIP042-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_constitution.py#L137
.. |image-CIP43| image:: https://img.shields.io/badge/CIP043-grey
   :target: https://github.com/CIP043-404
.. |image-CIP44| image:: https://img.shields.io/badge/CIP044-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L736
.. |image-CIP45| image:: https://img.shields.io/badge/CIP045-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L736
.. |image-CIP46| image:: https://img.shields.io/badge/CIP046-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L736
.. |image-CIP47| image:: https://img.shields.io/badge/CIP047-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L736
.. |image-CIP48| image:: https://img.shields.io/badge/CIP048-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_treasury_withdrawals.py#L258
.. |image-CIP49| image:: https://img.shields.io/badge/CIP049-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L273
.. |image-CIP50| image:: https://img.shields.io/badge/CIP050-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L273
.. |image-CIP51| image:: https://img.shields.io/badge/CIP051-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L273
.. |image-CIP52| image:: https://img.shields.io/badge/CIP052-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L273
.. |image-CIP53| image:: https://img.shields.io/badge/CIP053-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_info.py#L131
.. |image-CIP54| image:: https://img.shields.io/badge/CIP054-yellow
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L640
.. |image-CIP55| image:: https://img.shields.io/badge/CIP055-grey
   :target: https://github.com/CIP055-404
.. |image-CIP56| image:: https://img.shields.io/badge/CIP056-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L1058
.. |image-CIP57| image:: https://img.shields.io/badge/CIP057-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_no_confidence.py#L258
.. |image-CIP58| image:: https://img.shields.io/badge/CIP058-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L464
.. |image-CIP59| image:: https://img.shields.io/badge/CIP059-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_info.py#L131
.. |image-CIP60| image:: https://img.shields.io/badge/CIP060-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L736
.. |image-CIP61| image:: https://img.shields.io/badge/CIP061-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L688
.. |image-CIP62| image:: https://img.shields.io/badge/CIP062-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L1041
.. |image-CIP63| image:: https://img.shields.io/badge/CIP063-grey
   :target: https://github.com/CIP063-404
.. |image-CIP64| image:: https://img.shields.io/badge/CIP064-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L688
.. |image-CIP65| image:: https://img.shields.io/badge/CIP065-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L848
.. |image-CIP66| image:: https://img.shields.io/badge/CIP066-grey
   :target: https://github.com/CIP066-404
.. |image-CIP67| image:: https://img.shields.io/badge/CIP067-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_committee.py#L671
.. |image-CIP68| image:: https://img.shields.io/badge/CIP068-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L1093
.. |image-CIP69| image:: https://img.shields.io/badge/CIP069-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_no_confidence.py#L169
.. |image-CIP70| image:: https://img.shields.io/badge/CIP070-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_treasury_withdrawals.py#L647
.. |image-CIP71| image:: https://img.shields.io/badge/CIP071-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_conway.py#L32
.. |image-CIP72| image:: https://img.shields.io/badge/CIP072-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_constitution.py#L203
.. |image-CIP73| image:: https://img.shields.io/badge/CIP073-yellow
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_constitution.py#L203
.. |image-CIP74| image:: https://img.shields.io/badge/CIP074-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L882
.. |image-CIP75| image:: https://img.shields.io/badge/CIP075-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L1205
.. |image-CIP76| image:: https://img.shields.io/badge/CIP076-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L1205
.. |image-CIP77| image:: https://img.shields.io/badge/CIP077-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L1205
.. |image-CIP78| image:: https://img.shields.io/badge/CIP078-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_pparam_update.py#L1205
.. |image-CIP79| image:: https://img.shields.io/badge/CIP079-green
   :target: https://github.com/IntersectMBO/cardano-node-tests/blob/fafba27f4ea8d79b23fc90ae8aed8307e623d543/cardano_node_tests/tests/tests_conway/test_treasury_withdrawals.py#L326
.. |image-CIP80| image:: https://img.shields.io/badge/CIP080-grey
   :target: https://github.com/CIP080-404
.. |image-CIP81| image:: https://img.shields.io/badge/CIP081-grey
   :target: https://github.com/CIP081-404
.. |image-CIP82| image:: https://img.shields.io/badge/CIP082-grey
   :target: https://github.com/CIP082-404
.. |image-CIP83| image:: https://img.shields.io/badge/CIP083-grey
   :target: https://github.com/CIP083-404
.. |image-CIP84| image:: https://img.shields.io/badge/CIP084-grey
   :target: https://github.com/CIP084-404
