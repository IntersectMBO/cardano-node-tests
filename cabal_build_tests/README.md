# README for Dockerized cardano-node Cabal build tests

## Prerequisites

Podman or Docker are installed on the system.

When using Docker:

- Docker Engine is installed
- User is in group 'docker'

## Run tests

The "commit" can be any type of reference, like commit hash, tag or branch name.

### To test cardano-node Cabal build on Ubuntu

`./run.sh -d ubuntu -o <cardano-node commit>`

### To test cardano-node Cabal build on Fedora

`./run.sh -d fedora -o <cardano-node commit>`

If "Success" is shown at the end and the command exits with code 0, then build has completed successfully.
