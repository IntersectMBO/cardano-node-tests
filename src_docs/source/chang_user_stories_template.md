Chang HF User Stories Coverage
==============================

**Legend:** ![Success Badge][success-badge] ![Failure Badge][failure-badge] ![Uncovered Badge][uncovered-badge]
&nbsp;


CLI User Stories
----------------

| Status for Story ID | Title | Story Overview |
|---------------------|-------|----------------|
|[![Status Badge][CLI001-badge]][CLI001-link]| Obtain constitution hash for verification (HOLDER) | **As** an Ada holder, **I want** to obtain the hash of the off-chain text of a Constitution, **so that** I can compare it against the hash registered on-chain to verify its authenticity. |
|[![Status Badge][CLI002-badge]][CLI002-link]| Generate hash of the off-chain constitution (HOLDER) | **As** an Ada holder, **I want** to generate the hash of the off-chain text for a proposed Constitution, **so that** the hash can be utilized in a governance action. |
|[![Status Badge][CLI003-badge]][CLI003-link]| Generate Committee member cold key pair (CCM) | **As** a potential Constitutional Committee member, **I want** to generate COLD key pair, **so that** I can be proposed for the Committee in a Governance action. |
|[![Status Badge][CLI004-badge]][CLI004-link]| Generate committee member hot key pair (CCM) | **As** potential Constitutional Committee member, **I want** to generate HOT key pair, **so that** I can authorise the Hot key to sign votes on behalf of the Cold key. |
|[![Status Badge][CLI005-badge]][CLI005-link]| Authorization certificate (CCM) | **As** a Constitutional Committee member, **I want** to issue a authorization certificate from my cold key to a hot key, **so that** I can sign my votes using the hot key and keep the cold key in cold storage and can authorise a new hot key in case the original one is compromised. |
|[![Status Badge][CLI006-badge]][CLI006-link]| Generate committee member key hash (CCM) | **As** a potential Constitutional Committee member, **I want** to generate the key hashes for my cold and hot keys, **so that** they can be used by third parties to propose me as a new Constitutional Committee member and for identification purposes once I've been elected as Constitutional Committee member. |
|[![Status Badge][CLI007-badge]][CLI007-link]| Committee member resignation certificate (CCM) | **As** a Constitutional Committee member, **I want** to be able to generate a resignation certificate, **so that** I can submit it to the chain on a transaction to signal to the Ada holders that I'm resigning from my duties as CC member. |
|[![Status Badge][CLI008-badge]][CLI008-link]| Generate DRep keys (HOLDER) | **As** an Ada holder, **I want** to generate Ed25519 keys, **so that** I can register as a DRep. |
|[![Status Badge][CLI009-badge]][CLI009-link]| Generate DRep ID (DRep) | **As** a DRep, **I want** to generate a DRep Id, **so that** Ada holder can use it to delegate their votes to me and my voting record can be tracked. |
|[![Status Badge][CLI010-badge]][CLI010-link]| DRep Registration Certificate Generation (DRep) | **As** a DRep, **I want** to generate a DRep registration certificate, **so that** I can submit it on a transaction and the Ada holders can delegate their votes to me. |
|[![Status Badge][CLI011-badge]][CLI011-link]| DRep Retirement Certificate Generation (DRep) | **As** a DRep, **I want** to generate a DRep retirement (unregistration) certificate, **so that** I can submit it on a transaction and can get my DRep deposit back. |
|[![Status Badge][CLI012-badge]][CLI012-link]| DRep Metadata Hash Generation (DRep) | **As** a DRep, **I want** to generate the hash of my DRep metadata, **so that** I can supply it when registering as DRep. |
|[![Status Badge][CLI013-badge]][CLI013-link]| Create Update Constitution Governance Action (HOLDER) | **As** an Ada holder, **I want** to create a governance action that updates the constitution, **so that** it can be submitted to the chain and be voted by the governance bodies. |
|[![Status Badge][CLI014-badge]][CLI014-link]| Create Update Constitutional Committee Governance Action (HOLDER) | **As** an Ada holder, **I want** to create a governance action that updates the Constitutional Committee, **so that** it can be submitted to the chain and be voted by the governance bodies. |
|[![Status Badge][CLI015-badge]][CLI015-link]| Create Treasury Withdrawal Governance Action (HOLDER) | **As** an Ada holder, **I want** to create a governance action to withdraw funds from the treasury, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-treasury-withdrawal`. |
|[![Status Badge][CLI016-badge]][CLI016-link]| Create info governance action (HOLDER) | **As** an Ada holder, **I want** to create an info governance action, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-info`. |
|[![Status Badge][CLI017-badge]][CLI017-link]| Create update protocol parameters governance action (HOLDER) | **As** an Ada holder, **I want** to create a governance action to update protocol parameters, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-protocol-parameters-update`. |
|[![Status Badge][CLI018-badge]][CLI018-link]| Create no-confidence governance action (HOLDER) | **As** an Ada holder, **I want** to create a no-confidence governance action, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-no-confidence`. |
|[![Status Badge][CLI019-badge]][CLI019-link]| Create Hard-fork initiation governance action (HOLDER) | **As** an Ada holder, **I want** to create a governance action to initiate a hardfork, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-hf-init`. |
|[![Status Badge][CLI020-badge]][CLI020-link]| View governance action file (HOLDER) | **As** an Ada holder, **I want** to inspect the contents of a governance action file, **so that** I can verify it is correct before submitting it in a transaction. Command: `cardano-cli conway governance action view`. |
|[![Status Badge][CLI021-badge]][CLI021-link]| Create a governance action vote (DRep/SPO/CCM) | **As** a DRep, SPO or CC member, **I want** to create a vote for a governance action, **so that** I can include it in a transaction and submit it to the chain.  Command: `cardano-cli conway governance vote create`. |
|[![Status Badge][CLI022-badge]][CLI022-link]| View vote file (DRep/SPO/CCM) | **As** a DRep, SPO or CC member, **I want** to inspect the contents of a vote file, **so that** I can verify it is correct before submitting it in a transaction. Command: `cardano-cli conway governance vote view`. |
|[![Status Badge][CLI023-badge]][CLI023-link]| Build a transaction with to submit proposal (HOLDER) | **As** an Ada holder, **I want** to build a transaction that includes a proposal (containing a governance action), **so that** I can later sign and submit to the chain. Command: `transaction build`. |
|[![Status Badge][CLI024-badge]][CLI024-link]| Build transaction for proposal vote (DRep, SPO, CCM) | **As** a DRep, SPO or CC member, **I want** to build a transaction that includes my vote on a particular governance action, **so that** I can later sign and submit to the chain. Command: `transaction build`. |
|[![Status Badge][CLI025-badge]][CLI025-link]| Build RAW transaction for proposal vote (HOLDER) | **As** an Ada holder, **I want** to build a transaction that includes a proposal (containing a governance action), **so that** I can later sign and submit to the chain. Command: `transaction build-raw`. |
|[![Status Badge][CLI026-badge]][CLI026-link]| Build RAW transaction for proposal vote (DRep/SPO/CCM) | **As** a DRep, SPO or CC member, **I want** to build a transaction that includes my vote on a particular governance action, **so that** I can later sign and submit to the chain. Command: `transaction build-raw`. |
|[![Status Badge][CLI027-badge]][CLI027-link]| Create stake registration certificate (HOLDER) | **As** an Ada holder, **I want** to create a conway cddl-compliant stake registration certificate. |
|[![Status Badge][CLI028-badge]][CLI028-link]| Create stake deregistration certificate (HOLDER) | **As** an Ada holder, **I want** to create a conway cddl-compliant stake deregistration certificate to get my deposit back. |
|[![Status Badge][CLI029-badge]][CLI029-link]| Delegate vote to DRep (HOLDER) | **As** an Ada holder, **I want** to delegate my votes to a DRep (registered or default), **so that** my stake is counted when the DRep votes. |
|[![Status Badge][CLI030-badge]][CLI030-link]| Delegate stake to SPO and votes to DRep with a single certificate (HOLDER) | **As** an Ada holder, **I want** to delegate my stake to a stake pool AND my votes to a DRep (registered or default) with a single certificate. |
|[![Status Badge][CLI031-badge]][CLI031-link]| Query governance state (ANY) | **As** any persona, **I want** to query the nodes for the currentGovernance state, **so that** I can inform my decisions. |
|[![Status Badge][CLI032-badge]][CLI032-link]| Query committee state (CCM) | **As** a CC member, **I want** to query the Constitutional Committee state, **so that** I can find my expiration term and whether my hot key authorization certificate has been recorded on chain. |
|[![Status Badge][CLI033-badge]][CLI033-link]| Query DRep state (HOLDER) | **As** an Ada holder, **I want** to query the DRep state, **so that** I can find detailed information about registered DReps. |
|[![Status Badge][CLI034-badge]][CLI034-link]| Query DRep stake distribution (HOLDER) | **As** an Ada holder and DRep, **I want** to query the DRep stake distribution, **so that** I can find the weight (of the votes) of each DRep. |
|[![Status Badge][CLI035-badge]][CLI035-link]| Expand query stake-address-info to show deposits and vote delegation (HOLDER) | **As** an Ada holder, **I want** to query my stake address information, **so that** I can learn to which pool and DRep I'm delegating to and the value in lovelace of my deposits for delegating and for submitting governance actions. |
|[![Status Badge][CLI036-badge]][CLI036-link]| Register script based DReps. | |
|[![Status Badge][CLI037-badge]][CLI037-link]| Unregister script based DReps. | |
|[![Status Badge][CLI038-badge]][CLI038-link]| Script based CC GA. `--add` `--remove`. | |


CIP1694 User Stories
--------------------

| Status for Story ID | Title | Story Overview |
|---------------------|-------|----------------|
|[![Status Badge][CIP001-badge]][CIP001-link]| Hash value of the off-chain Constitution is recorded on-chain | **As** an Ada holder, **I want** the ledger state to record the hash of the current constitution, **so that** I can verify the authenticity of the  off-chain document. |
|[![Status Badge][CIP002-badge]][CIP002-link]| Node records Committee member key hashes, terms and status | **As** an Ada holder, **I want** the key hash of active and expired Committee Members and their terms to be registered on-chain, **so that** the system can count their votes. |
|[![Status Badge][CIP003-badge]][CIP003-link]| Authorization Certificate | **As** a Committee Member, **I want** to submit a Cold to Hot key Authorization certificate, **so that** I can sign my votes using the hot key and keep my cold keys safely in cold storage. |
|[![Status Badge][CIP004-badge]][CIP004-link]| Record cold credentials and authorization certificates on chain | **As** a committee member, **I want** node’s ledger state to accurately maintain the record of key-hashes, terms, and cold to hot key authorization maps for active and expired members, **so that** only votes from active Committee members are considered. |
|[![Status Badge][CIP005-badge]][CIP005-link]| Replacing the constitutional committee via a governance action | **As** an Ada holder, **I want** to be able to submit a governance action to replace all or part of the current constitutional committee, **so that** committee members that have lost confidence of Ada holders can be removed from their duties. |
|[![Status Badge][CIP006-badge]][CIP006-link]| Size of the constitutional committee | **As** an Ada holder, **I want** the size of the constitutional committee to be adjustable (a protocol parameter), **so that** I can propose a different size via a governance action. |
|[![Status Badge][CIP007-badge]][CIP007-link]| Committee voting threshold (quorum) can be modified | **As** an Ada holder, **I want** that the committee threshold (the fraction of committee required to ratify a gov action) is not fixed, **so that** I can propose a different threshold via a governance action. |
|[![Status Badge][CIP008-badge]][CIP008-link]| Electing an empty committee | **As** an Ada holder, **I want** to be able to elect an empty committee if the community wishes to abolish the constitutional committee entirely, **so that** governance actions don’t need the votes of a constitutional committee to be ratified. |
|[![Status Badge][CIP009-badge]][CIP009-link]| Constitutional committee members have a limited term | **As** an Ada holder, **I want** each committee member to have an individual term, **so that** the system can have a rotation scheme. |
|[![Status Badge][CIP010-badge]][CIP010-link]| Tracking committee member expirations | **As** an Ada holder, **I want** the system to keep track of the expiration epoch of each committee member, **so that** the information is publicly available in the ledger and can be consumed by anyone interested. |
|[![Status Badge][CIP011-badge]][CIP011-link]| Automatically expire committee members that have completed their terms | **As** an Ada holder, **I want** the system to automatically expire committee members that have reached their term, **so that** only active committee members can vote. |
|[![Status Badge][CIP012-badge]][CIP012-link]| Resign as committee member | **As** a committee member, **I want** to be able to resign my responsibilities, **so that** I can stop my responsibilities with the Cardano Community while minimizing the effects on the system. |
|[![Status Badge][CIP013-badge]][CIP013-link]| State of no-confidence | **As** an Ada holder, **I want** to submit a governance action to depose the current constitutional committee and put the system in a no-confidence-state, **so that** the community must elect a new constitutional committee. |
|[![Status Badge][CIP014-badge]][CIP014-link]| Automatically enter a state of no-confidence | **As** an Ada holder, **I want** the system to automatically enter a state of no-confidence when the number of non-expired committee members falls below the minimal size of the committee, **so that** only update-committee governance actions can be ratified. |
|[![Status Badge][CIP015-badge]][CIP015-link]| Proposal policy | **As** an Ada holder, **I want** a supplementary script to the constitution, **so that** some proposal types are automatically restricted. |
|[![Status Badge][CIP016-badge]][CIP016-link]| Delegate representatives | **As** an Ada holder, **I want** stake credentials to delegate voting rights to a registered delegate representative (DRep), **so that** I can participate in the governance of the system. |
|[![Status Badge][CIP017-badge]][CIP017-link]| Delegate to always abstain | **As** an Ada holder or an exchange, **I want** to delegate my stake to the predefined option 'Abstain', **so that** my stake is marked as not participating in governance. |
|[![Status Badge][CIP018-badge]][CIP018-link]| Delegate to no-confidence | **As** an Ada holder, **I want** to delegate my stake to the predefined DRep 'No Confidence', **so that** my stake is counted as a 'Yes' vote on every 'No Confidence' action and a 'No' vote on every other action. |
|[![Status Badge][CIP019-badge]][CIP019-link]| Inactive DReps | **As** an Ada holder, **I want** DReps to be considered inactive if they don’t vote for `drepActivity`-many epochs, **so that** their delegated stake does not count towards the active voting stake, this to avoid leaving the system in a state where no governance action can pass. |
|[![Status Badge][CIP020-badge]][CIP020-link]| DRep credentials | **As** a DRep, **I want** to be identified by a credential (A verification key (Ed2559) or a Native or Plutus Script), **so that** I can register and vote on governance actions. |
|[![Status Badge][CIP021-badge]][CIP021-link]| DRep registration certificate | **As** a DRep, **I want** to generate a registration certificate, **so that** the system recognizes my credentials and counts my votes on governance actions. |
|[![Status Badge][CIP022-badge]][CIP022-link]| Vote delegation certificate | **As** an Ada holder, **I want** to generate a vote delegation certificate, **so that** I can delegate my voting rights. |
|[![Status Badge][CIP023-badge]][CIP023-link]| DRep retirement certificate | **As** a DRep, **I want** to generate a retirement certificate, **so that** the system and Ada holders (delegators) know that I’m no longer voting on governance actions and they should redelegate. |

[success-badge]: https://img.shields.io/badge/success-green
[failure-badge]: https://img.shields.io/badge/failure-red
[uncovered-badge]: https://img.shields.io/badge/uncovered-grey

[CLI001-badge]: https://img.shields.io/badge/CLI001-grey
[CLI002-badge]: https://img.shields.io/badge/CLI002-grey
[CLI003-badge]: https://img.shields.io/badge/CLI003-grey
[CLI004-badge]: https://img.shields.io/badge/CLI004-grey
[CLI005-badge]: https://img.shields.io/badge/CLI005-grey
[CLI006-badge]: https://img.shields.io/badge/CLI006-grey
[CLI007-badge]: https://img.shields.io/badge/CLI007-grey
[CLI008-badge]: https://img.shields.io/badge/CLI008-grey
[CLI009-badge]: https://img.shields.io/badge/CLI009-grey
[CLI010-badge]: https://img.shields.io/badge/CLI010-grey
[CLI011-badge]: https://img.shields.io/badge/CLI011-grey
[CLI012-badge]: https://img.shields.io/badge/CLI012-grey
[CLI013-badge]: https://img.shields.io/badge/CLI013-grey
[CLI014-badge]: https://img.shields.io/badge/CLI014-grey
[CLI015-badge]: https://img.shields.io/badge/CLI015-grey
[CLI016-badge]: https://img.shields.io/badge/CLI016-grey
[CLI017-badge]: https://img.shields.io/badge/CLI017-grey
[CLI018-badge]: https://img.shields.io/badge/CLI018-grey
[CLI019-badge]: https://img.shields.io/badge/CLI019-grey
[CLI020-badge]: https://img.shields.io/badge/CLI020-grey
[CLI021-badge]: https://img.shields.io/badge/CLI021-grey
[CLI022-badge]: https://img.shields.io/badge/CLI022-grey
[CLI023-badge]: https://img.shields.io/badge/CLI023-grey
[CLI024-badge]: https://img.shields.io/badge/CLI024-grey
[CLI025-badge]: https://img.shields.io/badge/CLI025-grey
[CLI026-badge]: https://img.shields.io/badge/CLI026-grey
[CLI027-badge]: https://img.shields.io/badge/CLI027-grey
[CLI028-badge]: https://img.shields.io/badge/CLI028-grey
[CLI029-badge]: https://img.shields.io/badge/CLI029-grey
[CLI030-badge]: https://img.shields.io/badge/CLI030-grey
[CLI031-badge]: https://img.shields.io/badge/CLI031-grey
[CLI032-badge]: https://img.shields.io/badge/CLI032-grey
[CLI033-badge]: https://img.shields.io/badge/CLI033-grey
[CLI034-badge]: https://img.shields.io/badge/CLI034-grey
[CLI035-badge]: https://img.shields.io/badge/CLI035-grey
[CLI036-badge]: https://img.shields.io/badge/CLI036-grey
[CLI037-badge]: https://img.shields.io/badge/CLI037-grey
[CLI038-badge]: https://img.shields.io/badge/CLI038-grey
[CLI001-link]: https://github.com/CLI001-404
[CLI002-link]: https://github.com/CLI002-404
[CLI003-link]: https://github.com/CLI003-404
[CLI004-link]: https://github.com/CLI004-404
[CLI005-link]: https://github.com/CLI005-404
[CLI006-link]: https://github.com/CLI006-404
[CLI007-link]: https://github.com/CLI007-404
[CLI008-link]: https://github.com/CLI008-404
[CLI009-link]: https://github.com/CLI009-404
[CLI010-link]: https://github.com/CLI010-404
[CLI011-link]: https://github.com/CLI011-404
[CLI012-link]: https://github.com/CLI012-404
[CLI013-link]: https://github.com/CLI013-404
[CLI014-link]: https://github.com/CLI014-404
[CLI015-link]: https://github.com/CLI015-404
[CLI016-link]: https://github.com/CLI016-404
[CLI017-link]: https://github.com/CLI017-404
[CLI018-link]: https://github.com/CLI018-404
[CLI019-link]: https://github.com/CLI019-404
[CLI020-link]: https://github.com/CLI020-404
[CLI021-link]: https://github.com/CLI021-404
[CLI022-link]: https://github.com/CLI022-404
[CLI023-link]: https://github.com/CLI023-404
[CLI024-link]: https://github.com/CLI024-404
[CLI025-link]: https://github.com/CLI025-404
[CLI026-link]: https://github.com/CLI026-404
[CLI027-link]: https://github.com/CLI027-404
[CLI028-link]: https://github.com/CLI028-404
[CLI029-link]: https://github.com/CLI029-404
[CLI030-link]: https://github.com/CLI030-404
[CLI031-link]: https://github.com/CLI031-404
[CLI032-link]: https://github.com/CLI032-404
[CLI033-link]: https://github.com/CLI033-404
[CLI034-link]: https://github.com/CLI034-404
[CLI035-link]: https://github.com/CLI035-404
[CLI036-link]: https://github.com/CLI036-404
[CLI037-link]: https://github.com/CLI037-404
[CLI038-link]: https://github.com/CLI038-404

[CIP001-badge]: https://img.shields.io/badge/CIP001-grey
[CIP002-badge]: https://img.shields.io/badge/CIP002-grey
[CIP003-badge]: https://img.shields.io/badge/CIP003-grey
[CIP004-badge]: https://img.shields.io/badge/CIP004-grey
[CIP005-badge]: https://img.shields.io/badge/CIP005-grey
[CIP006-badge]: https://img.shields.io/badge/CIP006-grey
[CIP007-badge]: https://img.shields.io/badge/CIP007-grey
[CIP008-badge]: https://img.shields.io/badge/CIP008-grey
[CIP009-badge]: https://img.shields.io/badge/CIP009-grey
[CIP010-badge]: https://img.shields.io/badge/CIP010-grey
[CIP011-badge]: https://img.shields.io/badge/CIP011-grey
[CIP012-badge]: https://img.shields.io/badge/CIP012-grey
[CIP013-badge]: https://img.shields.io/badge/CIP013-grey
[CIP014-badge]: https://img.shields.io/badge/CIP014-grey
[CIP015-badge]: https://img.shields.io/badge/CIP015-grey
[CIP016-badge]: https://img.shields.io/badge/CIP016-grey
[CIP017-badge]: https://img.shields.io/badge/CIP017-grey
[CIP018-badge]: https://img.shields.io/badge/CIP018-grey
[CIP019-badge]: https://img.shields.io/badge/CIP019-grey
[CIP020-badge]: https://img.shields.io/badge/CIP020-grey
[CIP021-badge]: https://img.shields.io/badge/CIP021-grey
[CIP022-badge]: https://img.shields.io/badge/CIP022-grey
[CIP023-badge]: https://img.shields.io/badge/CIP023-grey
[CIP001-link]: https://github.com/CIP001-404
[CIP002-link]: https://github.com/CIP002-404
[CIP003-link]: https://github.com/CIP003-404
[CIP004-link]: https://github.com/CIP004-404
[CIP005-link]: https://github.com/CIP005-404
[CIP006-link]: https://github.com/CIP006-404
[CIP007-link]: https://github.com/CIP007-404
[CIP008-link]: https://github.com/CIP008-404
[CIP009-link]: https://github.com/CIP009-404
[CIP010-link]: https://github.com/CIP010-404
[CIP011-link]: https://github.com/CIP011-404
[CIP012-link]: https://github.com/CIP012-404
[CIP013-link]: https://github.com/CIP013-404
[CIP014-link]: https://github.com/CIP014-404
[CIP015-link]: https://github.com/CIP015-404
[CIP016-link]: https://github.com/CIP016-404
[CIP017-link]: https://github.com/CIP017-404
[CIP018-link]: https://github.com/CIP018-404
[CIP019-link]: https://github.com/CIP019-404
[CIP020-link]: https://github.com/CIP020-404
[CIP021-link]: https://github.com/CIP021-404
[CIP022-link]: https://github.com/CIP022-404
[CIP023-link]: https://github.com/CIP023-404
