#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "Error at line $LINENO"' ERR

retval=0

export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"
state_cluster="${CARDANO_NODE_SOCKET_PATH_CI%/*}"

# default era to use, can be overridden in each step if needed
cluster_era="conway"
export COMMAND_ERA="${COMMAND_ERA:-"$cluster_era"}"

: "${WORKDIR:?WORKDIR environment variable must be set}"
cluster_scripts_dir="$WORKDIR/cluster0_${cluster_era}"

# init dir for step1 binaries
readonly STEP1_BIN="$WORKDIR/step1-bin"

# shellcheck disable=SC1091
. scripts/common.sh

#
# STEP1 - start local cluster and run smoke tests for the first time
#

if [ "$1" = "step1" ]; then
  printf "STEP1 start: %(%H:%M:%S)T\n" -1

  export UPGRADE_TESTS_STEP=1

  reports_dir="${WORKDIR}/reports-step1"
  mkdir -p "$reports_dir"

  if [ -n "${BASE_TAR_URL:-""}" ]; then
    # download and extract base revision binaries
    base_tar_file="$WORKDIR/base_rev.tar.gz"
    curl -sSL "$BASE_TAR_URL" > "$base_tar_file" || exit 6
    mkdir -p "$WORKDIR/base_rev"
    tar -C "$WORKDIR/base_rev" -xzf "$base_tar_file" || exit 6
    rm -f "$base_tar_file"
    # add base revision binaries to the PATH
    base_rev_bin="$WORKDIR/base_rev/bin"
    mkdir -p "$base_rev_bin"
    # TODO: there seems to be a change of layout of the archive. Since 10.2.0, there is already a `bin`
    # dir and the binaries are already placed there.
    if [ ! -e "$base_rev_bin/cardano-node" ]; then
      ln -s "$WORKDIR/base_rev/cardano-node" "$base_rev_bin/cardano-node"
      ln -s "$WORKDIR/base_rev/cardano-cli" "$base_rev_bin/cardano-cli"
    fi
    export PATH="${base_rev_bin}:${PATH}"
  fi

  if is_truthy "${CI_BYRON_CLUSTER:-}"; then
    : "${TESTNET_VARIANT:="local_slow"}"
  else
    : "${TESTNET_VARIANT:="local_fast"}"
  fi
  export TESTNET_VARIANT

  # generate local cluster scripts
  prepare-cluster-scripts \
    -d "$cluster_scripts_dir" \
    -t "$TESTNET_VARIANT"

  # try to stop local cluster
  "$cluster_scripts_dir/stop-cluster"
  # start local cluster
  "$cluster_scripts_dir/start-cluster" || exit 6

  # backup the original cardano binaries
  mkdir -p "$STEP1_BIN"
  ln -s "$(command -v cardano-node)" "$STEP1_BIN/cardano-node-step1"
  ln -s "$(command -v cardano-cli)" "$STEP1_BIN/cardano-cli-step1"

  # backup the original config file
  cp -f "$state_cluster/config-pool3.json" "$state_cluster/config-pool3.step1.json"

  # run smoke tests
  printf "STEP1 tests: %(%H:%M:%S)T\n" -1
  retval=0
  pytest \
    cardano_node_tests \
    -n "$TEST_THREADS" \
    -m "smoke or upgrade_step1" \
    --artifacts-base-dir="$ARTIFACTS_DIR" \
    --cli-coverage-dir="$COVERAGE_DIR" \
    --alluredir="$reports_dir" \
    --html="$WORKDIR/testrun-report-step1.html" \
    --self-contained-html \
    || retval="$?"

  # stop local cluster if tests failed unexpectedly
  [ "$retval" -le 1 ] || "$cluster_scripts_dir/stop-cluster"

  # create results archive for step1
  ./runner/create_results.sh "$reports_dir" "$WORKDIR" allure-results-step1

  printf "STEP1 finish: %(%H:%M:%S)T\n" -1

#
# STEP2 - partly update local cluster and run smoke tests for the second time.
# The pool3 will continue running with the original cardano-node binary.
#

elif [ "$1" = "step2" ]; then
  printf "STEP2 start: %(%H:%M:%S)T\n" -1

  export UPGRADE_TESTS_STEP=2

  reports_dir="${WORKDIR}/reports-step2"
  mkdir -p "$reports_dir"

  NETWORK_MAGIC="$(jq -r '.networkMagic' "$state_cluster/shelley/genesis.json")"
  export NETWORK_MAGIC

  # add binaries saved in step1 to the PATH
  export PATH="${STEP1_BIN}:${PATH}"

  # re-generate config and topology files
  CARDANO_NODE_SOCKET_PATH="$WORKDIR/dry_config_step2/state-cluster0/bft1.socket" \
    PROTOCOL_VERSION=11 \
    DRY_RUN=true \
    "$cluster_scripts_dir/start-cluster"


  # use the original genesis files
  byron_genesis_hash="$(jq -r '.ByronGenesisHash' "$state_cluster/config-bft1.json")"
  shelley_genesis_hash="$(jq -r '.ShelleyGenesisHash' "$state_cluster/config-bft1.json")"
  alonzo_genesis_hash="$(jq -r '.AlonzoGenesisHash' "$state_cluster/config-bft1.json")"
  conway_genesis_hash="$(jq -r '.ConwayGenesisHash' "$state_cluster/config-bft1.json")"

  # copy newly generated config files to the cluster state dir
  for conf in "$WORKDIR"/dry_config_step2/state-cluster0/config-*.json; do
    fname="${conf##*/}"
    nodename="${fname#config-}"
    nodename="${nodename%.json}"

    # use new config on upgraded nodes
    if [ "$fname" != "config-pool3.json" ]; then
      jq \
        --arg byron_hash "$byron_genesis_hash" \
        --arg shelley_hash "$shelley_genesis_hash" \
        --arg alonzo_file "shelley/genesis.alonzo.json" \
        --arg alonzo_hash "$alonzo_genesis_hash" \
        --arg conway_file "shelley/genesis.conway.json" \
        --arg conway_hash "$conway_genesis_hash" '
        .ByronGenesisHash = $byron_hash
        | .ShelleyGenesisHash = $shelley_hash
        | .AlonzoGenesisFile = $alonzo_file
        | .AlonzoGenesisHash = $alonzo_hash
        | .ConwayGenesisFile = $conway_file
        | .ConwayGenesisHash = $conway_hash
        | .ExperimentalHardForksEnabled = false
        ' "$conf" > "$state_cluster/$fname"
    fi

    # copy newly generated topology files to the cluster state dir
    cp -f "${WORKDIR}/dry_config_step2/state-cluster0/topology-${nodename}.json" "$state_cluster"
  done

  # run the pool3 with the original cardano-node binary
  cp -f "$state_cluster/cardano-node-pool3" "$state_cluster/cardano-node-pool3.orig"
  sed -i 's/^exec cardano-node /exec cardano-node-step1 /' "$state_cluster/cardano-node-pool3"
  grep -q '^exec cardano-node-step1 ' "$state_cluster/cardano-node-pool3" || {
    echo "Failed to switch pool3 to the original cardano-node binary" >&2
    exit 6
  }

  # Restart local cluster nodes with binaries from new cluster-node version.
  # It is necessary to restart supervisord with new environment.
  "$state_cluster/supervisorctl_local" stop all
  sleep 5
  "$state_cluster/stop-cluster"
  sleep 3
  "$state_cluster/supervisord_start" || exit 6
  sleep 5
  "$state_cluster/supervisorctl_local" start all
  sleep 5
  "$state_cluster/supervisorctl_local" status

  # print path to cardano-node binaries
  echo "pool1 node binary:"
  pool1_pid="$("$state_cluster/supervisorctl_local" pid nodes:pool1)"
  pool1_bin="$(readlink -f "/proc/$pool1_pid/exe")"
  echo "$pool1_bin"
  echo "pool3 node binary:"
  pool3_pid="$("$state_cluster/supervisorctl_local" pid nodes:pool3)"
  pool3_bin="$(readlink -f "/proc/$pool3_pid/exe")"
  echo "$pool3_bin"

  # check that nodes are running
  if [ "$pool1_pid" = 0 ] || [ "$pool3_pid" = 0 ]; then
    echo "Failed to start node" >&2
    exit 6
  fi

  # check that pool3 is still running the original (base) binary, not the upgraded one
  if [ "$pool1_bin" = "$pool3_bin" ]; then
    echo "pool3 is running the same binary as pool1, expected it to stay on the base revision" >&2
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

  # generate ledger peer snapshot using old node version
  cardano-cli-step1 query ledger-peer-snapshot \
    --testnet-magic "$NETWORK_MAGIC" \
    --socket-path "$state_cluster/pool3.socket" \
    --output-json \
    --out-file "$state_cluster/peer-snapshot-base.json"
  if [ ! -e "$state_cluster/peer-snapshot-base.json" ]; then
    echo "Failed to get peer snapshot from pool3" >&2
    exit 6
  fi

  # run smoke tests
  printf "STEP2 tests: %(%H:%M:%S)T\n" -1
  retval=0
  pytest \
    cardano_node_tests \
    -n "$TEST_THREADS" \
    -m "smoke or upgrade_step2" \
    --artifacts-base-dir="$ARTIFACTS_DIR" \
    --cli-coverage-dir="$COVERAGE_DIR" \
    --alluredir="$reports_dir" \
    --html="$WORKDIR/testrun-report-step2.html" \
    --self-contained-html \
    ||retval="$?"

  # stop local cluster if tests failed unexpectedly
  [ "$retval" -le 1 ] || "$cluster_scripts_dir/stop-cluster"

  # create results archive for step2
  ./runner/create_results.sh "$reports_dir" "$WORKDIR" allure-results-step2

  [ "$err_retval" -gt "$retval" ] && retval=1

  printf "STEP2 finish: %(%H:%M:%S)T\n" -1


#
# STEP3 - finish update of local cluster and run smoke tests for the third time
#

elif [ "$1" = "step3" ]; then
  printf "STEP3 start: %(%H:%M:%S)T\n" -1

  export UPGRADE_TESTS_STEP=3

  reports_dir="${WORKDIR}/reports-step3"
  mkdir -p "$reports_dir"

  NETWORK_MAGIC="$(jq -r '.networkMagic' "$state_cluster/shelley/genesis.json")"
  export NETWORK_MAGIC

  # re-generate config and topology files
  CARDANO_NODE_SOCKET_PATH="$WORKDIR/dry_config_step3/state-cluster0/bft1.socket" \
    PROTOCOL_VERSION=11 \
    DRY_RUN=true \
    "$cluster_scripts_dir/start-cluster"

  # copy newly generated topology files to the cluster state dir
  cp -f "$WORKDIR"/dry_config_step3/state-cluster0/topology-*.json "$state_cluster"

  # Copy newly generated config files to the cluster state dir, but use the original genesis files
  byron_genesis_hash="$(jq -r '.ByronGenesisHash' "$state_cluster/config-bft1.json")"
  shelley_genesis_hash="$(jq -r '.ShelleyGenesisHash' "$state_cluster/config-bft1.json")"
  alonzo_genesis_hash="$(jq -r '.AlonzoGenesisHash' "$state_cluster/config-bft1.json")"
  conway_genesis_hash="$(jq -r '.ConwayGenesisHash' "$state_cluster/config-bft1.json")"
  dijkstra_genesis_hash="$(jq -r '.DijkstraGenesisHash' "$state_cluster/config-bft1.json")"
  if [ -z "$dijkstra_genesis_hash" ] || [ "$dijkstra_genesis_hash" = "null" ]; then
    cp -f "$WORKDIR/dry_config_step3/state-cluster0/shelley/genesis.dijkstra.json" "$state_cluster/shelley"
    dijkstra_genesis_hash="$(cardano-cli latest genesis hash --genesis \
      "$state_cluster/shelley/genesis.dijkstra.json")"
  fi
  node_ver="$(version_parse "$(get_node_version)")"
  node_v11="$(version_parse 11.0.0)"

  for conf in "$WORKDIR"/dry_config_step3/state-cluster0/config-*.json; do
    fname="${conf##*/}"
    jq \
      --arg byron_hash "$byron_genesis_hash" \
      --arg shelley_hash "$shelley_genesis_hash" \
      --arg alonzo_hash "$alonzo_genesis_hash" \
      --arg conway_hash "$conway_genesis_hash" \
      --arg dijkstra_hash "$dijkstra_genesis_hash" \
      --argjson node_ver "$node_ver" \
      --argjson node_v11 "$node_v11" '
      .ByronGenesisHash = $byron_hash
      | .ShelleyGenesisHash = $shelley_hash
      | .AlonzoGenesisHash = $alonzo_hash
      | .ConwayGenesisHash = $conway_hash
      | .DijkstraGenesisHash = $dijkstra_hash
      | .ExperimentalProtocolsEnabled = ($node_ver < $node_v11)
      ' "$conf" > "$state_cluster/$fname"
  done

  # generate ledger peer snapshot using new node version
  cardano-cli query ledger-peer-snapshot \
    --testnet-magic "$NETWORK_MAGIC" \
    --socket-path "$state_cluster/pool1.socket" \
    --output-json \
    --out-file "$state_cluster/peer-snapshot-upgrade.json"
  if [ ! -e "$state_cluster/peer-snapshot-upgrade.json" ]; then
    echo "Failed to get peer snapshot from pool1" >&2
    exit 6
  fi

  # use the base version snapshot for pool1 and upgrade version snapshot for pool3
  jq '
    .localRoots[] += {"trustable": true}
    | .peerSnapshotFile = "peer-snapshot-base.json"
    ' "$state_cluster/topology-pool1.json" > "$state_cluster/topology-pool1.tmp.json"
  mv -f "$state_cluster/topology-pool1.tmp.json" "$state_cluster/topology-pool1.json"
  jq '
    .localRoots[] += {"trustable": true}
    | .peerSnapshotFile = "peer-snapshot-upgrade.json"
    ' "$state_cluster/topology-pool3.json" > "$state_cluster/topology-pool3.tmp.json"
  mv -f "$state_cluster/topology-pool3.tmp.json" "$state_cluster/topology-pool3.json"

  # set `GenesisMode` for nodes that will use the ledger peer snapshot
  for pool in pool1 pool3; do
    jq '.ConsensusMode = "GenesisMode"' \
      "$state_cluster/config-${pool}.json" > "$state_cluster/config-${pool}.tmp.json"
    mv -f "$state_cluster/config-${pool}.tmp.json" "$state_cluster/config-${pool}.json"
  done

  # use the upgraded cardano-node binary for pool3
  cp -f "$state_cluster/cardano-node-pool3.orig" "$state_cluster/cardano-node-pool3"

  # restart all nodes
  "$state_cluster/supervisorctl_local" restart nodes:
  sleep 10
  "$state_cluster/supervisorctl_local" status

  # print path to cardano-node binaries
  echo "pool1 node binary:"
  pool1_pid="$("$state_cluster/supervisorctl_local" pid nodes:pool1)"
  readlink -f "/proc/$pool1_pid/exe"
  echo "pool3 node binary:"
  pool3_pid="$("$state_cluster/supervisorctl_local" pid nodes:pool3)"
  readlink -f "/proc/$pool3_pid/exe"

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
      --socket-path "${state_cluster}/pool3.socket" | jq -r '.syncProgress')"
    if [ "$sync_progress" = "100.00" ]; then
      break
    fi
    sleep 5
  done
  [ "$sync_progress" = "100.00" ] || { echo "Failed to sync node" >&2; exit 6; }  # assert

  # Test for ignoring expected errors in log files. Run separately to make sure it runs first.
  err_retval=0
  pytest cardano_node_tests/tests/test_node_upgrade.py -k test_ignore_log_errors || err_retval="$?"

  # Hard fork
  pytest cardano_node_tests/tests/test_node_upgrade.py -k test_hardfork || exit 6
  export PROTOCOL_VERSION=11

  # Run smoke tests
  printf "STEP3 tests: %(%H:%M:%S)T\n" -1
  retval=0
  pytest \
    cardano_node_tests \
    -n "$TEST_THREADS" \
    -m "smoke or upgrade_step3" \
    --artifacts-base-dir="$ARTIFACTS_DIR" \
    --cli-coverage-dir="$COVERAGE_DIR" \
    --alluredir="$reports_dir" \
    --html="$WORKDIR/testrun-report-step3.html" \
    --self-contained-html \
    ||retval="$?"

  # create results archive for step3
  ./runner/create_results.sh "$reports_dir" "$WORKDIR" allure-results-step3

  [ "$err_retval" -gt "$retval" ] && retval=1

  printf "STEP3 finish: %(%H:%M:%S)T\n" -1

#
# FINISH - teardown cluster, generate reports
#

elif [ "$1" = "finish" ]; then
  # stop local cluster
  "$cluster_scripts_dir/stop-cluster"

  # generate CLI coverage reports
  ./runner/cli_coverage.sh "$COVERAGE_DIR" "${WORKDIR}/cli_coverage.json"

  retval=0
fi

exit "$retval"
