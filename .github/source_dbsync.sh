#!/bin/bash

echo "::group::db-sync setup"

TEST_THREADS="${TEST_THREADS:-15}"
CLUSTERS_COUNT="${CLUSTERS_COUNT:-4}"
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

file_is_available() {
  local url="${1:?"URL parameter is required"}"
  local status_code
  status_code="$(curl -o /dev/null -s -w "%{http_code}" -I "$url")"

  case "$status_code" in
      200|302)
          return 0
          ;;
      *)
          return 1
          ;;
  esac
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

DBSYNC_TAR_URL="${DBSYNC_TAR_URL:-""}"

# Check if DBSYNC_TAR_URL is empty and DBSYNC_REV is a version number
if [[ -z "$DBSYNC_TAR_URL" && "$DBSYNC_REV" =~ ^[0-9]+(\.[0-9]+)*$ ]]; then
  DBSYNC_TAR_URL="https://github.com/IntersectMBO/cardano-db-sync/releases/download/${DBSYNC_REV}/cardano-db-sync-${DBSYNC_REV}-linux.tar.gz"
  if file_is_available "$DBSYNC_TAR_URL"; then
    echo "Using db-sync tarball from $DBSYNC_TAR_URL"
  else
    DBSYNC_TAR_URL=""
  fi
fi


if [ -n "$DBSYNC_TAR_URL" ]; then
  # download db-sync
  DBSYNC_TAR_FILE="$WORKDIR/dbsync_bins.tar.gz"
  curl -sSL "$DBSYNC_TAR_URL" > "$DBSYNC_TAR_FILE" || exit 1
  rm -rf "${WORKDIR}/dbsync_download"
  mkdir -p "${WORKDIR}/dbsync_download/bin"
  tar -C "${WORKDIR}/dbsync_download/bin" -xzf "$DBSYNC_TAR_FILE" || exit 1
  rm -f "$DBSYNC_TAR_FILE"
  rm -f db-sync-node
  ln -s "${WORKDIR}/dbsync_download" db-sync-node || exit 1
else
  # build db-sync
  nix build --accept-flake-config .#cardano-db-sync -o db-sync-node \
    || nix build --accept-flake-config .#cardano-db-sync:exe:cardano-db-sync -o db-sync-node \
    || exit 1
fi

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
