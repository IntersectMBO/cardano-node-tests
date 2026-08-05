#!/bin/bash

: "${WORKDIR:?"WORKDIR must be set to a writable directory"}"
: "${REPODIR:?"REPODIR must be set to the root of the cardano-node repository"}"

# This script relies on helpers from `scripts/common.sh`, sourced by the
# calling script.
for _helper in is_truthy is_usable_binary report_existing_binary; do
  if ! command -v "$_helper" >/dev/null 2>&1; then
    echo "scripts/common.sh must be sourced before this script ('$_helper' is missing)" >&2
    exit 1
  fi
done
unset _helper
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
    if ! kill "$psql_pid" 2>/dev/null; then
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

DBSYNC_TAR_URL="${DBSYNC_TAR_URL:-}"

# Check if DBSYNC_TAR_URL is empty and DBSYNC_REV is a version number
if [[ -z "$DBSYNC_TAR_URL" && "$DBSYNC_REV" =~ ^[0-9]+(\.[0-9]+)*$ ]]; then
  DBSYNC_TAR_URL="https://github.com/IntersectMBO/cardano-db-sync/releases/download/${DBSYNC_REV}/cardano-db-sync-${DBSYNC_REV}-linux.tar.gz"
  if file_is_available "$DBSYNC_TAR_URL"; then
    echo "Using db-sync tarball from $DBSYNC_TAR_URL"
  else
    DBSYNC_TAR_URL=""
  fi
fi

# Whether the nix build of `cardano-db-sync` / `cardano-smash-server` was
# skipped in favor of a `BIN_DIR` binary (build-from-source path only).
# Controls the existence asserts and PATH_PREPEND appends below.
_dbsync_from_bin_dir=0
_smash_from_bin_dir=0

if [ -n "${DBSYNC_TAR_URL:-}" ]; then
  # Download db-sync. The tarball is always downloaded (it also provides the
  # schema files), but binaries from the `BIN_DIR` directory still take PATH
  # priority over the downloaded ones.
  dbsync_tar_file="${WORKDIR}/dbsync_bins.tar.gz"
  curl -sSL "$DBSYNC_TAR_URL" > "$dbsync_tar_file" || exit 1
  rm -rf dbsync_download
  mkdir -p dbsync_download
  tar -C dbsync_download -xzf "$dbsync_tar_file" || exit 1
  rm -f "$dbsync_tar_file"
  rm -f db-sync-node
  ln -s dbsync_download db-sync-node || exit 1
  DBSYNC_SCHEMA_DIR="${WORKDIR}/db-sync-node/schema"
  export DBSYNC_SCHEMA_DIR
  rm -f smash-server || rm -f smash-server/bin/cardano-smash-server
  mkdir -p smash-server/bin
  ln -s "${WORKDIR}/dbsync_download/bin/cardano-smash-server" smash-server/bin/cardano-smash-server || exit 1

  # Report `BIN_DIR` binaries that will shadow the downloaded ones on PATH
  if [ -n "${BIN_DIR:-}" ] && is_usable_binary "${BIN_DIR}/cardano-db-sync"; then
    report_existing_binary "${BIN_DIR}/cardano-db-sync" || exit 1
    echo "NOTE: db-sync schema files are taken from the release tarball;" \
      "make sure they match the binary version above"
  fi
  if [ -n "${BIN_DIR:-}" ] && is_usable_binary "${BIN_DIR}/cardano-smash-server"; then
    report_existing_binary "${BIN_DIR}/cardano-smash-server" || exit 1
  fi
else
  # Build db-sync from source. The nix build may be skipped in favor of a
  # binary from the `BIN_DIR` directory, but the repo is still cloned for the
  # schema files.
  case "${DBSYNC_REV:-}" in
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

  # Build cardano-db-sync, unless a usable executable is already present in the
  # `BIN_DIR` directory. The repo above is cloned even when the build is
  # skipped, as the schema files are needed in any case.
  if [ -n "${BIN_DIR:-}" ] && is_usable_binary "${BIN_DIR}/cardano-db-sync"; then
    echo "Skipping build of 'cardano-db-sync'"
    report_existing_binary "${BIN_DIR}/cardano-db-sync" || exit 1
    echo "NOTE: db-sync schema files are taken from DBSYNC_REV=${DBSYNC_REV};" \
      "make sure they match the binary version above"
    _dbsync_from_bin_dir=1
  else
    nix build --accept-flake-config .#cardano-db-sync -o "${WORKDIR}/db-sync-node" \
      || nix build --accept-flake-config .#cardano-db-sync:exe:cardano-db-sync -o "${WORKDIR}/db-sync-node" \
      || exit 1
  fi

  # Build cardano-smash-server, unless a usable executable is already present
  # in the `BIN_DIR` directory
  if is_truthy "${SMASH:-}"; then
    if [ -n "${BIN_DIR:-}" ] && is_usable_binary "${BIN_DIR}/cardano-smash-server"; then
      echo "Skipping build of 'cardano-smash-server'"
      report_existing_binary "${BIN_DIR}/cardano-smash-server" || exit 1
      _smash_from_bin_dir=1
    else
      nix build --accept-flake-config .#cardano-smash-server -o "${WORKDIR}/smash-server" || exit 1
    fi
  fi

  mv "$PWD/schema" "${WORKDIR}/db-sync-schema"
  DBSYNC_SCHEMA_DIR="${WORKDIR}/db-sync-schema"
  export DBSYNC_SCHEMA_DIR

  cd "$WORKDIR" || exit 1
  rm -rf cardano-db-sync # Save space by removing the source code
fi

if [ "$_dbsync_from_bin_dir" -eq 0 ] && [ ! -e "${WORKDIR}/db-sync-node/bin/cardano-db-sync" ]; then
  echo "The \`cardano-db-sync\` binary not found, line $LINENO in sourced db-sync setup" >&2  # assert
  exit 1
fi
if is_truthy "${SMASH:-}" && [ "$_smash_from_bin_dir" -eq 0 ] \
    && [ ! -e "${WORKDIR}/smash-server/bin/cardano-smash-server" ]; then
  echo "The \`cardano-smash-server\` binary not found, line $LINENO in sourced db-sync setup" >&2  # assert
  exit 1
fi

# Add `cardano-db-sync` and `cardano-smash-server` to PATH_PREPEND. Skipped for
# binaries that come from the `BIN_DIR` directory, which the calling script is
# expected to have put on PATH_PREPEND already.
if [ "$_dbsync_from_bin_dir" -eq 0 ]; then
  PATH_PREPEND="${PATH_PREPEND:+"${PATH_PREPEND}:"}$(readlink -f "${WORKDIR}/db-sync-node/bin")"
fi
if [ "$_smash_from_bin_dir" -eq 0 ] && [ -e smash-server/bin/cardano-smash-server ]; then
  PATH_PREPEND="${PATH_PREPEND:+"${PATH_PREPEND}:"}$(readlink -f "${WORKDIR}/smash-server/bin")"
fi
export PATH_PREPEND
unset _dbsync_from_bin_dir _smash_from_bin_dir

# Remove migration files that create indexes
if [ -n "${DBSYNC_SKIP_INDEXES:-}" ]; then
  chmod -R u+w "$DBSYNC_SCHEMA_DIR"
  rm -f "$DBSYNC_SCHEMA_DIR"/migration-4-000*
fi

cd "$REPODIR" || exit 1

# set postgres env variables
export PGHOST=localhost
export PGUSER=postgres
export PGPORT=5432

# Start and setup postgres
if [ "$UID" -eq 0 ]; then
  # If running as root, which is the case for containers, create a postgres user because postgres cannot run as root
  if ! id -u postgres >/dev/null 2>&1; then
    useradd -m -s /bin/sh postgres
  fi

  mkdir -p "$WORKDIR/postgres"
  chown postgres:postgres "$WORKDIR/postgres"
  # shellcheck disable=SC2016
  REPODIR="$REPODIR" WORKDIR="$WORKDIR" SU="$(command -v su)" nix develop \
    --accept-flake-config .#postgres -i -k PGHOST -k PGPORT -k PGUSER -k REPODIR -k WORKDIR -k SU --command bash -c '
    "$SU" postgres -c "PATH=\"$PATH\" \"$REPODIR/scripts/postgres-start.sh\" \"$WORKDIR/postgres\" -k"
  ' || {
    echo "Failed to start postgres as postgres user, line $LINENO in sourced db-sync setup" >&2  # assert
    exit 1
  }
else
  ./scripts/postgres-start-nix.sh "$WORKDIR/postgres" -k
fi

cd "$_origpwd" || exit 1
unset _origpwd
