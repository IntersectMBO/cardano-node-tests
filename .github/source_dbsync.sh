#!/bin/bash

echo "::group::db-sync setup"

CLUSTERS_COUNT="${CLUSTERS_COUNT:-5}"

pushd "$WORKDIR" || exit 1

# clone db-sync
git clone https://github.com/input-output-hk/cardano-db-sync.git
pushd cardano-db-sync || exit 1
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

pushd "$REPODIR" || exit 1

# set postgres env variables
export PGHOST=localhost
export PGUSER=postgres
export PGPORT=5432

# start and setup postgres
./scripts/postgres-start.sh "$WORKDIR/postgres" -k

echo "::endgroup::"
