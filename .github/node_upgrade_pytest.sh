#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "Error at line $LINENO"' ERR

retval=0

export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"
STATE_CLUSTER="${CARDANO_NODE_SOCKET_PATH_CI%/*}"

# default era to use, can be overridden in each step if needed
export CLUSTER_ERA="${CLUSTER_ERA:-"conway"}"
export COMMAND_ERA="${COMMAND_ERA:-"$CLUSTER_ERA"}"
CLUSTER_SCRIPTS_DIR="$WORKDIR/cluster0_${CLUSTER_ERA}"

# init dir for step1 binaries
STEP1_BIN="$WORKDIR/step1-bin"

# init reports dir before each step
export REPORTS_DIR="${REPORTS_DIR:-".reports"}"
rm -rf "${REPORTS_DIR:?}"
mkdir -p "$REPORTS_DIR"

# shellcheck disable=SC1090,SC1091
. .github/common.sh

#
# STEP1 - start local cluster and run smoke tests for the first time
#

if [ "$1" = "step1" ]; then
  printf "STEP1 start: %(%H:%M:%S)T\n" -1

  export UPGRADE_TESTS_STEP=1

  if [ -n "${BASE_TAR_URL:-""}" ]; then
    # download and extract base revision binaries
    BASE_TAR_FILE="$WORKDIR/base_rev.tar.gz"
    curl -sSL "$BASE_TAR_URL" > "$BASE_TAR_FILE" || exit 6
    mkdir -p "$WORKDIR/base_rev"
    tar -C "$WORKDIR/base_rev" -xzf "$BASE_TAR_FILE" || exit 6
    rm -f "$BASE_TAR_FILE"
    # add base revision binaries to the PATH
    BASE_REV_BIN="$WORKDIR/base_rev/bin"
    mkdir -p "$BASE_REV_BIN"
    # TODO: there seems to be a change of layout of the archive. Since 10.2.0, there is already a `bin`
    # dir and the binaries are already placed there.
    if [ ! -e "$BASE_REV_BIN/cardano-node" ]; then
      ln -s "$WORKDIR/base_rev/cardano-node" "$BASE_REV_BIN/cardano-node"
      ln -s "$WORKDIR/base_rev/cardano-cli" "$BASE_REV_BIN/cardano-cli"
    fi
    export PATH="${BASE_REV_BIN}:${PATH}"
  fi

  if is_truthy "${CI_BYRON_CLUSTER:-}"; then
    : "${TESTNET_VARIANT:="${CLUSTER_ERA}_slow"}"
  else
    : "${TESTNET_VARIANT:="${CLUSTER_ERA}_fast"}"
  fi
  export TESTNET_VARIANT

  # generate local cluster scripts
  prepare-cluster-scripts \
    -d "$CLUSTER_SCRIPTS_DIR" \
    -t "$TESTNET_VARIANT"

  # try to stop local cluster
  "$CLUSTER_SCRIPTS_DIR/stop-cluster"
  # start local cluster
  "$CLUSTER_SCRIPTS_DIR/start-cluster" || exit 6

  # backup the original cardano binaries
  mkdir -p "$STEP1_BIN"
  ln -s "$(command -v cardano-node)" "$STEP1_BIN/cardano-node-step1"
  ln -s "$(command -v cardano-cli)" "$STEP1_BIN/cardano-cli-step1"

  # backup the original genesis files
  cp -f "$STATE_CLUSTER/shelley/genesis.alonzo.json" "$STATE_CLUSTER/shelley/genesis.alonzo.step1.json"
  cp -f "$STATE_CLUSTER/shelley/genesis.conway.json" "$STATE_CLUSTER/shelley/genesis.conway.step1.json"

  # run smoke tests
  printf "STEP1 tests: %(%H:%M:%S)T\n" -1
  retval=0
  pytest \
    cardano_node_tests \
    -n "$TEST_THREADS" \
    -m "smoke or upgrade_step1" \
    --artifacts-base-dir="$ARTIFACTS_DIR" \
    --cli-coverage-dir="$COVERAGE_DIR" \
    --alluredir="$REPORTS_DIR" \
    --html=testrun-report-step1.html \
    --self-contained-html \
    || retval="$?"

  # stop local cluster if tests failed unexpectedly
  [ "$retval" -le 1 ] || "$CLUSTER_SCRIPTS_DIR/stop-cluster"

  # create results archive for step1
  ./.github/create_results.sh .
  mv allure-results.tar.xz allure-results-step1.tar.xz

  printf "STEP1 finish: %(%H:%M:%S)T\n" -1

#
# STEP2 - partly update local cluster and run smoke tests for the second time.
# The pool3 will continue running with the original cardano-node binary.
#

elif [ "$1" = "step2" ]; then
  printf "STEP2 start: %(%H:%M:%S)T\n" -1

  export UPGRADE_TESTS_STEP=2

  NETWORK_MAGIC="$(jq '.networkMagic' "$STATE_CLUSTER/shelley/genesis.json")"
  export NETWORK_MAGIC

  # add binaries saved in step1 to the PATH
  export PATH="${STEP1_BIN}:${PATH}"

  # re-generate config and topology files
  CARDANO_NODE_SOCKET_PATH="$WORKDIR/dry_config_step2/state-cluster0/bft1.socket" \
    DRY_RUN=true \
    "$CLUSTER_SCRIPTS_DIR/start-cluster"

  # copy newly generated topology files to the cluster state dir
  cp -f "$WORKDIR"/dry_config_step2/state-cluster0/topology-*.json "$STATE_CLUSTER"

  if [ -n "${REPLACE_GENESIS_STEP2:-""}" ]; then
    # Copy newly generated Alonzo genesis to the cluster state dir
    cp -f "$WORKDIR/dry_config_step2/state-cluster0/shelley/genesis.alonzo.json" "$STATE_CLUSTER/shelley"

    # Copy newly generated Conway genesis file to the cluster state dir, use committee members from the original
    # Conway genesis.
    jq \
      --argfile src "$STATE_CLUSTER/shelley/genesis.conway.step1.json" \
      '.committee.members = $src.committee.members' \
      "$WORKDIR/dry_config_step2/state-cluster0/shelley/genesis.conway.json" > "$STATE_CLUSTER/shelley/genesis.conway.json"
  fi

  # use the original Byron, Shelley and Dijkstra genesis files
  BYRON_GENESIS_HASH="$(jq -r ".ByronGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  SHELLEY_GENESIS_HASH="$(jq -r ".ShelleyGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  # hashes of the original alonzo and conway genesis files
  CONWAY_GENESIS_STEP1_HASH="$(jq -r ".ConwayGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  ALONZO_GENESIS_STEP1_HASH="$(jq -r ".AlonzoGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  # hashes of genesis files that were potentially replaced
  ALONZO_GENESIS_HASH="$(cardano-cli latest genesis hash --genesis \
    "$STATE_CLUSTER/shelley/genesis.alonzo.json")"
  CONWAY_GENESIS_HASH="$(cardano-cli latest genesis hash --genesis \
    "$STATE_CLUSTER/shelley/genesis.conway.json")"

  # copy newly generated config files to the cluster state dir
  for conf in "$WORKDIR"/dry_config_step2/state-cluster0/config-*.json; do
    fname="${conf##*/}"

    if [ "$fname" = "config-pool3.json" ]; then
      # use old Alonzo and Conway genesis on pool3
      selected_alonzo_hash="$ALONZO_GENESIS_STEP1_HASH"
      selected_alonzo_file="shelley/genesis.alonzo.step1.json"
      selected_conway_hash="$CONWAY_GENESIS_STEP1_HASH"
      selected_conway_file="shelley/genesis.conway.step1.json"
    else
      # use new Alonzo and Conway genesis on upgraded nodes
      selected_alonzo_hash="$ALONZO_GENESIS_HASH"
      selected_alonzo_file="shelley/genesis.alonzo.json"
      selected_conway_hash="$CONWAY_GENESIS_HASH"
      selected_conway_file="shelley/genesis.conway.json"
    fi

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
  done

  # run the pool3 with the original cardano-node binary
  cp -f "$STATE_CLUSTER/cardano-node-pool3" "$STATE_CLUSTER/cardano-node-pool3.orig"
  sed -i 's/cardano-node run/cardano-node-step1 run/' "$STATE_CLUSTER/cardano-node-pool3"

  # Restart local cluster nodes with binaries from new cluster-node version.
  # It is necessary to restart supervisord with new environment.
  "$STATE_CLUSTER/supervisorctl" stop all
  sleep 5
  "$STATE_CLUSTER/stop-cluster"
  sleep 3
  "$STATE_CLUSTER/supervisord_start" || exit 6
  sleep 5
  "$STATE_CLUSTER/supervisorctl" start all
  sleep 5
  "$STATE_CLUSTER/supervisorctl" status

  # print path to cardano-node binaries
  echo "pool1 node binary:"
  pool1_pid="$("$STATE_CLUSTER/supervisorctl" pid nodes:pool1)"
  readlink -m "/proc/$pool1_pid/exe"
  echo "pool3 node binary:"
  pool3_pid="$("$STATE_CLUSTER/supervisorctl" pid nodes:pool3)"
  readlink -m "/proc/$pool3_pid/exe"

  # check that nodes are running
  if [ "$pool1_pid" = 0 ] || [ "$pool3_pid" = 0 ]; then
    echo "Failed to start node" >&2
    exit 6
  fi

  # Tx submission delay
  sleep 60

  # waiting to make sure the chain is synced
  for _ in {1..10}; do
    sync_progress="$(cardano-cli latest query tip \
      --testnet-magic "$NETWORK_MAGIC" | jq -r '.syncProgress')"
    if [ "$sync_progress" = "100.00" ]; then
      break
    fi
    sleep 5
  done
  [ "$sync_progress" = "100.00" ] || { echo "Failed to sync node" >&2; exit 6; }  # assert

  # Test for ignoring expected errors in log files. Run separately to make sure it runs first.
  err_retval=0
  pytest cardano_node_tests/tests/test_node_upgrade.py -k test_ignore_log_errors || err_retval="$?"

  # Update Plutus cost models.
  pytest cardano_node_tests/tests/test_node_upgrade.py -k test_update_cost_models || exit 6

  # run smoke tests
  printf "STEP2 tests: %(%H:%M:%S)T\n" -1
  retval=0
  pytest \
    cardano_node_tests \
    -n "$TEST_THREADS" \
    -m "smoke or upgrade_step2" \
    --artifacts-base-dir="$ARTIFACTS_DIR" \
    --cli-coverage-dir="$COVERAGE_DIR" \
    --alluredir="$REPORTS_DIR" \
    --html=testrun-report-step2.html \
    --self-contained-html \
    ||retval="$?"

  # stop local cluster if tests failed unexpectedly
  [ "$retval" -le 1 ] || "$CLUSTER_SCRIPTS_DIR/stop-cluster"

  # create results archive for step2
  ./.github/create_results.sh .
  mv allure-results.tar.xz allure-results-step2.tar.xz

  [ "$err_retval" -gt "$retval" ] && retval=1

  printf "STEP2 finish: %(%H:%M:%S)T\n" -1


#
# STEP3 - finish update of local cluster and run smoke tests for the third time
#

elif [ "$1" = "step3" ]; then
  printf "STEP3 start: %(%H:%M:%S)T\n" -1

  export UPGRADE_TESTS_STEP=3

  NETWORK_MAGIC="$(jq '.networkMagic' "$STATE_CLUSTER/shelley/genesis.json")"
  export NETWORK_MAGIC

  # re-generate config and topology files
  CARDANO_NODE_SOCKET_PATH="$WORKDIR/dry_config_step3/state-cluster0/bft1.socket" \
    ENABLE_EXPERIMENTAL=true \
    DRY_RUN=true \
    "$CLUSTER_SCRIPTS_DIR/start-cluster"

  # copy newly generated topology files to the cluster state dir
  cp -f "$WORKDIR"/dry_config_step3/state-cluster0/topology-*.json "$STATE_CLUSTER"

  # Copy newly generated config files to the cluster state dir, but use the original genesis files
  BYRON_GENESIS_HASH="$(jq -r ".ByronGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  SHELLEY_GENESIS_HASH="$(jq -r ".ShelleyGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  ALONZO_GENESIS_HASH="$(jq -r ".AlonzoGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  CONWAY_GENESIS_HASH="$(jq -r ".ConwayGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  DIJKSTRA_GENESIS_HASH="$(jq -r ".DijkstraGenesisHash" "$STATE_CLUSTER/config-bft1.json")"
  if [ -z "$DIJKSTRA_GENESIS_HASH" ] || [ "$DIJKSTRA_GENESIS_HASH" = "null" ]; then
    cp -f "$WORKDIR/dry_config_step3/state-cluster0/shelley/genesis.dijkstra.json" "$STATE_CLUSTER/shelley"
    DIJKSTRA_GENESIS_HASH="$(cardano-cli latest genesis hash --genesis \
      "$STATE_CLUSTER/shelley/genesis.dijkstra.json")"
  fi
  for conf in "$WORKDIR"/dry_config_step3/state-cluster0/config-*.json; do
    fname="${conf##*/}"
    jq \
      --arg byron_hash "$BYRON_GENESIS_HASH" \
      --arg shelley_hash "$SHELLEY_GENESIS_HASH" \
      --arg alonzo_hash "$ALONZO_GENESIS_HASH" \
      --arg conway_hash "$CONWAY_GENESIS_HASH" \
      --arg dijkstra_hash "$DIJKSTRA_GENESIS_HASH" \
      '.ByronGenesisHash = $byron_hash
      | .ShelleyGenesisHash = $shelley_hash
      | .AlonzoGenesisHash = $alonzo_hash
      | .ConwayGenesisHash = $conway_hash
      | .DijkstraGenesisHash = $dijkstra_hash' \
      "$conf" > "$STATE_CLUSTER/$fname"
  done

  # use the upgraded cardano-node binary for pool3
  cp -f "$STATE_CLUSTER/cardano-node-pool3.orig" "$STATE_CLUSTER/cardano-node-pool3"

  # restart all nodes
  "$STATE_CLUSTER/supervisorctl" restart nodes:
  sleep 10
  "$STATE_CLUSTER/supervisorctl" status

  # print path to cardano-node binaries
  echo "pool1 node binary:"
  pool1_pid="$("$STATE_CLUSTER/supervisorctl" pid nodes:pool1)"
  readlink -m "/proc/$pool1_pid/exe"
  echo "pool3 node binary:"
  pool3_pid="$("$STATE_CLUSTER/supervisorctl" pid nodes:pool3)"
  readlink -m "/proc/$pool3_pid/exe"

  # check that nodes are running
  if [ "$pool1_pid" = 0 ] || [ "$pool3_pid" = 0 ]; then
    echo "Failed to start node" >&2
    exit 6
  fi

  # Tx submission delay
  sleep 60

  # waiting to make sure the chain on pool3 is synced
  for _ in {1..10}; do
    sync_progress="$(cardano-cli latest query tip \
      --testnet-magic "$NETWORK_MAGIC" \
      --socket-path "${STATE_CLUSTER}/pool3.socket" | jq -r '.syncProgress')"
    if [ "$sync_progress" = "100.00" ]; then
      break
    fi
    sleep 5
  done
  [ "$sync_progress" = "100.00" ] || { echo "Failed to sync node" >&2; exit 6; }  # assert

  # Test for ignoring expected errors in log files. Run separately to make sure it runs first.
  err_retval=0
  pytest cardano_node_tests/tests/test_node_upgrade.py -k test_ignore_log_errors || err_retval="$?"

  # Hard fork to PV10.
  pytest cardano_node_tests/tests/test_node_upgrade.py -k test_hardfork || exit 6

  # Run smoke tests
  printf "STEP3 tests: %(%H:%M:%S)T\n" -1
  retval=0
  pytest \
    cardano_node_tests \
    -n "$TEST_THREADS" \
    -m "smoke or upgrade_step3" \
    --artifacts-base-dir="$ARTIFACTS_DIR" \
    --cli-coverage-dir="$COVERAGE_DIR" \
    --alluredir="$REPORTS_DIR" \
    --html=testrun-report-step3.html \
    --self-contained-html \
    ||retval="$?"

  # create results archive for step3
  ./.github/create_results.sh .
  mv allure-results.tar.xz allure-results-step3.tar.xz

  [ "$err_retval" -gt "$retval" ] && retval=1

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
