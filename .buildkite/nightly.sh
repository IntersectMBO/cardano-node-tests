#! /usr/bin/env nix-shell
#! nix-shell -i bash -p niv nix gnumake gnutar coreutils adoptopenjdk-jre-bin curl git
#! nix-shell -I nixpkgs=./nix
# shellcheck shell=bash

set -xeuo pipefail

REPODIR="$PWD"

WORKDIR="/scratch/workdir"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

# update cardano-node to specified branch and/or revision, or to the latest available
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/niv_update_cardano_node.sh"

pushd "$WORKDIR"

# install Allure
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/allure_install.sh"

pushd "$REPODIR"

# run tests and generate report
set +e
# shellcheck disable=SC2016
nix-shell --run \
  'SCHEDULING_LOG=scheduling.log CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI" CI_ARGS="--html=testrun-report.html --self-contained-html" make tests; retval="$?"; ./.buildkite/report.sh .; exit "$retval"'
retval="$?"

echo
echo "Dir content:"
ls -1

exit "$retval"
