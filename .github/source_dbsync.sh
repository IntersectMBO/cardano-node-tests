#!/bin/bash

echo "::group::db-sync setup"

TEST_THREADS="${TEST_THREADS:-15}"
CLUSTERS_COUNT="${CLUSTERS_COUNT:-5}"
export TEST_THREADS CLUSTERS_COUNT

pushd "$WORKDIR" || exit 1

stop_postgres() {
  local psql_pid_file="$WORKDIR/postgres/postgres.pid"
  if [ ! -f "$psql_pid_file" ]; then
    return 0
  fi

  local psql_pid
  psql_pid="$(<"$psql_pid_file")"
  for _ in {1..5}; do
    if ! kill "$psql_pid"; then
      break
    fi
    sleep 1
    if [ ! -f "$psql_pid_file" ]; then
      break
    fi
  done

  rm -f "$psql_pid_file"
}

# clone db-sync if needed
if [ ! -e cardano-db-sync ]; then
  git clone https://github.com/input-output-hk/cardano-db-sync.git
fi

pushd cardano-db-sync || exit 1
if [ -n "${DBSYNC_REV:-""}" ]; then
  git fetch
  git checkout "$DBSYNC_REV"
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
