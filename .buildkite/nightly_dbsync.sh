#! /usr/bin/env nix-shell
#! nix-shell -i bash -p niv nix gnugrep gnumake gnutar coreutils adoptopenjdk-jre-bin curl git
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

# clone db-sync
git clone git@github.com:input-output-hk/cardano-db-sync.git
pushd cardano-db-sync
if [ -n "${DBSYNC_REV:-""}" ]; then
  git fetch
  git checkout "$DBSYNC_REV"
elif [ -n "${DBSYNC_BRANCH:-""}" ]; then
  git fetch
  git checkout "$DBSYNC_BRANCH"
else
  git pull origin master
fi
git rev-parse HEAD

# build db-sync
nix-build -A cardano-db-sync -o db-sync-node
export DBSYNC_REPO="$PWD"

pushd "$REPODIR"

# set postgres env variables
export PGHOST=localhost
export PGUSER=postgres
export PGPORT=5432

# start and setup postgres
./scripts/postgres-start.sh "$WORKDIR/postgres" -k

# run tests and generate report
rm -rf "${ARTIFACTS_DIR:?}"/*
set +e
# shellcheck disable=SC2016
nix-shell --run \
  'SCHEDULING_LOG=scheduling.log CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI" CLUSTERS_COUNT=5 CI_ARGS="-m dbsync --html=testrun-report.html --self-contained-html" make tests; retval="$?"; ./.buildkite/report.sh .; exit "$retval"'
retval="$?"

# grep testing artifacts for errors
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/grep_errors.sh"

echo
echo "Dir content:"
ls -1a

exit "$retval"
