# README for Dockerized cardano-node Cabal build tests
## Prerequisites
- Docker Engine is installed
- User is in group 'docker'

## Run tests

### To test cardano-node Cabal build on Ubuntu:
`./run-ubuntu.sh <cardano-node tag>`

### To test cardano-node Cabal build on Fedora:

`./run-fedora.sh <cardano-node tag>`

If "Success" is shown at the end and exit with code 0 then build has completed successfully and verified with cardano-cli.
