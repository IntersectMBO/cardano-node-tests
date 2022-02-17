#! /usr/bin/env nix-shell
#! nix-shell -i bash -p niv nix gnugrep gnumake gnutar coreutils adoptopenjdk-jre-bin curl git xz
#! nix-shell -I nixpkgs=./nix
# shellcheck shell=bash

set -xeuo pipefail

REPODIR="$PWD"
export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"

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
rm -rf "${ARTIFACTS_DIR:?}"/*
set +e
# shellcheck disable=SC2016
nix-shell --run \
  'SCHEDULING_LOG=scheduling.log CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI" CI_ARGS="--html=testrun-report.html --self-contained-html" make tests; retval="$?"; ./.buildkite/report.sh .; exit "$retval"'
retval="$?"

# grep testing artifacts for errors
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/grep_errors.sh"

# save testing artifacts
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/save_artifacts.sh"

# compress scheduling log
xz scheduling.log

echo
echo "Dir content:"
ls -1a

exit "$retval"
