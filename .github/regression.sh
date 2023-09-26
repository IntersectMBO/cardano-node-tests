#! /usr/bin/env -S nix develop --accept-flake-config .#base -c bash
# shellcheck shell=bash

set -xeuo pipefail

nix --version

DEFAULT_CLUSTER_ERA="babbage"

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

# setup dbsync (disabled by default)
case "${DBSYNC_REV:-""}" in
  "" )
    ;;
  "none" )
    unset DBSYNC_REV
    ;;
  * )
    # shellcheck disable=SC1090,SC1091
    . .github/source_dbsync.sh
    ;;
esac

# setup plutus-apps (enabled by default)
# The "plutus-apps" repo is needed for the `create-script-context` tool, which is used by the
# Plutus tests that are testing script context.
case "${PLUTUS_APPS_REV:="main"}" in
  "none" )
    unset PLUTUS_APPS_REV
    ;;
  * )
    # shellcheck disable=SC1090,SC1091
    . .github/source_plutus_apps.sh
    ;;
esac

# setup cardano-cli (use the built-in version by default)
case "${CARDANO_CLI_REV:-""}" in
  "" )
    ;;
  "none" )
    unset CARDANO_CLI_REV
    ;;
  * )
    # shellcheck disable=SC1090,SC1091
    . .github/source_cardano_cli.sh
    ;;
esac

if [ "${CI_TOPOLOGY:-""}" = "p2p" ]; then
  export ENABLE_P2P=1
elif [ "${CI_TOPOLOGY:-""}" = "mixed" ]; then
  export MIXED_P2P=1
  export NUM_POOLS="${NUM_POOLS:-4}"
fi

export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
rm -rf "${ARTIFACTS_DIR:?}"

export SCHEDULING_LOG=scheduling.log
true > "$SCHEDULING_LOG"

MARKEXPR="${MARKEXPR:-""}"
if [ "${MARKEXPR:-""}" = "all" ]; then
  unset MARKEXPR
fi

if [ -n "${CLUSTERS_COUNT:-""}" ]; then
  export CLUSTERS_COUNT
fi

export CLUSTER_ERA="${CLUSTER_ERA:-"$DEFAULT_CLUSTER_ERA"}"

if [ "${TX_ERA:-""}" == "default" ]; then
  export TX_ERA=""
fi

if [ "${CI_FAST_CLUSTER:-"false"}" != "false" ]; then
  export SCRIPTS_DIRNAME="${SCRIPTS_DIRNAME:-"${CLUSTER_ERA}_fast"}"
else
  export SCRIPTS_DIRNAME="${SCRIPTS_DIRNAME:-"$CLUSTER_ERA"}"
fi

export CARDANO_NODE_SOCKET_PATH_CI="$WORKDIR/state-cluster0/bft1.socket"

# assume we run tests on testnet when `BOOTSTRAP_DIR` is set
if [ -n "${BOOTSTRAP_DIR:-""}" ]; then
  export CARDANO_NODE_SOCKET_PATH_CI="$WORKDIR/state-cluster0/relay1.socket"
  export MAKE_TARGET="${MAKE_TARGET:-"testnets"}"
fi

# function to update cardano-node to specified branch and/or revision, or to the latest available
# shellcheck disable=SC1090,SC1091
. .github/nix_override_cardano_node.sh

echo "::group::Nix env setup"
printf "start: %(%H:%M:%S)T\n" -1

# run tests and generate report
set +e
# shellcheck disable=SC2046,SC2016,SC2119
nix develop --accept-flake-config $(node_override) --command bash -c '
  printf "finish: %(%H:%M:%S)T\n" -1
  echo "::endgroup::"  # end group for "Nix env setup"
  echo "::group::Pytest run"
  export PATH="${PWD}/.bin":"$WORKDIR/cardano-cli/cardano-cli-build/bin":"$PATH"
  export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"
  make "${MAKE_TARGET:-"tests"}"
  retval="$?"
  echo "::endgroup::"
  echo "::group::Collect artifacts"
  ./.github/cli_coverage.sh .
  exit "$retval"
'
retval="$?"

# move reports to root dir
mv .reports/testrun-report.* ./

# grep testing artifacts for errors
# shellcheck disable=SC1090,SC1091
. .github/grep_errors.sh

# Don't stop cluster instances just yet if KEEP_CLUSTERS_RUNNING is set to 1.
# After any key is pressed, resume this script and stop all running cluster instances.
if [ "${KEEP_CLUSTERS_RUNNING:-""}" = 1 ]; then
  sets="$-"
  set +x
  echo
  echo "KEEP_CLUSTERS_RUNNING is set, leaving clusters running until any key is pressed."
  echo "Press any key to continue..."
  read -r
  set -"$sets"
fi

# stop all running cluster instances
stop_instances "$WORKDIR"

# stop postgres if running
stop_postgres || true

# prepare artifacts for upload in Github Actions
if [ -n "${GITHUB_ACTIONS:-""}" ]; then
  # create results archive
  ./.github/results.sh .

  # save testing artifacts
  # shellcheck disable=SC1090,SC1091
  . .github/save_artifacts.sh

  # compress scheduling log
  xz "$SCHEDULING_LOG"

  echo
  echo "Dir content:"
  ls -1a
fi

echo "::endgroup::" # end group for "Collect artifacts"

exit "$retval"
