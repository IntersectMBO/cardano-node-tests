#! /usr/bin/env -S nix develop --accept-flake-config .#base -c bash
# shellcheck shell=bash

set -xeuo pipefail

REPODIR="$PWD"
export STATE_CLUSTER="${CARDANO_NODE_SOCKET_PATH_CI%/*}"

export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
export COVERAGE_DIR="${COVERAGE_DIR:-".cli_coverage"}"
export SCHEDULING_LOG=scheduling.log

rm -f "$ARTIFACTS_DIR" "$COVERAGE_DIR"
mkdir -p "$ARTIFACTS_DIR" "$COVERAGE_DIR"

BASE_REVISION="${BASE_REVISION:-1.35.3}"

# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/nix_override_cardano_node.sh"

# update cardano-node to specified revision
NODE_OVERRIDE=$(node_override "$BASE_REVISION")

export WORKDIR="/scratch/workdir"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

export DEV_CLUSTER_RUNNING=1 CLUSTERS_COUNT=1 FORBID_RESTART=1 TEST_THREADS=10

set +e
# prepare scripts for stating cluster instance, start cluster instance, run smoke tests
# shellcheck disable=SC2086
nix develop --accept-flake-config $NODE_OVERRIDE --command ./.buildkite/nightly_upgrade_pytest.sh step1
retval="$?"

# retval 0 == all tests passed; 1 == some tests failed; > 1 == some runtime error and we don't want to continue
[ "$retval" -le 1 ] || exit "$retval"

# update cardano-node to specified branch and/or revision, or to the latest available revision
if [ -n "${UPGRADE_REVISION:-""}" ]; then
  NODE_OVERRIDE=$(node_override "$UPGRADE_REVISION")
elif [ -n "${UPGRADE_BRANCH:-""}" ]; then
  NODE_OVERRIDE=$(node_override "$UPGRADE_BRANCH")
else
  NODE_OVERRIDE=$(node_override)
fi

# shellcheck disable=SC2086,SC2016
nix develop --accept-flake-config $NODE_OVERRIDE --command bash -c '
  # update cluster nodes, run smoke tests
  ./.buildkite/nightly_upgrade_pytest.sh step2
  retval="$?"
  # retval 0 == all tests passed; 1 == some tests failed; > 1 == some runtime error and we dont want to continue
  [ "$retval" -le 1 ] || exit "$retval"

  # update to Babbage, run smoke tests
  ./.buildkite/nightly_upgrade_pytest.sh step3
  retval="$?"

  # teardown cluster
  ./.buildkite/nightly_upgrade_pytest.sh finish
  exit $retval
'
retval="$?"

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
