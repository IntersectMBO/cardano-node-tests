### Definition of Done
- [ ] Acceptance Criteria (including User Stories) are created (by PO) and peer-reviewed by Dev & Test 
- [ ] The new code is peer-reviewed (including component/integration level tests (both functional and non-functional scenarios))
- [ ] The test coverage and functionalities are peer-reviewed at the same time as the code-review
- [ ] The new code builds successfully on CI (runs all the tests - component, integration, system level tests)
- [ ] Includes documentation and usage examples (for user-facing functionalities)
- [ ] Log/record changes on Vnext (or similar depending on what we adopt)
- [ ] Test engineer signs-off + system tests fully automated

### Acceptance Criteria

#### Personas:
* **CLI user** (SPO, dApp developer, exchange, wallet, etc)

#### User Story 1
* As a CLI user, I want to auto-balance multi-asset transactions when using the `transaction build` CLI command

##### AC1.1
* The minFee is paid, and the transaction is auto-balanced when creating a transaction with (using the `transaction build` CLI command):
  * only 1 multi-asset
  * with ada and multi-asset
  * with max no of multi-asset (based on block size)
  * with multiple inputs and outputs of all possible types (ada, simple scripts, multi-assets, Plutus scripts, certificates) 

#### User Story 2
* As a CLI user, I don't expect that any of the previously existing functionalities for the `transaction build` CLI command to be changed

##### AC2.1
* all existing automated regression tests should pass