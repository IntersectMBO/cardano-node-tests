# Contributing to the Cardano node tests repository

Community contributions are highly appreciated. Below you will find a set of guidelines for contributing to this repository.

Note that these are guidelines and tips rather than rules. Use your best judgment, and feel free to propose changes to this document by opening a pull request.

## How can I contribute?

### Reporting an issue

Following these guidelines helps maintainers and the community understand your report, reproduce the behavior, and find related reports.

Before creating a bug report, please check [this list of existing issues](https://github.com/input-output-hk/cardano-node-tests/issues) to see if a similar report already exists. When creating a bug report, [include as many details as possible](#How do I submit a (good) issue report).

> **Note:** if you find a **closed** issue, which addresses the problems you're experiencing, open a new issue and include a link to the closed one.

#### How do I submit a (good) issue report?

Explain the problem and include additional details to help maintainers reproduce the problem:

- Use a **clear and descriptive title** so that the issue identifies the problem.
- Describe the **exact steps that reproduce the problem** in as much detail as possible. When listing steps, don't just say what you did but explain **how you did it**.
- **Provide specific examples**. Include links to files, GitHub projects, or copy-pastable snippets used in those examples. When adding snippets to the issue, use [Markdown code blocks](https://help.github.com/articles/markdown-basics/#multiple-lines).
- **Describe the behavior** you observed after following the steps and point out the exact problem with this behavior.
- Explain the behavior you **expected to see instead** and provide reasoning.
- Specify cardano-node and cardano-node-tests **versions** you are using.

### Your first code contribution

#### Pull requests

This process has several goals:

- Maintain the quality of cardano-node-tests
- Fix important issues
- Engage the community in producing the best possible Cardano system tests
- Enable a sustainable system for maintainers to review contributions

Please follow these steps to have your contribution considered by the maintainers:

1. Follow all instructions in the template (TO DO: to be added later)
2. Follow the [style guides](#style guides)
3. After submitting your pull request, verify that all [status checks](https://help.github.com/articles/about-status-checks/) have passed. If a status check is failing, and you believe the failure is unrelated to your change, comment on the pull request explaining why you believe it is unrelated. A maintainer will re-run the status check for you. If we conclude that the failure was a false positive, we will open an issue to track that problem with our status check suite.

While the above prerequisites must be satisfied prior to the pull request review, reviewer(s) may ask you to complete additional design work, tests, or other changes before the pull request acceptance.

## Style guides

### Git commit messages

- Use the present tense ('Add feature' instead of 'Added feature')
- Use the imperative mood ('Move the cursor to...' instead of 'Moves the cursor to...')
- Limit the first line to 72 characters or less
- Reference issues and pull requests liberally after the first line

### Python style guide

Run `pre-commit install` to set up git hook scripts that will check your changes before every commit. Alternatively, run `make lint` manually before pushing your changes.

Follow the Google Python style guide but note that formatting is handled automatically by Black (through `pre-commit` command).

## Roles and Responsibilities

We maintain a [CODEOWNERS file](https://github.com/input-output-hk/cardano-node-tests/blob/master/.github/CODEOWNERS) which provides information who should review your code if it touches given projects.  Note that you need to get approvals from all code owners (even though GitHub doesn't give a way to enforce it).
