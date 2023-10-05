#!/usr/bin/env bash

set -xuo pipefail

retval=1

export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"

export CLUSTER_ERA="${CLUSTER_ERA:-"babbage"}"
export TX_ERA="$CLUSTER_ERA"

CLUSTER_SCRIPTS_DIR="$WORKDIR/cluster0_${CLUSTER_ERA}"
STATE_CLUSTER="${CARDANO_NODE_SOCKET_PATH_CI%/*}"

# init dir for step1 binaries
STEP1_BIN="$WORKDIR/step1-bin"
mkdir -p "$STEP1_BIN"

# init reports dir before each step
export REPORTS_DIR="${REPORTS_DIR:-".reports"}"
rm -rf "${REPORTS_DIR:?}"
mkdir -p "$REPORTS_DIR"

#
# STEP1 - start local cluster and run smoke tests for the first time
#

if [ "$1" = "step1" ]; then
  printf "STEP1 start: %(%H:%M:%S)T\n" -1

  export UPGRADE_TESTS_STEP=1

  if [ -n "${BASE_TAR_URL:-""}" ]; then
    # download and extract base revision binaries
    BASE_TAR_FILE="$WORKDIR/base_rev.tar.gz"
    curl -sSL "$BASE_TAR_URL" > "$BASE_TAR_FILE"
    mkdir -p "${WORKDIR}/base_rev"
    tar -C "${WORKDIR}/base_rev" -xzf "$BASE_TAR_FILE"
    rm -f "$BASE_TAR_FILE"
    # add base revision binaries to the PATH
    BASE_REV_BIN="${WORKDIR}/base_rev/bin"
    mkdir -p "$BASE_REV_BIN"
    ln -s "${WORKDIR}/base_rev/cardano-node" "$BASE_REV_BIN/cardano-node"
    ln -s "${WORKDIR}/base_rev/cardano-cli" "$BASE_REV_BIN/cardano-cli"
    export PATH="${BASE_REV_BIN}:${PATH}"
  fi

  if [ "${CI_FAST_CLUSTER:-"false"}" != "false" ]; then
    : "${SCRIPTS_DIRNAME:="${CLUSTER_ERA}_fast"}"
  else
    : "${SCRIPTS_DIRNAME:="$CLUSTER_ERA"}"
  fi
  export SCRIPTS_DIRNAME

  # generate local cluster scripts
  PYTHONPATH=$PYTHONPATH:$PWD cardano_node_tests/prepare_cluster_scripts.py \
    -s "cardano_node_tests/cluster_scripts/$SCRIPTS_DIRNAME" \
    -d "$CLUSTER_SCRIPTS_DIR"

  # try to stop local cluster
  "$CLUSTER_SCRIPTS_DIR/stop-cluster"
  # start local cluster
  "$CLUSTER_SCRIPTS_DIR/start-cluster" || exit 6

  # backup the original cardano binaries
  ln -s "$(command -v cardano-node)" "$STEP1_BIN/cardano-node-step1"
  ln -s "$(command -v cardano-cli)" "$STEP1_BIN/cardano-cli-step1"

  # backup the original genesis files
  cp -f "$STATE_CLUSTER/shelley/genesis.alonzo.json" "$STATE_CLUSTER/shelley/genesis.alonzo-step1.json"
  if [ -e "$STATE_CLUSTER/shelley/genesis.conway.json" ]; then
    cp -f "$STATE_CLUSTER/shelley/genesis.conway.json" "$STATE_CLUSTER/shelley/genesis.conway-step1.json"
  fi

  # run smoke tests
  pytest \
    cardano_node_tests \
    -n "$TEST_THREADS" \
    -m "smoke or upgrade" \
    --artifacts-base-dir="$ARTIFACTS_DIR" \
    --cli-coverage-dir="$COVERAGE_DIR" \
    --alluredir="$REPORTS_DIR" \
    --html=testrun-report-step1.html \
    --self-contained-html
  retval="$?"

  # stop local cluster if tests failed unexpectedly
  [ "$retval" -le 1 ] || "$CLUSTER_SCRIPTS_DIR/stop-cluster"

  # create results archive for step1
  ./.github/results.sh .
  mv allure-results.tar.xz allure-results-step1.tar.xz

  printf "STEP1 finish: %(%H:%M:%S)T\n" -1

#
# STEP2 - partly update local cluster and run smoke tests for the second time.
# The pool3 will continue running with the original cardano-node binary.
#

elif [ "$1" = "step2" ]; then
  printf "STEP2 start: %(%H:%M:%S)T\n" -1

  export UPGRADE_TESTS_STEP=2

  # add binaries saved in step1 to the PATH
  export PATH="${STEP1_BIN}:${PATH}"

  # generate config and topology files for "mixed" mode
  CARDANO_NODE_SOCKET_PATH="$WORKDIR/dry_mixed/state-cluster0/bft1.socket" \
    MIXED_P2P=1 \
    DRY_RUN=1 \
    "$CLUSTER_SCRIPTS_DIR/start-cluster"

  CONWAY_GENESIS_STEP1_HASH=""
  if [ -e "$STATE_CLUSTER/shelley/genesis.conway.json" ]; then
    CONWAY_GENESIS_STEP1_HASH="$(jq -r ".ConwayGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  fi

  # copy newly generated topology files to the cluster state dir
  cp -f "$WORKDIR"/dry_mixed/state-cluster0/topology-*.json "$STATE_CLUSTER"

  # copy newly generated Alonzo genesis to the cluster state dir
  cp -f "$WORKDIR/dry_mixed/state-cluster0/shelley/genesis.alonzo.json" "$STATE_CLUSTER/shelley"

  # copy newly generated Conway genesis file to the cluster state dir
  cp -f "$WORKDIR/dry_mixed/state-cluster0/shelley/genesis.conway.json" "$STATE_CLUSTER/shelley"

  # copy newly generated config files to the cluster state dir, but use the original genesis files
  BYRON_GENESIS_HASH="$(jq -r ".ByronGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  SHELLEY_GENESIS_HASH="$(jq -r ".ShelleyGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  CONWAY_GENESIS_HASH="$(jq -r ".ConwayGenesisHash" "$WORKDIR/dry_mixed/state-cluster0/config-bft1.json")"
  # hashes of old and new Alonzo genesis files
  ALONZO_GENESIS_HASH="$(jq -r ".AlonzoGenesisHash" "$WORKDIR/dry_mixed/state-cluster0/config-bft1.json")"
  ALONZO_GENESIS_STEP1_HASH="$(jq -r ".AlonzoGenesisHash" "$STATE_CLUSTER/config-bft1.json")"

  for conf in "$WORKDIR"/dry_mixed/state-cluster0/config-*.json; do
    fname="${conf##*/}"

    if [ "$fname" = "config-pool3.json" ]; then
      # use old Alonzo genesis on pool3
      selected_alonzo_hash="$ALONZO_GENESIS_STEP1_HASH"
      selected_alonzo_file="shelley/genesis.alonzo-step1.json"
      selected_conway_hash="$CONWAY_GENESIS_STEP1_HASH"
      selected_conway_file="shelley/genesis.conway-step1.json"
    else
      # use new Alonzo genesis on upgraded nodes
      selected_alonzo_hash="$ALONZO_GENESIS_HASH"
      selected_alonzo_file="shelley/genesis.alonzo.json"
      selected_conway_hash="$CONWAY_GENESIS_HASH"
      selected_conway_file="shelley/genesis.conway.json"
    fi

    # If the base revision doesn't have conway genesis and the config file is for pool3,
    # then don't add conway hash.
    if [[ "$fname" = "config-pool3.json" && "$selected_conway_hash" = "" ]]; then
      jq \
        --arg byron_hash "$BYRON_GENESIS_HASH" \
        --arg shelley_hash "$SHELLEY_GENESIS_HASH" \
        --arg alonzo_file "$selected_alonzo_file" \
        --arg alonzo_hash "$selected_alonzo_hash" \
        '.ByronGenesisHash = $byron_hash
        | .ShelleyGenesisHash = $shelley_hash
        | .AlonzoGenesisFile = $alonzo_file
        | .AlonzoGenesisHash = $alonzo_hash' \
        "$conf" > "$STATE_CLUSTER/$fname"
    else
      jq \
        --arg byron_hash "$BYRON_GENESIS_HASH" \
        --arg shelley_hash "$SHELLEY_GENESIS_HASH" \
        --arg alonzo_file "$selected_alonzo_file" \
        --arg alonzo_hash "$selected_alonzo_hash" \
        --arg conway_file "$selected_conway_file" \
        --arg conway_hash "$selected_conway_hash" \
        '.ByronGenesisHash = $byron_hash
        | .ShelleyGenesisHash = $shelley_hash
        | .AlonzoGenesisFile = $alonzo_file
        | .AlonzoGenesisHash = $alonzo_hash
        | .ConwayGenesisFile = $conway_file
        | .ConwayGenesisHash = $conway_hash' \
        "$conf" > "$STATE_CLUSTER/$fname"
    fi
  done

  # run the "pool3" with the original cardano-node binary
  cp -a "$STATE_CLUSTER/cardano-node-pool3" "$STATE_CLUSTER/cardano-node-pool3.orig"
  sed -i 's/cardano-node run/cardano-node-step1 run/' "$STATE_CLUSTER/cardano-node-pool3"

  # Restart local cluster nodes with binaries from new cluster-node version.
  # It is necessary to restart supervisord with new environment.
  "$STATE_CLUSTER/supervisord_stop"
  sleep 3
  "$STATE_CLUSTER/supervisord_start" || exit 6
  sleep 5
  "$STATE_CLUSTER/supervisorctl" start all
  sleep 5
  "$STATE_CLUSTER/supervisorctl" status

  # print path to cardano-node binaries
  pool1_pid="$("$STATE_CLUSTER/supervisorctl" pid nodes:pool1)"
  ls -l "/proc/$pool1_pid/exe"
  pool3_pid="$("$STATE_CLUSTER/supervisorctl" pid nodes:pool3)"
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
  pytest \
    cardano_node_tests \
    -n "$TEST_THREADS" \
    -m "smoke or upgrade" \
    --artifacts-base-dir="$ARTIFACTS_DIR" \
    --cli-coverage-dir="$COVERAGE_DIR" \
    --alluredir="$REPORTS_DIR" \
    --html=testrun-report-step2.html \
    --self-contained-html
  retval="$?"

  # stop local cluster if tests failed unexpectedly
  [ "$retval" -le 1 ] || "$CLUSTER_SCRIPTS_DIR/stop-cluster"

  # create results archive for step2
  ./.github/results.sh .
  mv allure-results.tar.xz allure-results-step2.tar.xz

  printf "STEP2 finish: %(%H:%M:%S)T\n" -1


#
# STEP3 - finish update of local cluster and run smoke tests for the third time
#

elif [ "$1" = "step3" ]; then
  printf "STEP3 start: %(%H:%M:%S)T\n" -1

  export UPGRADE_TESTS_STEP=3

  # generate config and topology files for p2p mode
  CARDANO_NODE_SOCKET_PATH="$WORKDIR/dry_p2p/state-cluster0/bft1.socket" \
    ENABLE_P2P=1 \
    DRY_RUN=1 \
    "$CLUSTER_SCRIPTS_DIR/start-cluster"

  # copy newly generated topology files to the cluster state dir
  cp -f "$WORKDIR"/dry_p2p/state-cluster0/topology-*.json "$STATE_CLUSTER"

  # copy newly generated config files to the cluster state dir, but use the original genesis files
  BYRON_GENESIS_HASH="$(jq -r ".ByronGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  SHELLEY_GENESIS_HASH="$(jq -r ".ShelleyGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  ALONZO_GENESIS_HASH="$(jq -r ".AlonzoGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  CONWAY_GENESIS_HASH="$(jq -r ".ConwayGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  for conf in "$WORKDIR"/dry_p2p/state-cluster0/config-*.json; do
    fname="${conf##*/}"
    jq \
      --arg byron_hash "$BYRON_GENESIS_HASH" \
      --arg shelley_hash "$SHELLEY_GENESIS_HASH" \
      --arg alonzo_hash "$ALONZO_GENESIS_HASH" \
      --arg conway_hash "$CONWAY_GENESIS_HASH" \
      '.ByronGenesisHash = $byron_hash
      | .ShelleyGenesisHash = $shelley_hash
      | .AlonzoGenesisHash = $alonzo_hash
      | .ConwayHash = $conway_hash' \
      "$conf" > "$STATE_CLUSTER/$fname"
  done

  # use the upgraded cardano-node binary for pool3
  cp -a "$STATE_CLUSTER/cardano-node-pool3.orig" "$STATE_CLUSTER/cardano-node-pool3"

  # restart all nodes
  "$STATE_CLUSTER/supervisorctl" restart nodes:
  sleep 10
  "$STATE_CLUSTER/supervisorctl" status

  # print path to cardano-node binaries
  pool1_pid="$("$STATE_CLUSTER/supervisorctl" pid nodes:pool1)"
  ls -l "/proc/$pool1_pid/exe"
  pool3_pid="$("$STATE_CLUSTER/supervisorctl" pid nodes:pool3)"
  ls -l "/proc/$pool3_pid/exe"

  # Test for ignoring expected errors in log files. Run separately to make sure it runs first.
  pytest cardano_node_tests/tests/test_node_upgrade.py -k test_ignore_log_errors

  # run smoke tests
  pytest \
    cardano_node_tests \
    -n "$TEST_THREADS" \
    -m "smoke or upgrade" \
    --artifacts-base-dir="$ARTIFACTS_DIR" \
    --cli-coverage-dir="$COVERAGE_DIR" \
    --alluredir="$REPORTS_DIR" \
    --html=testrun-report-step3.html \
    --self-contained-html
  retval="$?"

  # create results archive for step3
  ./.github/results.sh .
  mv allure-results.tar.xz allure-results-step3.tar.xz

  printf "STEP3 finish: %(%H:%M:%S)T\n" -1

#
# FINISH - teardown cluster, generate reports
#

elif [ "$1" = "finish" ]; then
  # stop local cluster
  "$CLUSTER_SCRIPTS_DIR/stop-cluster"

  # generate CLI coverage reports
  ./.github/cli_coverage.sh .

  retval=0
fi

exit "$retval"
