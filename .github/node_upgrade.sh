#! /usr/bin/env -S nix develop --accept-flake-config .#base -c bash
# shellcheck shell=bash

set -xeuo pipefail

nix --version

REPODIR="$(readlink -m "${0%/*}/..")"
cd "$REPODIR"

export WORKDIR="$REPODIR/run_workdir"

# shellcheck disable=SC1090,SC1091
. .github/stop_cluster_instances.sh

# stop all running cluster instances
stop_instances "$WORKDIR"

# create clean workdir
rm -rf "${WORKDIR:?}"
mkdir -p "$WORKDIR"

export TMPDIR="$WORKDIR/tmp"
mkdir -p "$TMPDIR"

export CARDANO_NODE_SOCKET_PATH_CI="$WORKDIR/state-cluster0/bft1.socket"

export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
rm -rf "${ARTIFACTS_DIR:?}"
mkdir -p "$ARTIFACTS_DIR"

export COVERAGE_DIR="${COVERAGE_DIR:-".cli_coverage"}"
rm -rf "${COVERAGE_DIR:?}"
mkdir -p "$COVERAGE_DIR"

export SCHEDULING_LOG=scheduling.log
true > "$SCHEDULING_LOG"

BASE_REVISION="${BASE_REVISION:-1.35.7}"

# shellcheck disable=SC1090,SC1091
. .github/nix_override_cardano_node.sh

# update cardano-node to specified revision
NODE_OVERRIDE=$(node_override "$BASE_REVISION")

export DEV_CLUSTER_RUNNING=1 CLUSTERS_COUNT=1 FORBID_RESTART=1 TEST_THREADS=10 NUM_POOLS="${NUM_POOLS:-4}"
unset ENABLE_P2P MIXED_P2P

echo "::group::Nix env setup"
printf "start: %(%H:%M:%S)T\n" -1

set +e
# shellcheck disable=SC2086,SC2016
nix develop --accept-flake-config $NODE_OVERRIDE --command bash -c '
  : > "$WORKDIR/.nix_step1"
  printf "finish: %(%H:%M:%S)T\n" -1
  echo "::endgroup::"  # end group for "Nix env setup"

  echo "::group::Pytest step1"
  # prepare scripts for stating cluster instance, start cluster instance, run smoke tests
  ./.github/node_upgrade_pytest.sh step1
'
retval="$?"

if [ ! -e "$WORKDIR/.nix_step1" ]; then
  echo "Nix env setup failed, exiting"
  exit 1
fi

# retval 0 == all tests passed; 1 == some tests failed; > 1 == some runtime error and we don't want to continue
[ "$retval" -le 1 ] || exit "$retval"

echo "::endgroup::"  # end group for "Pytest step1"
echo "::group::Pytest step2"

# update cardano-node to specified branch and/or revision, or to the latest available revision
if [ -n "${UPGRADE_REVISION:-""}" ]; then
  NODE_OVERRIDE=$(node_override "$UPGRADE_REVISION")
else
  NODE_OVERRIDE=$(node_override)
fi

# shellcheck disable=SC2086,SC2016
nix develop --accept-flake-config $NODE_OVERRIDE --command bash -c '
  : > "$WORKDIR/.nix_step2"

  # update cluster nodes, run smoke tests
  ./.github/node_upgrade_pytest.sh step2
  retval="$?"
  # retval 0 == all tests passed; 1 == some tests failed; > 1 == some runtime error and we dont want to continue
  [ "$retval" -le 1 ] || exit "$retval"
  echo "::endgroup::"  # end group for "Pytest step2"

  echo "::group::Pytest step3"
  # update to Babbage, run smoke tests
  ./.github/node_upgrade_pytest.sh step3
  retval="$?"
  echo "::endgroup::"  # end group for "Pytest step3"

  echo "::group::Cluster teardown & artifacts"
  # teardown cluster
  ./.github/node_upgrade_pytest.sh finish
  exit $retval
'
retval="$?"

if [ ! -e "$WORKDIR/.nix_step2" ]; then
  echo "Nix env setup failed, exiting"
  exit 1
fi

# grep testing artifacts for errors
# shellcheck disable=SC1090,SC1091
. .github/grep_errors.sh

# stop all running cluster instances
stop_instances "$WORKDIR"

# prepare artifacts for upload in Github Actions
if [ -n "${GITHUB_ACTIONS:-""}" ]; then
  # save testing artifacts
  # shellcheck disable=SC1090,SC1091
  . .github/save_artifacts.sh

  # compress scheduling log
  xz "$SCHEDULING_LOG"

  echo
  echo "Dir content:"
  ls -1a
fi

echo "::endgroup::" # end group for "Cluster teardown & artifacts"

exit "$retval"
