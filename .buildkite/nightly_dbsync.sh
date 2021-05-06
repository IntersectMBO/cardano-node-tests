#! /usr/bin/env nix-shell
#! nix-shell -i bash -p niv nix gnumake gnutar coreutils adoptopenjdk-jre-bin curl git
#! nix-shell -I nixpkgs=./nix
# shellcheck shell=bash

set -xeuo pipefail

REPODIR="$PWD"

WORKDIR="/scratch/workdir"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

# update to latest cardano-node
niv update

pushd "$WORKDIR"

# install Allure
mkdir allure_install
curl -sfLo \
  allure_install/allure.tgz \
  "https://repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/2.13.9/allure-commandline-2.13.9.tgz"
tar xzf allure_install/allure.tgz -C allure_install
export PATH="$PWD/allure_install/allure-2.13.9/bin:$PATH"

# build dbsync
git clone --depth 1 git@github.com:input-output-hk/cardano-db-sync.git
pushd cardano-db-sync
nix-build -A cardano-db-sync -o db-sync-node
export DBSYNC_REPO="$PWD"
popd

# set postgres env variables
export PGHOST=localhost
export PGUSER=postgres
export PGPORT=5432
export PGPASSFILE="$PWD/pgpass"

cd "$REPODIR"

# setup and start postgres
./scripts/postgres-start.sh "$WORKDIR/postgres" -k

# setup dbsync
./scripts/postgres-setup.sh --createdb

# run tests and generate report
set +e
# shellcheck disable=SC2016
nix-shell --run \
  'SCHEDULING_LOG=scheduling.log CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI" TEST_THREADS=5 CLUSTERS_COUNT=1 FORBID_RESTART=1 CI_ARGS="-m dbsync --html=nightly-report.html --self-contained-html" make tests; retval="$?"; ./.buildkite/report.sh .; exit "$retval"'
retval="$?"

echo
echo "Dir content:"
ls -1

exit "$retval"
