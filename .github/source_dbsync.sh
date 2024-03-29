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

case "${DBSYNC_REV:-""}" in
  "" )
    echo "The value for DBSYNC_REV cannot be empty" >&2
    exit 1
    ;;

  "master" | "HEAD" )
    export DBSYNC_REV="master"

    if [ ! -e cardano-db-sync ]; then
      git clone --depth 1 https://github.com/IntersectMBO/cardano-db-sync.git
    fi

    pushd cardano-db-sync || exit 1
    git fetch origin master
    ;;

  * )
    if [ ! -e cardano-db-sync ]; then
      git clone https://github.com/IntersectMBO/cardano-db-sync.git
    fi

    pushd cardano-db-sync || exit 1
    git fetch
    ;;
esac

git stash
git checkout "$DBSYNC_REV"
git rev-parse HEAD

if [ -n "${DBSYNC_SKIP_INDEXES:-""}" ]; then
  rm -f schema/migration-4-000*
fi

# build db-sync
nix build --accept-flake-config .#cardano-db-sync -o db-sync-node \
  || nix build --accept-flake-config .#cardano-db-sync:exe:cardano-db-sync -o db-sync-node \
  || exit 1
[ -e db-sync-node/bin/cardano-db-sync ] || exit 1
export DBSYNC_REPO="$PWD"

pushd "$REPODIR" || exit 1

# set postgres env variables
export PGHOST=localhost
export PGUSER=postgres
export PGPORT=5432

# start and setup postgres
./scripts/postgres-start.sh "$WORKDIR/postgres" -k

echo "::endgroup::"
