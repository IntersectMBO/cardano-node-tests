# README for Dockerized cardano-node Cabal build tests

## Prerequisites

Podman or Docker are installed on the system.

When using Docker:

- Docker Engine is installed
- User is in group 'docker'

## Run tests

In the following description, "tag" means tagged release (e.g. "8.0.0"), not an arbitrary git tag.

The "commit" can be any type of reference, like commit hash, tag or branch name.

### To test cardano-node Cabal build on Ubuntu

`./run-ubuntu.sh -t tag -o <cardano-node tag>`

`./run-ubuntu.sh -t commit -o <cardano-node commit>`

### To test cardano-node Cabal build on Fedora

`./run-fedora.sh -t tag -o <cardano-node tag>`

`./run-fedora.sh -t commit -o <cardano-node commit>`

If "Success" is shown at the end and the command exits with code 0, then build has completed successfully.
