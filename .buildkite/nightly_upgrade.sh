#! /usr/bin/env nix-shell
#! nix-shell -i bash -p niv nix gnugrep gnumake gnutar coreutils adoptopenjdk-jre-bin curl git xz
#! nix-shell -I nixpkgs=./nix
# shellcheck shell=bash

set -xeuo pipefail

REPODIR="$PWD"
export STATE_CLUSTER="${CARDANO_NODE_SOCKET_PATH_CI%/*}"

export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
export ALLURE_DIR="${ALLURE_DIR:-".reports"}"
export COVERAGE_DIR="${COVERAGE_DIR:-".cli_coverage"}"
rm -f "$ARTIFACTS_DIR" "$ALLURE_DIR" "$COVERAGE_DIR"
mkdir -p "$ARTIFACTS_DIR" "$ALLURE_DIR" "$COVERAGE_DIR"

BASE_REVISION="${BASE_REVISION:-1.34.1}"

niv update

export WORKDIR="/scratch/workdir"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

pushd "$WORKDIR"

# install Allure
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/allure_install.sh"

pushd "$REPODIR"

# update cardano-node to specified revision
niv update cardano-node --rev "$BASE_REVISION"
cat nix/sources.json

export DEV_CLUSTER_RUNNING=1 CLUSTERS_COUNT=1 FORBID_RESTART=1 TEST_THREADS=10

set +e
# prepare scripts for stating cluster instance, start cluster instance, run selected tests
# shellcheck disable=SC2016
nix-shell --run \
  'PYTHONPATH=$PYTHONPATH:$PWD cardano_node_tests/prepare_cluster_scripts.py -s cardano_node_tests/cluster_scripts/alonzo/ -d "$WORKDIR"/cluster0_alonzo; export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"; "$WORKDIR"/cluster0_alonzo/stop-cluster-hfc; "$WORKDIR"/cluster0_alonzo/start-cluster-hfc && SCHEDULING_LOG=scheduling.log pytest -n "$TEST_THREADS" -m "smoke" cardano_node_tests --artifacts-base-dir="$ARTIFACTS_DIR" --cli-coverage-dir="$COVERAGE_DIR" --alluredir="$ALLURE_DIR"; retval="$?"; [ "$retval" -le 1 ] || "$WORKDIR"/cluster0_alonzo/stop-cluster-hfc; exit "$retval"'
retval_first="$?"

# retval 0 == all tests passed; 1 == some tests failed; > 1 == some runtime error and we don't want to continue
[ "$retval_first" -le 1 ] || exit "$retval_first"

# update cardano-node to the latest available revision
niv update cardano-node
cat nix/sources.json

# update cluster nodes, run tests, generate report
# shellcheck disable=SC2016
nix-shell --run \
  'export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"; "$STATE_CLUSTER"/supervisorctl_restart_nodes; SCHEDULING_LOG=scheduling.log pytest -n "$TEST_THREADS" -m "smoke" cardano_node_tests --artifacts-base-dir="$ARTIFACTS_DIR" --cli-coverage-dir="$COVERAGE_DIR" --alluredir="$ALLURE_DIR" --html=testrun-report.html --self-contained-html; retval="$?"; "$WORKDIR"/cluster0_alonzo/stop-cluster-hfc; ./.buildkite/report.sh .; exit "$retval"'
retval_second="$?"

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

exit "$retval_second"
