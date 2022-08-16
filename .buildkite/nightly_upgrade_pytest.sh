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
  # print path to cardano-node binary
  pool1_pid="$("$STATE_CLUSTER"/supervisorctl pid nodes:pool1)"
  ls -l "/proc/$pool1_pid/exe"
  # run smoke tests
  pytest cardano_node_tests -n "$TEST_THREADS" -m "smoke" --artifacts-base-dir="$ARTIFACTS_DIR" --cli-coverage-dir="$COVERAGE_DIR" --html=testrun-report-step1.html --self-contained-html
  retval="$?"
  # stop local cluster if tests failed unexpectedly
  [ "$retval" -le 1 ] || "$CLUSTER_SCRIPTS_DIR"/stop-cluster-hfc

#
# STEP2 - run smoke tests for the second time on the already started local cluster
#

elif [ "$1" = "step2" ]; then
  # Restart local cluster nodes with binaries from new cluster-node version.
  # It is necessary to restart supervisord with new environment.
  "$STATE_CLUSTER"/supervisord_stop
  sleep 3
  "$STATE_CLUSTER"/supervisord_start || exit 6
  sleep 5
  "$STATE_CLUSTER"/supervisorctl start all
  sleep 5
  "$STATE_CLUSTER"/supervisorctl status
  # print path to cardano-node binary
  pool1_pid="$("$STATE_CLUSTER"/supervisorctl pid nodes:pool1)"
  ls -l "/proc/$pool1_pid/exe"

  # waiting for node to start
  for _ in {1..10}; do
    if [ -S "$CARDANO_NODE_SOCKET_PATH" ]; then
      break
    fi
    sleep 5
  done
  [ -S "$CARDANO_NODE_SOCKET_PATH" ] || { echo "Failed to start node" >&2; exit 1; }  # assert

  # waiting to make sure the chain is synced
  NETWORK_MAGIC="$(jq '.networkMagic' "$STATE_CLUSTER/shelley/genesis.json")"
  for _ in {1..10}; do
    sync_progress="$(cardano-cli query tip --testnet-magic "$NETWORK_MAGIC" | jq '.syncProgress')"
    if [ "$sync_progress" = "100.00" ]; then
      break
    fi
    sleep 5
  done

  # run smoke tests
  pytest cardano_node_tests -n "$TEST_THREADS" -m "smoke" --artifacts-base-dir="$ARTIFACTS_DIR" --cli-coverage-dir="$COVERAGE_DIR" --html=testrun-report-step2.html --self-contained-html
  retval="$?"
  # stop local cluster
  "$CLUSTER_SCRIPTS_DIR"/stop-cluster-hfc
  # generate CLI coverage reports
  ./.buildkite/cli_coverage.sh .
fi

exit "$retval"
