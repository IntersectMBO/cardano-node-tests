Chang HF User Stories Coverage
==============================

**Legend:** ![Success Badge][success-badge] ![Failure Badge][failure-badge] ![Uncovered Badge][uncovered-badge]
&nbsp;


| Status for Story ID | Story Overview |
|---------------------|----------------|
|[![Status Badge][CLI01-badge]][CLI01-link]| **As** an Ada holder, **I want** to obtain the hash of the off-chain text of a Constitution, **so that** I can compare it against the hash registered on-chain to verify its authenticity. |
|[![Status Badge][CLI02-badge]][CLI02-link]| **As** an Ada holder, **I want** to generate the hash of the off-chain text for a proposed Constitution, **so that** the hash can be utilized in a governance action. |
|[![Status Badge][CLI03-badge]][CLI03-link]| **As** a potential Constitutional Committee member, **I want** to generate COLD key pair, **so that** I can be proposed for the Committee in a Governance action. |
|[![Status Badge][CLI04-badge]][CLI04-link]| **As** potential Constitutional Committee member, **I want** to generate HOT key pair, **so that** I can authorise the Hot key to sign votes on behalf of the Cold key. |
|[![Status Badge][CLI05-badge]][CLI05-link]| **As** a Constitutional Committee member, **I want** to issue a authorization certificate from my cold key to a hot key, **so that** I can sign my votes using the hot key and keep the cold key in cold storage and can authorise a new hot key in case the original one is compromised. |
|[![Status Badge][CLI06-badge]][CLI06-link]| **As** a potential Constitutional Committee member, **I want** to generate the key hashes for my cold and hot keys, **so that** they can be used by third parties to propose me as a new Constitutional Committee member and for identification purposes once I've been elected as Constitutional Committee member. |
|[![Status Badge][CLI07-badge]][CLI07-link]| **As** a Constitutional Committee member, **I want** to be able to generate a resignation certificate, **so that** I can submit it to the chain on a transaction to signal to the Ada holders that I'm resigning from my duties as CC member. |
|[![Status Badge][CLI08-badge]][CLI08-link]| **As** an Ada holder, **I want** to generate Ed25519 keys, **so that** I can register as a DRep. |
|[![Status Badge][CLI09-badge]][CLI09-link]| **As** a DRep, **I want** to generate a DRep Id, **so that** Ada holder can use it to delegate their votes to me and my voting record can be tracked. |
|[![Status Badge][CLI10-badge]][CLI10-link]| **As** a DRep, **I want** to generate a DRep registration certificate, **so that** I can submit it on a transaction and the Ada holders can delegate their votes to me. |
|[![Status Badge][CLI11-badge]][CLI11-link]| **As** a DRep, **I want** to generate a DRep retirement (unregistration) certificate, **so that** I can submit it on a transaction and can get my DRep deposit back. |
|[![Status Badge][CLI12-badge]][CLI12-link]| **As** a DRep, **I want** to generate the hash of my DRep metadata, **so that** I can supply it when registering as DRep. |
|[![Status Badge][CLI13-badge]][CLI13-link]| **As** an Ada holder, **I want** to create a governance action that updates the constitution, **so that** it can be submitted to the chain and be voted by the governance bodies. |
|[![Status Badge][CLI14-badge]][CLI14-link]| **As** an Ada holder, **I want** to create a governance action that updates the Constitutional Committee, **so that** it can be submitted to the chain and be voted by the governance bodies. |
|[![Status Badge][CLI15-badge]][CLI15-link]| **As** an Ada holder, **I want** to create a governance action to withdraw funds from the treasury, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-treasury-withdrawal`. |
|[![Status Badge][CLI16-badge]][CLI16-link]| **As** an Ada holder, **I want** to create an info governance action, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-info`. |
|[![Status Badge][CLI17-badge]][CLI17-link]| **As** an Ada holder, **I want** to create a governance action to update protocol parameters, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-protocol-parameters-update`. |
|[![Status Badge][CLI18-badge]][CLI18-link]| **As** an Ada holder, **I want** to create a no-confidence governance action, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-no-confidence`. |
|[![Status Badge][CLI19-badge]][CLI19-link]| **As** an Ada holder, **I want** to create a governance action to initiate a hardfork, **so that** it can be submitted to the chain and be voted by the governance bodies. Command: `cardano-cli conway governance action create-hf-init`. |
|[![Status Badge][CLI20-badge]][CLI20-link]| **As** an Ada holder, **I want** to inspect the contents of a governance action file, **so that** I can verify it is correct before submitting it in a transaction. Command: `cardano-cli conway governance action view`. |
|[![Status Badge][CLI21-badge]][CLI21-link]| **As** a DRep, SPO or CC member, **I want** to create a vote for a governance action, **so that** I can include it in a transaction and submit it to the chain.  Command: `cardano-cli conway governance vote create`. |
|[![Status Badge][CLI22-badge]][CLI22-link]| **As** a DRep, SPO or CC member, **I want** to inspect the contents of a vote file, **so that** I can verify it is correct before submitting it in a transaction. Command: `cardano-cli conway governance vote view`. |
|[![Status Badge][CLI23-badge]][CLI23-link]| **As** an Ada holder, **I want** to build a transaction that includes a proposal (containing a governance action), **so that** I can later sign and submit to the chain. Command: `transaction build`. |
|[![Status Badge][CLI24-badge]][CLI24-link]| **As** a DRep, SPO or CC member, **I want** to build a transaction that includes my vote on a particular governance action, **so that** I can later sign and submit to the chain. Command: `transaction build`. |
|[![Status Badge][CLI25-badge]][CLI25-link]| **As** an Ada holder, **I want** to build a transaction that includes a proposal (containing a governance action), **so that** I can later sign and submit to the chain. Command: `transaction build-raw`. |
|[![Status Badge][CLI26-badge]][CLI26-link]| **As** a DRep, SPO or CC member, **I want** to build a transaction that includes my vote on a particular governance action, **so that** I can later sign and submit to the chain. Command: `transaction build-raw`. |
|[![Status Badge][CLI27-badge]][CLI27-link]| **As** an Ada holder, **I want** to create a conway cddl-compliant stake registration certificate. |
|[![Status Badge][CLI28-badge]][CLI28-link]| **As** an Ada holder, **I want** to create a conway cddl-compliant stake deregistration certificate to get my deposit back. |
|[![Status Badge][CLI29-badge]][CLI29-link]| **As** an Ada holder, **I want** to delegate my votes to a DRep (registered or default), **so that** my stake is counted when the DRep votes. |
|[![Status Badge][CLI30-badge]][CLI30-link]| **As** an Ada holder, **I want** to delegate my stake to a stake pool AND my votes to a DRep (registered or default) with a single certificate. |
|[![Status Badge][CLI31-badge]][CLI31-link]| **As** any persona, **I want** to query the nodes for the currentGovernance state, **so that** I can inform my decisions. |
|[![Status Badge][CLI32-badge]][CLI32-link]| **As** a CC member, **I want** to query the Constitutional Committee state, **so that** I can find my expiration term and whether my hot key authorization certificate has been recorded on chain. |
|[![Status Badge][CLI33-badge]][CLI33-link]| **As** an Ada holder, **I want** to query the DRep state, **so that** I can find detailed information about registered DReps. |
|[![Status Badge][CLI34-badge]][CLI34-link]| **As** an Ada holder and DRep, **I want** to query the DRep stake distribution, **so that** I can find the weight (of the votes) of each DRep. |
|[![Status Badge][CLI35-badge]][CLI35-link]| **As** an Ada holder, **I want** to query my stake address information so that I can learn to which pool and DRep I'm delegating to and the value in lovelace of my deposits for delegating and for submitting governance actions. |
|[![Status Badge][CLI36-badge]][CLI36-link]| Script as a DRep. |

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
