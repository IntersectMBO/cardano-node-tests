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
  export UPGRADE_TESTS_STEP=1

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
  export UPGRADE_TESTS_STEP=2

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
  [ -S "$CARDANO_NODE_SOCKET_PATH" ] || { echo "Failed to start node" >&2; exit 6; }  # assert

  # waiting to make sure the chain is synced
  NETWORK_MAGIC="$(jq '.networkMagic' "$STATE_CLUSTER/shelley/genesis.json")"
  for _ in {1..10}; do
    sync_progress="$(cardano-cli query tip --testnet-magic "$NETWORK_MAGIC" | jq -r '.syncProgress')"
    if [ "$sync_progress" = "100.00" ]; then
      break
    fi
    sleep 5
  done
  [ "$sync_progress" = "100.00" ] || { echo "Failed to sync node" >&2; exit 6; }  # assert

  # Test for ignoring expected errors in log files. Run separately to make sure it runs first.
  pytest cardano_node_tests/tests/test_node_upgrade.py -k test_ignore_log_errors

  # run smoke tests
  pytest cardano_node_tests -n "$TEST_THREADS" -m "smoke" --artifacts-base-dir="$ARTIFACTS_DIR" --cli-coverage-dir="$COVERAGE_DIR" --html=testrun-report-step2.html --self-contained-html
  retval="$?"

  # stop local cluster if tests failed unexpectedly
  [ "$retval" -le 1 ] || "$CLUSTER_SCRIPTS_DIR"/stop-cluster-hfc


#
# STEP3 - update local cluster to Babbage, run smoke tests for the third time
#

elif [ "$1" = "step3" ]; then
  export UPGRADE_TESTS_STEP=3

  # update to Babbage
  pytest cardano_node_tests/tests/test_node_upgrade.py -k test_update_to_babbage
  retval="$?"
  [ "$retval" -le 1 ] || exit "$retval"

  # run smoke tests
  export CLUSTER_ERA="babbage"
  export TX_ERA="babbage"
  pytest cardano_node_tests -n "$TEST_THREADS" -m "smoke" --artifacts-base-dir="$ARTIFACTS_DIR" --cli-coverage-dir="$COVERAGE_DIR" --html=testrun-report-step3.html --self-contained-html
  retval="$?"

#
# FINISH - teardown cluster, generate reports
#

elif [ "$1" = "finish" ]; then
  # stop local cluster
  "$CLUSTER_SCRIPTS_DIR"/stop-cluster-hfc

  # generate CLI coverage reports
  ./.buildkite/cli_coverage.sh .

  retval=0
fi

exit "$retval"
