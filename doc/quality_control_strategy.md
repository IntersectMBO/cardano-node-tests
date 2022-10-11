# Quality control strategy

## Scope and objectives

The purpose of this document is to explain the steps that happen during the quality control (QC) phase. This ensures high-quality delivery of node and hard fork releases.

This document also clarifies QC activities, roles, responsibilities, processes, and practices to be used within the Cardano project.

![img.png](https://input-output-hk.github.io/cardano-node-tests/_static/images/qa_process.png)

## Key principles

- **Prevention instead of reaction**: preventing a problem is always better than fixing it.
- **Work with the team, not against it**: collaboration is key to success.
- **Be in users’ shoes**: always focus on product end users. User experience is the top priority since the software is developed for end users specifically.
- **Automation first approach**: all tests should be automated.

## Quality control approach

Quality control takes place during the SDLC development and launch phases.

It is recommended to create and run automated tests as soon as possible, at the component, integration, and system test levels.
For this reason, it is recommended to use the shift left testing approach. This approach is based on the principle that if the software development team can test code early in the development cycle, they can discover errors before the code is released for testing.

## Quality control methodology

The quality control process is multi-dimensional:

### Active quality control

#### During the pre-development SDLC phase

- **Acceptance criteria**, **user stories** and **definition of done** should be created by the product owner, then peer-reviewed and signed off by PM, dev, and test owners
- Each user-facing functionality change should go through the product owner and be planned accordingly

#### During the development SDLC phase

- Development work is started based on the acceptance criteria
- Each new piece of code is covered by tests at the dev/unit/component level (for functional and non-functional, positive and negative scenarios – based on the acceptance criteria); these tests are part of the PR when merged into master
- The code review of the PR also includes the review of the new test coverage for the newly added code
- Each PR with user-facing functionality is passed to the system test team, along with a clear usage/documentation/example
  to be tested and automated (for functional and non-functional, positive and negative scenarios – based on the acceptance criteria)
- Test scenarios proposed by the system test team are peer-reviewed and signed off by PO, PM, and Dev owners
- DevOps and DevX support the development process by executing the required updates to the different environments and by monitoring
  the environments for possible errors
- All the configurations on test environments are similar to the mainnet ones  (when a parameter value is changed on mainnet,
  it is updated on all the environments – eg, `maxTxExecutionUnits`)
- The system test team runs frequent sync tests on all available environments to find possible regressions in sync speed,
  sync time, RAM & CPU usage, and disk space usage
- Benchmarking teams run frequent benchmarks in order to find any possible regressions in the core blockchain functionality
- All the integration tests (at the node level) owned by dev teams are run automatically on a nightly basis
- TBD - all the system tests (that run on a local cluster) to be run at the PR level in the `cardano-node` repository
- All the integration/system/E2E tests owned by the system test team are run automatically on a nightly basis
- When all the features in the release scope are fully developed, fully tested, and covered by automated tests at
  component/integration/system levels, are fully implemented/tested (as per acceptance criteria & DoD), and have proper documentation,
  a new node tag is created
- Depending on the release scope, there might be some audits (internal or external) required – eg, code, security, legal
- User acceptance testing (UAT) is done by involving different external teams/projects and community members
  (SPOs, DApp developers, other possible users, external contractors, etc)
- DevOps and DevX support UAT by executing the required updates to the different environments (making sure there are mixed
  node versions used on at least one of the internal environments) and by monitoring the environments for possible errors
- Test summary is generated and shared with delivery, stakeholders, and the community

**TBD**: define what the test summary should include

### Passive quality control

#### During the development and maintenance SDLC phases

- Longer-term active monitoring of the different environments through Grafana dashboards
- Longer-term active monitoring of the external environment with multiple stake pools and different node versions
- Constant monitoring/grooming/prioritization of the issues raised by internal teams using official channels → GitHub Issues
- Constant monitoring/grooming/prioritization of the issues raised by the community using different mediums (GitHub, Discord, etc)
