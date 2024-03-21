Governance testing in Conway
============================


Committee
---------

 ``Register a potential CC Member``

* Verify that is possible to register anyone with a CC key authorization record
* Verify that the member is marked on-chain as an authorized member
* Verify that expired members should not be elected

 ``Willingly resign a member early``

* Verify that the member is marked on-chain as an expired member
* Verify that the expired member can no longer vote
* Verify that after resignation, the existing votes no longer counts for actions that were not ratified yet

 ``Create/update Committee``

* Verify that the data needed for creating the action includes:

  * The set of verification key hash digests for members to be removed
  * A map of verification key hash digests to epoch numbers for new members - and their term limit in epochs.
  * A quorum of the committee necessary for a successful vote
  * An anchor pointing to the proposal URL
  * A governance action ID for the most recently enacted Committee action
  * A deposit

* Confirm that approval from both DReps and SPOs is needed - threshold for ratification might be different depending on if the governance is in a state of confidence or a state of no confidence
* Confirm that if the committee disapproves the action, the action is enacted anyway, as committee approval is not needed

 ``Create an empty Committee``

* Check that the `committeeMinSize` pparam is set to 0
* Check that there are none CC members
* Check that is possible to update de Constitution without needing CC members votes

 ``Create state of no-confidence``

* Verify that the data needed for creating the action includes:

  * An anchor pointing to the proposal URL
  * A governance action ID for the most recently enacted Committee action
  * A deposit

* Confirm that approval from both DReps and SPOs is required
* Confirm that the current committee can no longer participate in governance actions
* Verify that no actions can be ratified before the committee is replaced

 ``Update size of the Constitutional Committee``

* Check that the CC size `committeeMinSize` could be any non-negative number.
* Check that the system will automatically enter a state of no-confidence when the number of active CC members < committeeMinSize
* Check that only update-committee actions can be ratified when the number of active CC members < committeeMinSize

 ``Update Committee threshold - quorum``

* Confirm that is possible to change the fraction of committee required to ratify a government action
* After the action is enacted, verify that the new threshold is enforced:

  * Approve action: If "Yes" votes are above the new threshold the action is approved
  * Disapprove action: If "No" votes exceeds the committee threshold, the action is disapproved
  * Check that members who abstained are not considered as "No" votes

 ``Set Committee threshold to zero``

* Confirm that is possible to change the Committee threshold to 0
* Check that is possible to ratify an action that requires CC approval without CC votes.

DReps
-----

 ``Register a DRep and delegate stake to it``

* Verify that DRep registration certificates include:

  * A DRep ID
  * A deposit

* Confirm that an anchor is optional for a DRep registration certificate
* Confirm that DRep registration is correctly recorded in the ledger state
* Verify that Vote delegation certificates include:

  * The DRep ID for stake delegation
  * The stake credential for the delegator

 ``Delegate a stake credential to an "Abstain" DRep``

* Ensure that the stake is clearly marked as inactive in governance participation
* Verify that votes proportional to the delegated Lovelace to the "Abstain" DRep are not included in the "active voting stake."
* Confirm that the stake is registered for incentive purposes

 ``Delegate a stake credential to a "No Confidence" DRep``

* Confirm that the stake is counted as a "Yes" vote on every "No Confidence" action
* Verify that the stake is counted as a "No" vote on actions other than "No Confidence"

 ``Miss votes for `drepActivity` during many epochs``

* Verify that DReps become inactive when they miss votes during many epochs
* Verify that DReps do not become inactive when there are no actions to vote for
* Confirm that DReps' inactivity is postponed for every epoch where there are no actions to vote for
* Ensure that inactive DReps no longer contribute to the active voting stake
* Verify that inactive DReps can become active again for `drepActivity`-many epochs by voting on any governance actions

 ``Retire a DRep``

* Verify that the DRep retirement certificates include a DRep ID
* Confirm that a DRep is retired immediately upon the chain accepting a retirement certificate
* Ensure that the deposit is returned as part of the transaction that submits the retirement certificate

 ``Verify that an Ada holder can switch between DReps by re-delegating their associated stake credential``

* Example Scenario:

  * Create an "Update to the Constitution" action
  * Approve the action by the committee
  * Vote "Yes" by DReps that collectively have enough delegated stake for their votes to approve the action
  * Vote "No" by DReps that don't have enough combined delegated stake for their votes to disapprove the action
  * Before the end of the current epoch, re-delegate stake to one of the DReps that voted "No," where the stake amount is sufficient to reverse the voting ratio to "No" votes
  * Confirm that the action was not ratified

 ``Check that delegated stake does not count towards the active voting stake for inactive DReps.``


Constitution
------------

 ``Propose an Update to the Constitution and vote in a way that ensures the action is ratified``

* Verify that the necessary data for creating the action includes:

  * An anchor pointing to the updated Constitution URL
  * An anchor pointing to the proposal URL
  * A governance action ID for the most recently enacted "Update to the Constitution" action
  * A deposit

* Confirm that the new constitution replaces the old one at the next epoch boundary (action is enacted)
* Confirm that a minimum of CommitteeThreshold members must approve the Governance action
* Confirm that DRep votes have to be >= than DrepVotingThreshold for UpdateToConstitution as a percentage of active voting stake.
* Verify that approval from SPOs is not necessary

 ``Propose an Update to the Constitution and vote in a way that ensures the action expires``

* Confirm that the proposed constitution is discarded, and the old constitution remains valid at the end of the current epoch

 ``Attempt to create an "Update to the Constitution" action with a deposit amount below the minimum required``

* Verify that the attempt fails due to an insufficient deposit amount

 ``Create an "Update to the Constitution" action where the deposit amount is spread across multiple TxIns``

* Verify that no errors occur

 ``Create an "Update to the Constitution" action where the deposit TxIn also contains non-Ada value``

* Verify that no errors occur

 ``Create multiple "Update to the Constitution" actions in a single epoch, and vote in a way that all actions are approved``

* Confirm that the action submitted first is ratified and enacted
* Verify that the remaining actions are dropped


Protocol Parameter
------------------

 ``Propose an action to change the Protocol Parameters, and vote in a way that ensures the action is ratified``

* Verify that the necessary data for creating the action includes:

  * An anchor pointing to the proposal URL
  * A governance action ID for the most recently enacted "Protocol Parameters Update" action
  * A deposit
  * The changed parameter

* Check that is possible to change multiple Protocol Parameters belonging to multiple Protocol Parameter groups in the same action
* Confirm that the thresholds of all the involved groups applies to any such governance action
* Confirm that approval from the Committee and DReps is required
* Check that SPOs cannot vote on a "protocol parameters update"


 ``Propose an action to change a Protocol Parameter, and vote in a way that ensures the action expires``

* Confirm that the proposed Protocol Parameter is discarded, and the old one remains valid


Treasury Withdrawals
--------------------

 ``Vote on Treasury Withdrawals to ensure the action is ratified``

* Verify that the necessary data for creating the action includes:

  * An anchor pointing to the proposal URL
  * A deposit
  * A map from stake credentials to a positive number of Lovelace

* Confirm that a minimum of CommitteeThreshold members must approve the Governance action
* Confirm that DRep votes have to be >= than DrepVotingThreshold for TreasuryWithdrawal as a percentage of active voting stake.
* Verify that approval from SPOs is not necessary
* Confirm that the exact amount is withdrawn to credential's rewards account after the action is enacted
* Confirm that multiple Treasury Withdrawals actions can be enacted in the same epoch
* Confirm that multiple Treasury Withdrawals to same credential yields the combined sum of the individual withdrawal amounts

 ``Vote on Treasury Withdrawals in a way that ensures the action expires``

* Verify that no funds are withdrawn

 ``Try to withdraw funds from the treasury using MIR certificates``

* Verify that the attempt fails, the only way to withdraw funds from the treasury is through a ratified Treasury Withdrawal governance action


Info
---------

 ``Vote on Info in a way that ensures the action expires``

* Verify that the necessary data for creating the action includes:

  * An anchor pointing to the proposal URL
  * A deposit

 ``Vote on Info in a way that ensures the action is ratified``

* Confirm that approval from the committee is needed, as well as 100% approval from both DReps and SPOs to ratify the action



Hard-fork
-------------------
 ``Hard-fork initiation``

* Confirm that a minimum of CommitteeThreshold members must approve the Governance action
* Confirm that DRep votes have to be >= than DrepVotingThreshold as a percentage of active voting stake.
* Confirm that SPO votes have to be >= than PoolVotingThreshold as a percentage of the total delegated active stake for the epoch.


Votes
-----

 ``As DRep``

* Confirm that the number of votes is proportional to the delegated Lovelace to the DRep
* Ensure that correctly submitted votes take precedence over any older votes for the same credential
* Vote "Yes", "No", "Abstain"
* Confirm that a missed vote is treated like a "No" vote

 ``As SPO``

* Confirm that the number of votes is proportional to the delegated Lovelace to the DRep
* Ensure that correctly submitted votes take precedence over any older votes for the same credential
* Vote "Yes", "No", "Abstain"
* Verify that a block-producing node does not need to be online or actively creating blocks - having stake delegated to the pool is sufficient for the votes to count

 ``As Commitee``

* Verify that each current member has a single vote
* Ensure that votes from retired/expired committee members do not count
* Ensure that correctly submitted votes take precedence over any older votes for the same credential

 ``Vote on a ratified action``

* Confirm that it is possible to vote on an ratified action but the votes will not count

 ``Vote on an enacted action``

* Confirm that it is not possible to vote on an action that has already been enacted, the action was already removed

 ``Test that changes to stake distribution affect past votes``

* Have DReps vote in a way that the "Yes" votes don't meet the threshold for ratification in the current epoch
* In the next epoch, delegate more stake to the DReps that voted "Yes" so the threshold for ratification is met and the action gets ratified in the current epoch without any changes to votes


Ratification
------------

 ``Delay ratification``

* Check that a successful motion of no-confidence delays the ratification of all other governance actions until the first epoch after enactment of the action
* Check that the election of a new constitutional committee delays the ratification of all other governance actions until the first epoch after enactment of the action
* Check that a constitutional change delays the ratification of all other governance actions until the first epoch after enactment of the action


 ``Check that governance action is not ratified when it includes a governance action ID that doesn't match the most recently enacted action of its given type``

* Scenario 1:

  * Create an action that includes a governance action ID of an enacted action of different type

* Scenario 2:

  * Create an action that includes a governance action ID of an enacted action of the same type, but that is not the most recently enacted action

* Scenario 3:

  * Create an action that includes a governance action ID that doesn't exist on chain


Enactment
---------

 ``Check that two actions of the same type can be enacted simultaneously``

* Scenario 1:

  * Create a first "Update to Protocol Parameter" action
  * Create a second "Update to Protocol Parameter" action that includes the governance action ID of the first action as the most recently enacted action
  * Approve the first action by the committee
  * Approve the first action by DReps
  * Approve the second action by the committee
  * Approve the second action by DReps
  * Confirm that both actions are enacted at the end of next epoch

* Scenario 2:

  * Create a first "Update to Protocol Parameter" action
  * Create a second "Update to Protocol Parameter" action that includes the governance action ID of the first action as the most recently enacted action
  * Approve the first action by the committee
  * Disapprove the first action by DReps
  * Approve the second action by the committee
  * Approve the second action by DReps
  * Confirm that neither action is ratified


 ``Check that governance actions of the same type are enacted in the order of acceptance to the chain``

* Create a first "Update to Protocol Parameter" action
* Create a second "Update to Protocol Parameter" action
* Approve the first action by the committee
* Approve the first action by DReps
* Approve the second action by the committee
* Approve the second action by DReps
* Confirm that the first action is enacted and the second action expires


 ``Governance action enactment prioritization``

* Check that the actions that have been ratified in the current epoch are prioritized for enactment in the following order:

  * Motion of no-confidence
  * New committee/threshold
  * Update to the Constitution or proposal policy
  * Hard Fork initiation
  * Protocol parameter changes
  * Treasury withdrawals
  * Info


Transactions
------------

 ``Use both `cardano-cli conway transaction build` and `cardano-cli conway transaction build-raw` commands to build transactions that``

* create a governance action
* vote for a governance action
* register DRep
* deregister (retire) DRep

 ``Use both `cardano-cli transaction submit` and the Submit API service to submit transactions that``

* create a governance action
* vote for a governance action
* register DRep
* deregister (retire) DRep


Bootstrapping phase
-------------------

 ``Verify that an Ada holder is earning rewards (incentives) for delegating to a DRep``

* Confirm that during a short bootstrapping phase, rewards earned for stake delegation, etc., may be withdrawn at any time. After this phase, although rewards will continue to be earned for block delegation, etc., reward accounts will be blocked from withdrawing any rewards unless their associated stake credential is also delegated to a DRep.

 ``Verify that on bootstrap period only hard fork, protocol parameter changes and info actions are allowed.``
