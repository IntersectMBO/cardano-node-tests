#! /usr/bin/env nix-shell
#! nix-shell -i bash -p niv nix gnumake gnutar coreutils adoptopenjdk-jre-bin curl git
#! nix-shell -I nixpkgs=./nix
# shellcheck shell=bash

set -uo pipefail

# install Allure
mkdir -p .allure
curl -sfLo \
  .allure/allure.tgz \
  "https://repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/2.13.9/allure-commandline-2.13.9.tgz"
tar xzf .allure/allure.tgz -C .allure
export PATH="$PWD/.allure/allure-2.13.9/bin:$PATH"

# update to latest cardano-node
niv update

# run tests and generate report
# shellcheck disable=SC2016
nix-shell --run \
  'SCHEDULING_LOG=scheduling.log CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI" make tests; retval="$?"; ./.buildkite/report.sh ./; exit "$retval"'
retval="$?"

exit "$retval"
