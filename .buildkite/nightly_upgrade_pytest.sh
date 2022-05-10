#!/usr/bin/env bash

set -x

retval=1

export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"
export SCHEDULING_LOG=scheduling.log

CLUSTER_ERA="${CLUSTER_ERA:-"alonzo"}"
CLUSTER_SCRIPTS_DIR="$WORKDIR/cluster0_${CLUSTER_ERA}"

#
# STEP1 - start local cluster and run smoke tests for the first time
#

if [ "$1" = "step1" ]; then
  # generate local cluster scripts
  PYTHONPATH=$PYTHONPATH:$PWD cardano_node_tests/prepare_cluster_scripts.py -s cardano_node_tests/cluster_scripts/alonzo/ -d "$CLUSTER_SCRIPTS_DIR"
  # try to stop local cluster
  "$CLUSTER_SCRIPTS_DIR"/stop-cluster-hfc
  # start local cluster
  "$CLUSTER_SCRIPTS_DIR"/start-cluster-hfc || exit 6
  # run smoke tests
  pytest cardano_node_tests -n "$TEST_THREADS" -m "smoke" --artifacts-base-dir="$ARTIFACTS_DIR" --cli-coverage-dir="$COVERAGE_DIR" --html=testrun-report-step1.html --self-contained-html
  retval="$?"
  # stop local cluster if tests failed unexpectedly
  [ "$retval" -le 1 ] || "$CLUSTER_SCRIPTS_DIR"/stop-cluster-hfc

#
# STEP2 - run smoke tests for the second time on the already started local cluster
#

elif [ "$1" = "step2" ]; then
  # restart local cluster nodes with binaries from new cluster-node version
  "$STATE_CLUSTER"/supervisorctl_restart_nodes
  # run smoke tests
  pytest cardano_node_tests -n "$TEST_THREADS" -m "smoke" --artifacts-base-dir="$ARTIFACTS_DIR" --cli-coverage-dir="$COVERAGE_DIR" --html=testrun-report-step2.html --self-contained-html
  retval="$?"
  # stop local cluster
  "$CLUSTER_SCRIPTS_DIR"/stop-cluster-hfc
  # generate CLI coverage reports
  ./.buildkite/cli_coverage.sh .
fi

exit "$retval"
