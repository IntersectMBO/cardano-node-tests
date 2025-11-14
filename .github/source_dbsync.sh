#!/bin/bash

TEST_THREADS="${TEST_THREADS:-15}"
CLUSTERS_COUNT="${CLUSTERS_COUNT:-4}"
export TEST_THREADS CLUSTERS_COUNT

_origpwd="$PWD"
cd "$WORKDIR" || exit 1

stop_postgres() {
  echo "Stopping postgres"

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
  mkdir -p cardano-db-sync && cd cardano-db-sync || exit 1
  DBSYNC_TAR_FILE="$PWD/dbsync_bins.tar.gz"
  curl -sSL "$DBSYNC_TAR_URL" > "$DBSYNC_TAR_FILE" || exit 1
  rm -rf "${PWD}/dbsync_download"
  mkdir -p "${PWD}/dbsync_download/"
  tar -C "${PWD}/dbsync_download/" -xzf "$DBSYNC_TAR_FILE" || exit 1
  rm -f "$DBSYNC_TAR_FILE"
  rm -f db-sync-node
  ln -s "${PWD}/dbsync_download" db-sync-node || exit 1
  rm -f smash-server || rm -f smash-server/bin/cardano-smash-server
  mkdir -p smash-server/bin
  ln -s "${PWD}/dbsync_download/bin/cardano-smash-server" smash-server/bin/cardano-smash-server || exit 1
  DBSYNC_SCHEMA_DIR="$(readlink -m db-sync-node)/schema"
  export DBSYNC_SCHEMA_DIR
else
  # Build db-sync
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

      cd cardano-db-sync || exit 1
      git fetch origin master
      ;;

    * )
      if [ ! -e cardano-db-sync ]; then
        git clone https://github.com/IntersectMBO/cardano-db-sync.git
      fi

      cd cardano-db-sync || exit 1
      git fetch
      ;;
  esac

  git stash
  git checkout "$DBSYNC_REV"
  git rev-parse HEAD

  nix build --accept-flake-config .#cardano-db-sync -o db-sync-node \
    || nix build --accept-flake-config .#cardano-db-sync:exe:cardano-db-sync -o db-sync-node \
    || exit 1
  # Build cardano-smash-server
  if [ "${SMASH:-"false"}" != "false" ]; then
    nix build --accept-flake-config .#cardano-smash-server -o smash-server || exit 1
  fi
  DBSYNC_SCHEMA_DIR="$PWD/schema"
  export DBSYNC_SCHEMA_DIR
fi

if [ ! -e db-sync-node/bin/cardano-db-sync ]; then
  echo "The \`cardano-db-sync\` binary not found, line $LINENO in sourced db-sync setup" >&2  # assert
  exit 1
fi

# Add `cardano-db-sync` and `cardano-smash-server` to PATH_PREPEND
PATH_PREPEND="${PATH_PREPEND:+"${PATH_PREPEND}:"}$(readlink -m db-sync-node/bin)"
if [ -e smash-server/bin/cardano-smash-server ]; then
  PATH_PREPEND="${PATH_PREPEND:+"${PATH_PREPEND}:"}$(readlink -m smash-server/bin)"
fi
export PATH_PREPEND

if [ -n "${DBSYNC_SKIP_INDEXES:-""}" ]; then
  # Delete the indexes only after the binaries are ready, so the binaries can be retrieved from
  # the nix binary cache if available.
  chmod -R u+w "$DBSYNC_SCHEMA_DIR" # Add write permissions
  rm -f "$DBSYNC_SCHEMA_DIR"/migration-4-000*

  if [ -z "$DBSYNC_TAR_URL" ]; then
    cleanup_dbsync_repo() {
      local _origpwd="$PWD"
      cd "$DBSYNC_SCHEMA_DIR"/.. || exit 1
      # shellcheck disable=SC2015
      git stash && git stash drop || :
      cd "$_origpwd" || exit 1
    }
  fi
fi

cd "$REPODIR" || exit 1

# set postgres env variables
export PGHOST=localhost
export PGUSER=postgres
export PGPORT=5432

# start and setup postgres
./scripts/postgres-start.sh "$WORKDIR/postgres" -k

cd "$_origpwd" || exit 1
unset _origpwd
