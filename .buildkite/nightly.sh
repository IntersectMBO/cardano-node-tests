#! /usr/bin/env nix-shell
#! nix-shell -i bash -p niv nix gnumake gnutar coreutils adoptopenjdk-jre-bin curl git
#! nix-shell -I nixpkgs=./nix
# shellcheck shell=bash

set -xeuo pipefail

REPODIR="$PWD"

WORKDIR="/scratch/workdir"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

# update to latest cardano-node
niv update

pushd "$WORKDIR"

# install Allure
mkdir allure_install
curl -sfLo \
  allure_install/allure.tgz \
  "https://repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/2.13.9/allure-commandline-2.13.9.tgz"
tar xzf allure_install/allure.tgz -C allure_install
export PATH="$PWD/allure_install/allure-2.13.9/bin:$PATH"

cd "$REPODIR"

# run tests and generate report
set +e
# shellcheck disable=SC2016
nix-shell --run \
  'SCHEDULING_LOG=scheduling.log CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI" CI_ARGS="--html=nightly-report.html --self-contained-html" make tests; retval="$?"; ./.buildkite/report.sh .; exit "$retval"'
retval="$?"

echo
echo "Dir content:"
ls -1

exit "$retval"
