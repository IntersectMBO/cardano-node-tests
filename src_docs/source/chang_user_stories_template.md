Chang HF User Stories Coverage
==============================

**Legend:** ![Success Badge][success-badge] ![Failure Badge][failure-badge] ![Uncovered Badge][uncovered-badge]
&nbsp;


CLI User Stories
----------------

| Status for Story ID | Title | Story Overview |
|---------------------|-------|----------------|
|[![Status Badge][CLI01-badge]][CLI01-link]| Obtain constitution hash for verification (HOLDER) | **As** an Ada holder, **I want** to obtain the hash of the off-chain text of a Constitution, **so that** I can compare it against the hash registered on-chain to verify its authenticity. |
|[![Status Badge][CLI02-badge]][CLI02-link]| Generate hash of the off-chain constitution (HOLDER) | **As** an Ada holder, **I want** to generate the hash of the off-chain text for a proposed Constitution, **so that** the hash can be utilized in a governance action. |
|[![Status Badge][CLI03-badge]][CLI03-link]| Generate Committee member cold key pair (CCM) | **As** a potential Constitutional Committee member, **I want** to generate COLD key pair, **so that** I can be proposed for the Committee in a Governance action. |
|[![Status Badge][CLI04-badge]][CLI04-link]| Generate committee member hot key pair (CCM) | **As** potential Constitutional Committee member, **I want** to generate HOT key pair, **so that** I can authorise the Hot key to sign votes on behalf of the Cold key. |
|[![Status Badge][CLI05-badge]][CLI05-link]| Authorization certificate (CCM) | **As** a Constitutional Committee member, **I want** to issue a authorization certificate from my cold key to a hot key, **so that** I can sign my votes using the hot key and keep the cold key in cold storage and can authorise a new hot key in case the original one is compromised. |
|[![Status Badge][CLI06-badge]][CLI06-link]| Generate committee member key hash (CCM) | **As** a potential Constitutional Committee member, **I want** to generate the key hashes for my cold and hot keys, **so that** they can be used by third parties to propose me as a new Constitutional Committee member and for identification purposes once I've been elected as Constitutional Committee member. |
|[![Status Badge][CLI07-badge]][CLI07-link]| Committee member resignation certificate (CCM) | **As** a Constitutional Committee member, **I want** to be able to generate a resignation certificate, **so that** I can submit it to the chain on a transaction to signal to the Ada holders that I'm resigning from my duties as CC member. |
|[![Status Badge][CLI08-badge]][CLI08-link]| Generate DRep keys (HOLDER) | **As** an Ada holder, **I want** to generate Ed25519 keys, **so that** I can register as a DRep. |
|[![Status Badge][CLI09-badge]][CLI09-link]| Generate DRep ID (DRep) | **As** a DRep, **I want** to generate a DRep Id, **so that** Ada holder can use it to delegate their votes to me and my voting record can be tracked. |
|[![Status Badge][CLI10-badge]][CLI10-link]| DRep Registration Certificate Generation (DRep) | **As** a DRep, **I want** to generate a DRep registration certificate, **so that** I can submit it on a transaction and the Ada holders can delegate their votes to me. |
|[![Status Badge][CLI11-badge]][CLI11-link]| DRep Retirement Certificate Generation (DRep) | **As** a DRep, **I want** to generate a DRep retirement (unregistration) certificate, **so that** I can submit it on a transaction and can get my DRep deposit back. |
|[![Status Badge][CLI12-badge]][CLI12-link]| DRep Metadata Hash Generation (DRep) | **As** a DRep, **I want** to generate the hash of my DRep metadata, **so that** I can supply it when registering as DRep. |
|[![Status Badge][CLI13-badge]][CLI13-link]| Create Update Constitution Governance Action (HOLDER) | **As** an Ada holder, **I want** to create a governance action that updates the constitution, **so that** it can be submitted to the chain and be voted by the governance bodies. |
|[![Status Badge][CLI14-badge]][CLI14-link]| Create Update Constitutional Committee Governance Action (HOLDER) | **As** an Ada holder, **I want** to create a governance action that updates the Constitutional Committee, **so that** it can be submitted to the chain and be voted by the governance bodies. |
|[![Status Badge][CLI15-badge]][CLI15-link]| Create Treasury Withdrawal Governance Action (HOLDER) | **As** an Ada holder, **I want** to create a governance action to withdraw funds from the treasury, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-treasury-withdrawal`. |
|[![Status Badge][CLI16-badge]][CLI16-link]| Create info governance action (HOLDER) | **As** an Ada holder, **I want** to create an info governance action, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-info`. |
|[![Status Badge][CLI17-badge]][CLI17-link]| Create update protocol parameters governance action (HOLDER) | **As** an Ada holder, **I want** to create a governance action to update protocol parameters, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-protocol-parameters-update`. |
|[![Status Badge][CLI18-badge]][CLI18-link]| Create no-confidence governance action (HOLDER) | **As** an Ada holder, **I want** to create a no-confidence governance action, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-no-confidence`. |
|[![Status Badge][CLI19-badge]][CLI19-link]| Create Hard-fork initiation governance action (HOLDER) | **As** an Ada holder, **I want** to create a governance action to initiate a hardfork, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-hf-init`. |
|[![Status Badge][CLI20-badge]][CLI20-link]| View governance action file (HOLDER) | **As** an Ada holder, **I want** to inspect the contents of a governance action file, **so that** I can verify it is correct before submitting it in a transaction. Command: `cardano-cli conway governance action view`. |
|[![Status Badge][CLI21-badge]][CLI21-link]| Create a governance action vote (DRep/SPO/CCM) | **As** a DRep, SPO or CC member, **I want** to create a vote for a governance action, **so that** I can include it in a transaction and submit it to the chain.  Command: `cardano-cli conway governance vote create`. |
|[![Status Badge][CLI22-badge]][CLI22-link]| View vote file (DRep/SPO/CCM) | **As** a DRep, SPO or CC member, **I want** to inspect the contents of a vote file, **so that** I can verify it is correct before submitting it in a transaction. Command: `cardano-cli conway governance vote view`. |
|[![Status Badge][CLI23-badge]][CLI23-link]| Build a transaction with to submit proposal (HOLDER) | **As** an Ada holder, **I want** to build a transaction that includes a proposal (containing a governance action), **so that** I can later sign and submit to the chain. Command: `transaction build`. |
|[![Status Badge][CLI24-badge]][CLI24-link]| Build transaction for proposal vote (DRep, SPO, CCM) | **As** a DRep, SPO or CC member, **I want** to build a transaction that includes my vote on a particular governance action, **so that** I can later sign and submit to the chain. Command: `transaction build`. |
|[![Status Badge][CLI25-badge]][CLI25-link]| Build RAW transaction for proposal vote (HOLDER) | **As** an Ada holder, **I want** to build a transaction that includes a proposal (containing a governance action), **so that** I can later sign and submit to the chain. Command: `transaction build-raw`. |
|[![Status Badge][CLI26-badge]][CLI26-link]| Build RAW transaction for proposal vote (DRep/SPO/CCM) | **As** a DRep, SPO or CC member, **I want** to build a transaction that includes my vote on a particular governance action, **so that** I can later sign and submit to the chain. Command: `transaction build-raw`. |
|[![Status Badge][CLI27-badge]][CLI27-link]| Create stake registration certificate (HOLDER) | **As** an Ada holder, **I want** to create a conway cddl-compliant stake registration certificate. |
|[![Status Badge][CLI28-badge]][CLI28-link]| Create stake deregistration certificate (HOLDER) | **As** an Ada holder, **I want** to create a conway cddl-compliant stake deregistration certificate to get my deposit back. |
|[![Status Badge][CLI29-badge]][CLI29-link]| Delegate vote to DRep (HOLDER) | **As** an Ada holder, **I want** to delegate my votes to a DRep (registered or default), **so that** my stake is counted when the DRep votes. |
|[![Status Badge][CLI30-badge]][CLI30-link]| Delegate stake to SPO and votes to DRep with a single certificate (HOLDER) | **As** an Ada holder, **I want** to delegate my stake to a stake pool AND my votes to a DRep (registered or default) with a single certificate. |
|[![Status Badge][CLI31-badge]][CLI31-link]| Query governance state (ANY) | **As** any persona, **I want** to query the nodes for the currentGovernance state, **so that** I can inform my decisions. |
|[![Status Badge][CLI32-badge]][CLI32-link]| Query committee state (CCM) | **As** a CC member, **I want** to query the Constitutional Committee state, **so that** I can find my expiration term and whether my hot key authorization certificate has been recorded on chain. |
|[![Status Badge][CLI33-badge]][CLI33-link]| Query DRep state (HOLDER) | **As** an Ada holder, **I want** to query the DRep state, **so that** I can find detailed information about registered DReps. |
|[![Status Badge][CLI34-badge]][CLI34-link]| Query DRep stake distribution (HOLDER) | **As** an Ada holder and DRep, **I want** to query the DRep stake distribution, **so that** I can find the weight (of the votes) of each DRep. |
|[![Status Badge][CLI35-badge]][CLI35-link]| Expand query stake-address-info to show deposits and vote delegation (HOLDER) | **As** an Ada holder, **I want** to query my stake address information **so that** I can learn to which pool and DRep I'm delegating to and the value in lovelace of my deposits for delegating and for submitting governance actions. |
|[![Status Badge][CLI36-badge]][CLI36-link]| Register script based DReps. | |
|[![Status Badge][CLI37-badge]][CLI37-link]| Unregister script based DReps. | |
|[![Status Badge][CLI38-badge]][CLI38-link]| Script based CC GA. `--add` `--remove`. | |


CIP1694 User Stories
--------------------

| Status for Story ID | Title | Story Overview |
|---------------------|-------|----------------|
|[![Status Badge][CIP001-badge]][CIP001-link]| Hash value of the off-chain Constitution is recorded on-chain | **As** an Ada holder, **I want** the ledger state to record the hash of the current constitution **so that** I can verify the authenticity of the  off-chain document. |
|[![Status Badge][CIP002-badge]][CIP002-link]| Node records Committee member key hashes, terms and status | **As** an Ada holder, **I want** the key hash of active and expired Committee Members and their terms to be registered on-chain **so that** the system can count their votes. |
|[![Status Badge][CIP003-badge]][CIP003-link]| Authorization Certificate | **As** a Committee Member, **I want** to submit a Cold to Hot key Authorization certificate **so that** I can sign my votes using the hot key and keep my cold keys safely in cold storage. |
|[![Status Badge][CIP004-badge]][CIP004-link]| Record cold credentials and authorization certificates on chain | **As** a committee member, **I want** node’s ledger state to accurately maintain the record of key-hashes, terms, and cold to hot key authorization maps for active and expired members **so that** only votes from active Committee members are considered. |
|[![Status Badge][CIP005-badge]][CIP005-link]| Replacing the constitutional committee via a governance action | **As** an Ada holder, **I want** to be able to submit a governance action to replace all or part of the current constitutional committee **so that** committee members that have lost confidence of Ada holders can be removed from their duties. |
|[![Status Badge][CIP006-badge]][CIP006-link]| Size of the constitutional committee | **As** an Ada holder, **I want** the size of the constitutional committee to be adjustable (a protocol parameter) **so that** I can propose a different size via a governance action. |
|[![Status Badge][CIP007-badge]][CIP007-link]| Committee voting threshold (quorum) can be modified | **As** an Ada holder, **I want** that the committee threshold (the fraction of committee required to ratify a gov action) is not fixed **so that** I can propose a different threshold via a governance action. |
|[![Status Badge][CIP008-badge]][CIP008-link]| Electing an empty committee | **As** an Ada holder, **I want** to be able to elect an empty committee if the community wishes to abolish the constitutional committee entirely **so that** governance actions don’t need the votes of a constitutional committee to be ratified. |
|[![Status Badge][CIP009-badge]][CIP009-link]| Constitutional committee members have a limited term | **As** an Ada holder, **I want** each committee member to have an individual term **so that** the system can have a rotation scheme. |
|[![Status Badge][CIP010-badge]][CIP010-link]| Tracking committee member expirations | **As** an Ada holder, **I want** the system to keep track of the expiration epoch of each committee member **so that** the information is publicly available in the ledger and can be consumed by anyone interested. |
|[![Status Badge][CIP011-badge]][CIP011-link]| Automatically expire committee members that have completed their terms | **As** an Ada holder, **I want** the system to automatically expire committee members that have reached their term **so that** only active committee members can vote. |
|[![Status Badge][CIP012-badge]][CIP012-link]| Resign as committee member | **As** a committee member, **I want** to be able to resign my responsibilities **so that** I can stop my responsibilities with the Cardano Community while minimizing the effects on the system. |
|[![Status Badge][CIP013-badge]][CIP013-link]| State of no-confidence | **As** an Ada holder, **I want** to submit a governance action to depose the current constitutional committee and put the system in a no-confidence-state **so that** the community must elect a new constitutional committee. |
|[![Status Badge][CIP014-badge]][CIP014-link]| Automatically enter a state of no-confidence | **As** an Ada holder, **I want** the system to automatically enter a state of no-confidence when the number of non-expired committee members falls below the minimal size of the committee **so that** only update-committee governance actions can be ratified. |
|[![Status Badge][CIP015-badge]][CIP015-link]| Proposal policy | **As** an Ada holder, **I want** a supplementary script to the constitution **so that** some proposal types are automatically restricted. |
|[![Status Badge][CIP016-badge]][CIP016-link]| Delegate representatives | **As** an Ada holder, **I want** stake credentials to delegate voting rights to a registered delegate representative (DRep) **so that** I can participate in the governance of the system. |
|[![Status Badge][CIP017-badge]][CIP017-link]| Delegate to always abstain | **As** an Ada holder or an exchange, **I want** to delegate my stake to the predefined option 'Abstain' **so that** my stake is marked as not participating in governance. |
|[![Status Badge][CIP018-badge]][CIP018-link]| Delegate to no-confidence | **As** an Ada holder, **I want** to delegate my stake to the predefined DRep 'No Confidence' **so that** my stake is counted as a 'Yes' vote on every 'No Confidence' action and a 'No' vote on every other action. |
|[![Status Badge][CIP019-badge]][CIP019-link]| Inactive DReps | **As** an Ada holder, **I want** DReps to be considered inactive if they don’t vote for `drepActivity`-many epochs **so that** their delegated stake does not count towards the active voting stake, this to avoid leaving the system in a state where no governance action can pass. |
|[![Status Badge][CIP020-badge]][CIP020-link]| DRep credentials | **As** a DRep, **I want** to be identified by a credential (A verification key (Ed2559) or a Native or Plutus Script) **so that** I can register and vote on governance actions. |
|[![Status Badge][CIP021-badge]][CIP021-link]| DRep registration certificate | **As** a DRep, **I want** to generate a registration certificate **so that** the system recognizes my credentials and counts my votes on governance actions. |
|[![Status Badge][CIP022-badge]][CIP022-link]| Vote delegation certificate | **As** an Ada holder, **I want** to generate a vote delegation certificate **so that** I can delegate my voting rights. |
|[![Status Badge][CIP023-badge]][CIP023-link]| DRep retirement certificate | **As** a DRep, **I want** to generate a retirement certificate **so that** the system and Ada holders (delegators) know that I’m no longer voting on governance actions and they should redelegate. |

[success-badge]: https://img.shields.io/badge/success-green
[failure-badge]: https://img.shields.io/badge/failure-red
[uncovered-badge]: https://img.shields.io/badge/uncovered-grey
[CLI01-badge]: https://img.shields.io/badge/CLI01-grey
[CLI02-badge]: https://img.shields.io/badge/CLI02-grey
[CLI03-badge]: https://img.shields.io/badge/CLI03-grey
[CLI04-badge]: https://img.shields.io/badge/CLI04-grey
[CLI05-badge]: https://img.shields.io/badge/CLI05-grey
[CLI06-badge]: https://img.shields.io/badge/CLI06-grey
[CLI07-badge]: https://img.shields.io/badge/CLI07-grey
[CLI08-badge]: https://img.shields.io/badge/CLI08-grey
[CLI09-badge]: https://img.shields.io/badge/CLI09-grey
[CLI10-badge]: https://img.shields.io/badge/CLI10-grey
[CLI11-badge]: https://img.shields.io/badge/CLI11-grey
[CLI12-badge]: https://img.shields.io/badge/CLI12-grey
[CLI13-badge]: https://img.shields.io/badge/CLI13-grey
[CLI14-badge]: https://img.shields.io/badge/CLI14-grey
[CLI15-badge]: https://img.shields.io/badge/CLI15-grey
[CLI16-badge]: https://img.shields.io/badge/CLI16-grey
[CLI17-badge]: https://img.shields.io/badge/CLI17-grey
[CLI18-badge]: https://img.shields.io/badge/CLI18-grey
[CLI19-badge]: https://img.shields.io/badge/CLI19-grey
[CLI20-badge]: https://img.shields.io/badge/CLI20-grey
[CLI21-badge]: https://img.shields.io/badge/CLI21-grey
[CLI22-badge]: https://img.shields.io/badge/CLI22-grey
[CLI23-badge]: https://img.shields.io/badge/CLI23-grey
[CLI24-badge]: https://img.shields.io/badge/CLI24-grey
[CLI25-badge]: https://img.shields.io/badge/CLI25-grey
[CLI26-badge]: https://img.shields.io/badge/CLI26-grey
[CLI27-badge]: https://img.shields.io/badge/CLI27-grey
[CLI28-badge]: https://img.shields.io/badge/CLI28-grey
[CLI29-badge]: https://img.shields.io/badge/CLI29-grey
[CLI30-badge]: https://img.shields.io/badge/CLI30-grey
[CLI31-badge]: https://img.shields.io/badge/CLI31-grey
[CLI32-badge]: https://img.shields.io/badge/CLI32-grey
[CLI33-badge]: https://img.shields.io/badge/CLI33-grey
[CLI34-badge]: https://img.shields.io/badge/CLI34-grey
[CLI35-badge]: https://img.shields.io/badge/CLI35-grey
[CLI36-badge]: https://img.shields.io/badge/CLI36-grey
[CLI37-badge]: https://img.shields.io/badge/CLI37-grey
[CLI38-badge]: https://img.shields.io/badge/CLI38-grey
[CLI01-link]: https://github.com/CLI01-404
[CLI02-link]: https://github.com/CLI02-404
[CLI03-link]: https://github.com/CLI03-404
[CLI04-link]: https://github.com/CLI04-404
[CLI05-link]: https://github.com/CLI05-404
[CLI06-link]: https://github.com/CLI06-404
[CLI07-link]: https://github.com/CLI07-404
[CLI08-link]: https://github.com/CLI08-404
[CLI09-link]: https://github.com/CLI09-404
[CLI10-link]: https://github.com/CLI10-404
[CLI11-link]: https://github.com/CLI11-404
[CLI12-link]: https://github.com/CLI12-404
[CLI13-link]: https://github.com/CLI13-404
[CLI14-link]: https://github.com/CLI14-404
[CLI15-link]: https://github.com/CLI15-404
[CLI16-link]: https://github.com/CLI16-404
[CLI17-link]: https://github.com/CLI17-404
[CLI18-link]: https://github.com/CLI18-404
[CLI19-link]: https://github.com/CLI19-404
[CLI20-link]: https://github.com/CLI20-404
[CLI21-link]: https://github.com/CLI21-404
[CLI22-link]: https://github.com/CLI22-404
[CLI23-link]: https://github.com/CLI23-404
[CLI24-link]: https://github.com/CLI24-404
[CLI25-link]: https://github.com/CLI25-404
[CLI26-link]: https://github.com/CLI26-404
[CLI27-link]: https://github.com/CLI27-404
[CLI28-link]: https://github.com/CLI28-404
[CLI29-link]: https://github.com/CLI29-404
[CLI30-link]: https://github.com/CLI30-404
[CLI31-link]: https://github.com/CLI31-404
[CLI32-link]: https://github.com/CLI32-404
[CLI33-link]: https://github.com/CLI33-404
[CLI34-link]: https://github.com/CLI34-404
[CLI35-link]: https://github.com/CLI35-404
[CLI36-link]: https://github.com/CLI36-404
[CLI37-link]: https://github.com/CLI37-404
[CLI38-link]: https://github.com/CLI38-404
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
