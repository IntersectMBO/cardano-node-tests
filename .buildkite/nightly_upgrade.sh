#! /usr/bin/env nix-shell
#! nix-shell -i bash -p niv nix gnugrep gnumake gnutar coreutils adoptopenjdk-jre-bin curl git xz
#! nix-shell -I nixpkgs=./nix
# shellcheck shell=bash

set -xeuo pipefail

REPODIR="$PWD"
export STATE_CLUSTER="${CARDANO_NODE_SOCKET_PATH_CI%/*}"

export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
export COVERAGE_DIR="${COVERAGE_DIR:-".cli_coverage"}"
rm -f "$ARTIFACTS_DIR" "$COVERAGE_DIR"
mkdir -p "$ARTIFACTS_DIR" "$COVERAGE_DIR"

BASE_REVISION="${BASE_REVISION:-1.35.3}"

# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/niv_update_func.sh"

# update cardano-node to specified revision
niv_update
niv_update cardano-node --rev "$BASE_REVISION"
cat nix/sources.json

export WORKDIR="/scratch/workdir"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

export DEV_CLUSTER_RUNNING=1 CLUSTERS_COUNT=1 FORBID_RESTART=1 TEST_THREADS=10

set +e
# prepare scripts for stating cluster instance, start cluster instance, run smoke tests
nix-shell --run './.buildkite/nightly_upgrade_pytest.sh step1'
retval="$?"

# retval 0 == all tests passed; 1 == some tests failed; > 1 == some runtime error and we don't want to continue
[ "$retval" -le 1 ] || exit "$retval"

# update cardano-node to specified branch and/or revision, or to the latest available revision
if [ -n "${UPGRADE_REVISION:-""}" ]; then
  niv_update cardano-node --rev "$UPGRADE_REVISION"
elif [ -n "${UPGRADE_BRANCH:-""}" ]; then
  niv_update cardano-node --branch "$UPGRADE_BRANCH"
else
  niv_update cardano-node
fi
cat nix/sources.json

# update cluster nodes, run smoke tests
nix-shell --run './.buildkite/nightly_upgrade_pytest.sh step2'
retval="$?"

# retval 0 == all tests passed; 1 == some tests failed; > 1 == some runtime error and we don't want to continue
[ "$retval" -le 1 ] || exit "$retval"

# update to Babbage PV8, run smoke tests
nix-shell --run './.buildkite/nightly_upgrade_pytest.sh step3'
retval="$?"

# teardown cluster
nix-shell --run './.buildkite/nightly_upgrade_pytest.sh finish'

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
