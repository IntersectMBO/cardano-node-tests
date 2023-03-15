#!/usr/bin/env bash

set -x

retval=1

export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"

export CLUSTER_ERA="${CLUSTER_ERA:-"babbage"}"
export TX_ERA="babbage"

CLUSTER_SCRIPTS_DIR="$WORKDIR/cluster0_${CLUSTER_ERA}"
STATE_CLUSTER="${CARDANO_NODE_SOCKET_PATH_CI%/*}"

# init reports dir before each step
export REPORTS_DIR="${REPORTS_DIR:-".reports"}"
rm -rf "${REPORTS_DIR:?}"
mkdir -p "$REPORTS_DIR"

#
# STEP1 - start local cluster and run smoke tests for the first time
#

if [ "$1" = "step1" ]; then
  export UPGRADE_TESTS_STEP=1

  if [ "${CI_FAST_CLUSTER:-"false"}" != "false" ] && [ -z "${SCRIPTS_DIRNAME:-""}" ]; then
    export SCRIPTS_DIRNAME="${CLUSTER_ERA:-"babbage"}_fast"
  fi

  # generate local cluster scripts
  PYTHONPATH=$PYTHONPATH:$PWD cardano_node_tests/prepare_cluster_scripts.py -s "cardano_node_tests/cluster_scripts/$SCRIPTS_DIRNAME" -d "$CLUSTER_SCRIPTS_DIR"

  # try to stop local cluster
  "$CLUSTER_SCRIPTS_DIR"/stop-cluster-hfc
  # start local cluster
  "$CLUSTER_SCRIPTS_DIR"/start-cluster-hfc || exit 6

  # get path to cardano-node binary
  pool1_pid="$("$STATE_CLUSTER"/supervisorctl pid nodes:pool1)"
  node_path_step1="$(readlink -f "/proc/$pool1_pid/exe")"

  # backup the original cardano-node binary
  ln -s "$node_path_step1" "$STATE_CLUSTER"/cardano-node-step1

  # run smoke tests
  pytest cardano_node_tests -n "$TEST_THREADS" -m "smoke or upgrade" --artifacts-base-dir="$ARTIFACTS_DIR" --cli-coverage-dir="$COVERAGE_DIR" --alluredir="$REPORTS_DIR" --html=testrun-report-step1.html --self-contained-html
  retval="$?"

  # stop local cluster if tests failed unexpectedly
  [ "$retval" -le 1 ] || "$CLUSTER_SCRIPTS_DIR"/stop-cluster-hfc

  # create results archive for step1
  ./.github/results.sh .
  mv allure-results.tar.xz allure-results-step1.tar.xz

#
# STEP2 - partly update local cluster and run smoke tests for the second time
#

elif [ "$1" = "step2" ]; then
  export UPGRADE_TESTS_STEP=2

  # generate config and topology files for "mixed" mode
  CARDANO_NODE_SOCKET_PATH="$WORKDIR/dry_mixed/state-cluster0/bft1.socket" MIXED_P2P=1 DRY_RUN=1 "$CLUSTER_SCRIPTS_DIR"/start-cluster-hfc

  # copy newly generated topology files to the cluster state dir
  cp -f "$WORKDIR"/dry_mixed/state-cluster0/topology-*.json "$STATE_CLUSTER"

  # copy newly generated config files to the cluster state dir, but use the original genesis files
  BYRON_GENESIS_HASH="$(jq -r ".ByronGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  SHELLEY_GENESIS_HASH="$(jq -r ".ShelleyGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  ALONZO_GENESIS_HASH="$(jq -r ".AlonzoGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  for conf in "$WORKDIR"/dry_mixed/state-cluster0/config-*.json; do
    fname="${conf##*/}"
    jq \
      --arg byron_hash "$BYRON_GENESIS_HASH" \
      --arg shelley_hash "$SHELLEY_GENESIS_HASH" \
      --arg alonzo_hash "$ALONZO_GENESIS_HASH" \
      '.ByronGenesisHash = $byron_hash | .ShelleyGenesisHash = $shelley_hash | .AlonzoGenesisHash = $alonzo_hash' \
      "$conf" > "$STATE_CLUSTER/$fname"
  done

  # run pool3 with the original cardano-node binary
  cp -a "$STATE_CLUSTER"/cardano-node-pool3 "$STATE_CLUSTER"/cardano-node-pool3.orig
  sed -i 's/exec cardano-node run/exec .\/state-cluster0\/cardano-node-step1 run/' "$STATE_CLUSTER"/cardano-node-pool3

  # Restart local cluster nodes with binaries from new cluster-node version.
  # It is necessary to restart supervisord with new environment.
  "$STATE_CLUSTER"/supervisord_stop
  sleep 3
  "$STATE_CLUSTER"/supervisord_start || exit 6
  sleep 5
  "$STATE_CLUSTER"/supervisorctl start all
  sleep 5
  "$STATE_CLUSTER"/supervisorctl status

  # print path to cardano-node binaries
  pool1_pid="$("$STATE_CLUSTER"/supervisorctl pid nodes:pool1)"
  ls -l "/proc/$pool1_pid/exe"
  pool3_pid="$("$STATE_CLUSTER"/supervisorctl pid nodes:pool3)"
  ls -l "/proc/$pool3_pid/exe"

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
  pytest cardano_node_tests -n "$TEST_THREADS" -m "smoke or upgrade" --artifacts-base-dir="$ARTIFACTS_DIR" --cli-coverage-dir="$COVERAGE_DIR" --alluredir="$REPORTS_DIR" --html=testrun-report-step2.html --self-contained-html
  retval="$?"

  # stop local cluster if tests failed unexpectedly
  [ "$retval" -le 1 ] || "$CLUSTER_SCRIPTS_DIR"/stop-cluster-hfc

  # create results archive for step2
  ./.github/results.sh .
  mv allure-results.tar.xz allure-results-step2.tar.xz


#
# STEP3 - finish update of local cluster and run smoke tests for the third time
#

elif [ "$1" = "step3" ]; then
  export UPGRADE_TESTS_STEP=3

  # generate config and topology files for p2p mode
  CARDANO_NODE_SOCKET_PATH="$WORKDIR/dry_p2p/state-cluster0/bft1.socket" ENABLE_P2P=1 DRY_RUN=1 "$CLUSTER_SCRIPTS_DIR"/start-cluster-hfc

  # copy newly generated topology files to the cluster state dir
  cp -f "$WORKDIR"/dry_p2p/state-cluster0/topology-*.json "$STATE_CLUSTER"

  # copy newly generated config files to the cluster state dir, but use the original genesis files
  BYRON_GENESIS_HASH="$(jq -r ".ByronGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  SHELLEY_GENESIS_HASH="$(jq -r ".ShelleyGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  ALONZO_GENESIS_HASH="$(jq -r ".AlonzoGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  for conf in "$WORKDIR"/dry_p2p/state-cluster0/config-*.json; do
    fname="${conf##*/}"
    jq \
      --arg byron_hash "$BYRON_GENESIS_HASH" \
      --arg shelley_hash "$SHELLEY_GENESIS_HASH" \
      --arg alonzo_hash "$ALONZO_GENESIS_HASH" \
      '.ByronGenesisHash = $byron_hash | .ShelleyGenesisHash = $shelley_hash | .AlonzoGenesisHash = $alonzo_hash' \
      "$conf" > "$STATE_CLUSTER/$fname"
  done

  # use the upgraded cardano-node binary for pool3
  cp -a "$STATE_CLUSTER"/cardano-node-pool3.orig "$STATE_CLUSTER"/cardano-node-pool3

  # restart all nodes
  "$STATE_CLUSTER"/supervisorctl restart nodes:
  sleep 10
  "$STATE_CLUSTER"/supervisorctl status

  # print path to cardano-node binaries
  pool1_pid="$("$STATE_CLUSTER"/supervisorctl pid nodes:pool1)"
  ls -l "/proc/$pool1_pid/exe"
  pool3_pid="$("$STATE_CLUSTER"/supervisorctl pid nodes:pool3)"
  ls -l "/proc/$pool3_pid/exe"

  # Test for ignoring expected errors in log files. Run separately to make sure it runs first.
  pytest cardano_node_tests/tests/test_node_upgrade.py -k test_ignore_log_errors

  # run smoke tests
  pytest cardano_node_tests -n "$TEST_THREADS" -m "smoke or upgrade" --artifacts-base-dir="$ARTIFACTS_DIR" --cli-coverage-dir="$COVERAGE_DIR" --alluredir="$REPORTS_DIR" --html=testrun-report-step3.html --self-contained-html
  retval="$?"

  # create results archive for step3
  ./.github/results.sh .
  mv allure-results.tar.xz allure-results-step3.tar.xz

#
# FINISH - teardown cluster, generate reports
#

elif [ "$1" = "finish" ]; then
  # stop local cluster
  "$CLUSTER_SCRIPTS_DIR"/stop-cluster-hfc

  # generate CLI coverage reports
  ./.github/cli_coverage.sh .

  retval=0
fi

exit "$retval"
