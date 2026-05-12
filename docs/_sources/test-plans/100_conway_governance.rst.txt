Governance testing in Conway
============================

Committee
---------

* Approve an action as a committee - when the number of committee members voting "Yes" exceeds the committee threshold

  * Verify that each current member has a single vote
  * Ensure that votes from retired committee members do not count
  * Confirm that correctly submitted votes take precedence over any older votes for the same credential and role

* Disapprove an action as a committee

  * Check that when the number of committee members voting "No" or not voting at all exceeds the committee threshold, the action will expire

* Test that votes of committee members who abstained are not considered as "No" votes

  * Scenario: Two members of the committee vote "Yes," one member does not vote at all, and the remaining members vote "Abstain" (the "Yes" votes need to exceed the committee threshold)
  * Verify that the committee approved the action

* Vote on a motion of no-confidence

  * Verify that the data needed for creating the action includes:

    * An anchor pointing to the proposal URL
    * A governance action ID for the most recently enacted "New committee" action
    * A deposit

  * Confirm that approval from both DReps and SPOs is required
  * Ensure that the deposit is returned as soon as the action is ratified or expires
  * Confirm that the current committee can no longer participate in governance actions
  * Verify that no actions can be ratified before the committee is replaced
  * Confirm that if the committee disapproves the action, the action is enacted anyway, as committee approval is not needed
  * Verify that the action is not enacted if it expires

* Vote on new constitutional committee

  * Verify that the data needed for creating the action includes:

    * The set of verification key hash digests (members to be removed)
    * A map of verification key hash digests to epoch numbers (new members and their term limit)
    * A quorum of the committee necessary for a successful vote
    * An anchor pointing to the proposal URL
    * A governance action ID for the most recently enacted "New Committee" action
    * A deposit

  * Confirm that approval from both DReps and SPOs is needed - threshold for ratification might be different depending on if the governance is in a state of confidence or a state of no confidence
  * Confirm that if the committee disapproves the action, the action is enacted anyway, as committee approval is not needed

* Vote on new threshold

  * Confirm that if the committee disapproves the action, the action is enacted anyway, as committee approval is not needed
  * After the action is enacted, verify that the new threshold is enforced: "Yes" votes are below the new threshold and the action is not approved

* Vote on new committee members' terms

  * Confirm that if the committee disapproves the action, the action is enacted anyway, as committee approval is not needed
  * After the action is enacted, verify that expired members can no longer vote

* Create an empty committee

  * Verify that the committee no longer needs to approve actions
  * Attempt to set the threshold for an empty committee

* Willingly resign a member early

  * Verify that the member is marked on-chain as an expired member
  * Verify that the expired member can no longer vote
  * Verify that after resignation, the existing votes no longer counts for actions that were not ratified yet

* To ensure compliance with the committee's minimum size requirement, allow a sufficient number of members' terms to expire, resulting in a total count of non-expired members below the specified threshold

  * Scenario: a committee of size five with a threshold of 3/5, a minimum size of three and two expired members can still pass governance actions if two non-expired members vote "Yes".
    If one more member expires then the system enters a state of no-confidence, since the two remaining members are not enough to meet quorum.


DReps
-----

* Delegate a stake credential to an "Abstain" DRep

  * Ensure that the stake is clearly marked as inactive in governance participation
  * Verify that votes proportional to the delegated Lovelace to the "Abstain" DRep are not included in the "active voting stake."
  * Confirm that the stake is registered for incentive purposes

* Delegate a stake credential to a "No Confidence" DRep

  * Confirm that the stake is counted as a "Yes" vote on every "No Confidence" action
  * Verify that the stake is counted as a "No" vote on actions other than "No Confidence"

* Register a DRep and delegate stake to it

  * Verify that DRep registration certificates include:

    * A DRep ID
    * A deposit

  * Confirm that an anchor is optional for a DRep registration certificate
  * Confirm that DRep registration is correctly recorded in the ledger state
  * Verify that Vote delegation certificates include:

    * The DRep ID for stake delegation
    * The stake credential for the delegator

  * Vote "Yes" as a DRep

    * Confirm that the number of votes is proportional to the delegated Lovelace to the DRep
    * Ensure that correctly submitted votes take precedence over any older votes for the same credential and role

  * Vote "No" as a DRep

    * Confirm that the number of votes is proportional to the delegated Lovelace to the DRep

  * Miss a vote as a DRep

    * Confirm that the number of votes is proportional to the delegated Lovelace to the DRep, and the vote acts as a "No" vote

  * Vote "Abstain" as a DRep

    * Example Scenario: One DRep votes "Abstain," the rest of DReps vote "Yes" or "No," and no stake is delegated to the predefined Abstain DRep
    * Verify that votes proportionate to the Lovelace delegated to the abstaining DRep are not included in the "active voting stake."

  * Miss votes for `drepActivity`-many epochs and confirm that the DRep becomes inactive

    * Verify that DReps do not become inactive when there are no actions to vote for
    * Confirm that DReps' inactivity is postponed for every epoch where there are no actions to vote for
    * Ensure that inactive DReps no longer contribute to the active voting stake
    * Verify that inactive DReps can become active again for `drepActivity`-many epochs by voting on any governance actions

  * Retire a DRep

    * Verify that the DRep retirement certificates include a DRep ID
    * Confirm that a DRep is retired immediately upon the chain accepting a retirement certificate
    * Ensure that the deposit is returned as part of the transaction that submits the retirement certificate

* Verify that an Ada holder is earning rewards (incentives) for delegating to a DRep

  * Confirm that during a short bootstrapping phase, rewards earned for stake delegation, etc., may be withdrawn at any time. After this phase, although rewards will continue to be earned for block delegation, etc., reward accounts will be blocked from withdrawing any rewards unless their associated stake credential is also delegated to a DRep. (Question: how long is the bootstrapping phase?)

* Verify that an Ada holder can switch between DReps by re-delegating their associated stake credential

  * Example Scenario:

    * Create an "Update to the Constitution" action
    * Approve the action by the committee
    * Vote "Yes" by DReps that collectively have enough delegated stake for their votes to approve the action
    * Vote "No" by DReps that don't have enough combined delegated stake for their votes to disapprove the action
    * Before the end of the current epoch, re-delegate stake to one of the DReps that voted "No," where the stake amount is sufficient to reverse the voting ratio to "No" votes
    * Confirm that the action was not ratified

Actions, Voting, Ratification and Enactment
-------------------------------------------

* Propose an Update to the Constitution and vote in a way that ensures the action is ratified

  * Verify that the necessary data for creating the action includes:

    * An anchor pointing to the updated Constitution URL
    * An anchor pointing to the proposal URL
    * A governance action ID for the most recently enacted "Update to the Constitution" action
    * A deposit

  * Confirm that the new constitution replaces the old one at the next epoch boundary (action is enacted)
  * Verify that approval from the committee and DReps is required, and approval from SPOs is not necessary

* Propose an Update to the Constitution and vote in a way that ensures the action expires

  * Confirm that the proposed constitution is discarded, and the old constitution remains valid at the end of the current epoch

* Attempt to create an "Update to the Constitution" action with a deposit amount below the minimum required

  * Verify that the attempt fails due to an insufficient deposit amount

* Create an "Update to the Constitution" action where the deposit amount is spread across multiple TxIns

* Create an "Update to the Constitution" action where the deposit TxIn also contains non-Ada value

* Create multiple "Update to the Constitution" actions in a single epoch, and vote in a way that all actions are approved

  * Confirm that the action submitted first is ratified and enacted
  * Verify that the remaining actions are dropped

* Vote on an "Update to the Constitution" action that has already been enacted

  * Confirm that it is not possible to vote on an action that has already been enacted

* Propose an action to change a single Protocol Parameter, and vote in a way that ensures the action is ratified

  * Verify that the necessary data for creating the action includes:

    * An anchor pointing to the proposal URL
    * A governance action ID for the most recently enacted "Protocol Parameters Update" action
    * A deposit
    * The changed parameter

  * Confirm that approval from the committee and DReps is required, and approval from SPOs is not necessary

* Propose an action to change multiple Protocol Parameters belonging to multiple Protocol Parameter groups, and vote to ensure the action is ratified

  * Verify that the changed parameters are necessary for creating the action
  * Confirm that the maximum threshold of all the involved groups applies to any such governance action

* Propose an action to change a single Protocol Parameter, and vote in a way that ensures the action expires

  * Confirm that the deposit amount is counted towards the stake of the reward address to which it will be paid back

* Propose an action to change multiple Protocol Parameters, and vote in a way that ensures the action expires

* Vote on Treasury Withdrawals to ensure the action is ratified

  * Verify that the necessary data for creating the action includes:

    * An anchor pointing to the proposal URL
    * A deposit
    * A map from stake credentials to a positive number of Lovelace

  * Confirm that approval from the committee and DReps is required, and approval from SPOs is not necessary
  * Confirm that the exact amount is withdrawn to credential's rewards account after the action is enacted
  * Confirm that multiple Treasury Withdrawals actions can be enacted in the same epoch
  * Confirm that multiple Treasury Withdrawals to same credential yields the combined sum of the individual withdrawal amounts

* Vote on Treasury Withdrawals in a way that ensures the action expires

* Vote on Info in a way that ensures the action expires

  * Verify that the necessary data for creating the action includes:

    * An anchor pointing to the proposal URL
    * A deposit

* Vote on Info in a way that ensures the action is ratified

  * Confirm that approval from the committee is needed, as well as 100% approval from both DReps and SPOs to ratify the action
  * Confirm that multiple Info actions can be enacted in the same epoch

* Test that a successful motion of no-confidence delays the ratification of all other governance actions until the first epoch after enactment of the action

* Test that the election of a new constitutional committee delays the ratification of all other governance actions until the first epoch after enactment of the action

* Test that a constitutional change delays the ratification of all other governance actions until the first epoch after enactment of the action

* Test that two actions of the same type can be enacted simultaneously

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

* Test that governance action is not ratified when it includes a governance action ID that doesn't match the most recently enacted action of its given type

  * Scenario 1:

    * Create a "Update to Protocol Parameter" action that includes a governance action ID of an enacted action of different type

  * Scenario 2:

    * Create a "Update to Protocol Parameter" action that includes a governance action ID of an enacted action of the same type, but that is not the most recently enacted action

  * Scenario 3:

    * Create a "Update to Protocol Parameter" action that includes a governance action ID that doesn't exist on chain

* Test that governance actions are enacted in the order of acceptance to the chain

  * Scenario:

    * Create a first "Update to Protocol Parameter" action
    * Create a second "Update to Protocol Parameter" action
    * Approve the first action by the committee
    * Approve the first action by DReps
    * Approve the second action by the committee
    * Approve the second action by DReps
    * Confirm that the first action is enacted and the second action expires

* Test that changes to stake distribution affect past votes

  * Scenario:

    * Create an "Update to Protocol Parameter" action
    * Approve the action by the committee
    * Have DReps vote in a way that the "Yes" votes don't meet the threshold for ratification in the current epoch
    * In the next epoch, delegate more stake to the DReps that voted "Yes" so the threshold for ratification is met and the action gets ratified in the current epoch without any changes to votes

* Query the progress of a governance action

  * Confirm that the following is tracked:

    * The governance action ID
    * The epoch in which the action expires
    * The deposit amount
    * The rewards address that will receive the deposit when it is returned
    * The total 'Yes'/'No'/'Abstain' votes of the constitutional committee for this action
    * The total 'Yes'/'No'/'Abstain' votes of the DReps for this action
    * The total 'Yes'/'No'/'Abstain' votes of the SPOs for this action

SPOs
----

* Cast a "Yes" Vote as an SPO

  * Confirm that the number of votes is proportionate to the Lovelace delegated to the SPO
  * Ensure that correctly submitted votes take precedence over any older votes for the same credential and role
  * Verify that a block-producing node does not need to be online or actively creating blocks - having stake delegated to the pool is sufficient for the votes to count

* Cast a "No" Vote as an SPO

* Cast an "Abstain" Vote as an SPO

Transactions
------------

* Use both `cardano-cli conway transaction build` and `cardano-cli conway transaction build-raw` commands to build transactions that

  * create a governance action
  * vote for a governance action
  * register DRep
  * deregister (retire) DRep

* Use both both `cardano-cli transaction submit` and the Submit API service to submit transactions that

  * create a governance action
  * vote for a governance action
  * register DRep
  * deregister (retire) DRep
