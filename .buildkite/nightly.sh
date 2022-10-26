#! /usr/bin/env nix-shell
#! nix-shell -i bash -p niv nix gnugrep gnumake gnutar coreutils adoptopenjdk-jre-bin curl git xz
#! nix-shell -I nixpkgs=./nix
# shellcheck shell=bash

set -xeuo pipefail

REPODIR="$PWD"
export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"

MARKEXPR="${MARKEXPR:-""}"
if [ "${CI_SKIP_LONG:-"false"}" != "false" ]; then
  MARKEXPR="${MARKEXPR:+"${MARKEXPR} and "}not long"
fi
export MARKEXPR

if [ "${CI_ENABLE_P2P:-"false"}" != "false" ]; then
  export ENABLE_P2P="true"
fi

WORKDIR="/scratch/workdir"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

# update cardano-node to specified branch and/or revision, or to the latest available
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/niv_update_func.sh"
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/niv_update_cardano_node.sh"

# run tests and generate report
rm -rf "${ARTIFACTS_DIR:?}"/*
set +e
# shellcheck disable=SC2016
nix-shell --run \
  'SCHEDULING_LOG=scheduling.log CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI" make tests; retval="$?"; ./.buildkite/cli_coverage.sh .; exit "$retval"'
retval="$?"

# move html report to root dir
mv .reports/testrun-report.html testrun-report.html

# create results archive
"$REPODIR"/.buildkite/results.sh .

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
