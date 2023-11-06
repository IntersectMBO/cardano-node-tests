# Plutus

## Summary

Since transitioning into the Alonzo era, Cardano has incorporated Plutus functionality, enabling users to flexibly define transaction validation rules. Plutus is exposed to a subset of transaction information and provides access to a range of operations and built-in functions that can be utilised for validation.

## Scope

The purpose of this document is to outline the process for conducting  system testing on the Plutus Core language and PlutusTx library for major releases of [cardano-node](https://github.com/input-output-hk/cardano-node). The goal is to comprehensively assess essential Plutus Core functionality, encompassing transaction information exposed via  [TxInfo](https://input-output-hk.github.io/plutus/master/plutus-ledger-api/html/PlutusLedgerApi-V3-Contexts.html#t:TxInfo) to builtin cryptographic primitives such as [bls12-381](https://github.com/input-output-hk/plutus/pull/5231).

## Not in scope

- Specific tests to cover (see test plan)
- Low-level testing of Plutus Core
- Performance testing or benchmarking

## Automation

### Antaeus

Antaeus serves as an end-to-end test framework, primarily dedicated to testing [plutus-core](https://github.com/input-output-hk/plutus) once integrated with [cardano-node](https://github.com/input-output-hk/cardano-node). It leverages [plutus-tx](https://github.com/input-output-hk/plutus/tree/master/plutus-tx) to define and compile scripts, which are evaluated both locally and on-chain when submitted to private or public testnets. In establishing a private testnet, Antaeus relies on [cardano-testnet](https://github.com/input-output-hk/cardano-node/tree/master/cardano-testnet), and for building, submitting transactions, and querying the ledger state for events to assert, it uses [cardano-api](https://github.com/input-output-hk/cardano-api).

Antaeus offers the advantage of direct script definition and compilations, granting greater control over the scripts in use. Additionally, it doesn’t rely on core components such as [cardano-cli](https://github.com/input-output-hk/cardano-cli). Consequently, it enables the definition of tests at an earlier stage in the SDLC. This quality positions Antaeus as a suitable choice for fulfilling all Plutus end-to-end testing requirements for each supported language version.

Antaeus will cover testing of each ledger language version (e.g. PlutusV3) within every supported protocol version and era (E.g. protocol version 9 in Conway era), as well as each Plutus Core compiler version (e.g. 1.1.0). Tests will be introduced to cover new attributes within the [ScriptContext](https://input-output-hk.github.io/plutus/master/plutus-ledger-api/html/PlutusLedgerApi-V3.html#t:ScriptContext), including freshly exposed transaction information via [TxInfo](https://input-output-hk.github.io/plutus/master/plutus-ledger-api/html/PlutusLedgerApi-V3-Contexts.html#t:TxInfo). Moreover, it will cover new built-in functions, with a priority on higher-risk functionality like cryptographic primitives. The inclusion of tests for certain existing built-in functions, like hashing functions, will be considered necessary based on effort and risk, while commonly used built-in functions may not necessitate dedicated test coverage.

### Cardano-node-tests

Incorporating Plutus scripts into a limited subset of tests within the [cardano-node-tests](https://github.com/input-output-hk/cardano-node-tests) suite offers notable advantages. These tests are defined with slight variations, cover additional components such as [cardano-cli](https://github.com/input-output-hk/cardano-cli), and executed in more public network environments, thereby enhancing coverage and assurance. This framework lacks native script compilation capability, necessitating the provision of scripts in the CLI's text envelope format for each language version.


## Exploratory Testing

Automated end-to-end tests provide a good level of assurance for component integration and core functionality. However, due to their complexity and longer execution duration, they are inadequate in encompassing all possible scenarios. In the context of intricate software functionality, it becomes imperative to assess the system through diverse methods, such as exploratory testing.

Test engineers will typically allocate time to exploratory testing. Although, other business stakeholders and community members are encouraged to participate and employ varied approaches when engaging with the application. This broader exploration of paths and combinations enhances the probability of identifying bugs or uncovering areas in need of improvement.

Incorporating exploratory testing into the test plan is essential for every major cardano-node release introducing new functionality to support a hard fork.
