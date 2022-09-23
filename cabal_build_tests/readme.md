# README for Dockerized cardano-node Cabal build tests

## Prerequisites

- Docker Engine is installed
- User is in group 'docker'

## Run tests

### To test cardano-node Cabal build on Ubuntu

`./run-ubuntu.sh -t tag -o <cardano-node tag>`

`./run-ubuntu.sh -t commit -o <cardano-node commit>`

### To test cardano-node Cabal build on Fedora

`./run-fedora.sh  -t tag -o <cardano-node tag>`

`./run-fedora.sh -t commit -o <cardano-node commit>`

If "Success" is shown at the end and exit with code 0 then build has completed successfully and verified with cardano-cli.
