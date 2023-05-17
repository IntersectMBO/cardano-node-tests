#! /usr/bin/env -S nix develop --accept-flake-config .#base -c bash
# shellcheck shell=bash

set -xeuo pipefail

REPODIR="$PWD"
export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
export CLUSTERS_COUNT="${CLUSTERS_COUNT:-5}"

export SCHEDULING_LOG=scheduling.log

MARKEXPR="${MARKEXPR:-"dbsync"}"
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

# function to update cardano-node to specified branch and/or revision, or to the latest available
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/nix_override_cardano_node.sh"

pushd "$WORKDIR"

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
nix build .#cardano-db-sync -o db-sync-node
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
# shellcheck disable=SC2046,SC2016,SC2119
nix develop --accept-flake-config $(node_override) --command bash -c \ "
  export CARDANO_NODE_SOCKET_PATH=\"$CARDANO_NODE_SOCKET_PATH_CI\"
  make tests; retval=\"\$?\"; ./.buildkite/cli_coverage.sh .; exit \"\$retval\"
"
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
xz "$SCHEDULING_LOG"

echo
echo "Dir content:"
ls -1a

exit "$retval"
