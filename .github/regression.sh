#! /usr/bin/env -S nix develop --accept-flake-config .#base -c bash
# shellcheck shell=bash disable=SC2317

set -euo pipefail

nix --version
df -h .

DEFAULT_CLUSTER_ERA="conway"

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

if [ "${CI_TOPOLOGY:-""}" = "legacy" ]; then
  export ENABLE_LEGACY=1
elif [ "${CI_TOPOLOGY:-""}" = "mixed" ]; then
  export MIXED_P2P=1
  export NUM_POOLS="${NUM_POOLS:-4}"
fi

export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
rm -rf "${ARTIFACTS_DIR:?}"

export SCHEDULING_LOG=scheduling.log
: > "$SCHEDULING_LOG"

MARKEXPR="${MARKEXPR:-""}"
if [ "$MARKEXPR" = "all" ]; then
  unset MARKEXPR
elif [ "$MARKEXPR" = "conway only" ]; then
  unset MARKEXPR
  export TESTS_DIR="cardano_node_tests/tests/tests_conway"
fi

if [ -n "${CLUSTERS_COUNT:-""}" ]; then
  export CLUSTERS_COUNT
fi

CLUSTER_ERA="${CLUSTER_ERA:-"$DEFAULT_CLUSTER_ERA"}"
if [ "$CLUSTER_ERA" = "conway 9" ]; then
  unset PV10
  CLUSTER_ERA="conway"
elif [ "$CLUSTER_ERA" = "conway 10" ]; then
  export PV10=1
  CLUSTER_ERA="conway"
fi
export CLUSTER_ERA

TX_ERA="${TX_ERA:-""}"
if [ "$TX_ERA" = "conway" ] || [ "$CLUSTER_ERA" = "conway" ]; then
  unset TX_ERA
  export COMMAND_ERA="conway"
elif [ "$TX_ERA" = "default" ]; then
  export TX_ERA=""
fi

if [ -n "${BOOTSTRAP_DIR:-""}" ]; then
  :  # don't touch `SCRIPTS_DIRNAME` when running on testnet
elif [ "${CI_BYRON_CLUSTER:-"false"}" != "false" ]; then
  export SCRIPTS_DIRNAME="${SCRIPTS_DIRNAME:-"$CLUSTER_ERA"}"
else
  export SCRIPTS_DIRNAME="${SCRIPTS_DIRNAME:-"${CLUSTER_ERA}_fast"}"
fi

export CARDANO_NODE_SOCKET_PATH_CI="$WORKDIR/state-cluster0/bft1.socket"

# assume we run tests on testnet when `BOOTSTRAP_DIR` is set
if [ -n "${BOOTSTRAP_DIR:-""}" ]; then
  export CARDANO_NODE_SOCKET_PATH_CI="$WORKDIR/state-cluster0/relay1.socket"
  export MAKE_TARGET="${MAKE_TARGET:-"testnets"}"
fi

echo "### Dependencies setup ###"

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

# Setup plutus-apps (disabled by default).
# The "plutus-apps" repo is needed for the `create-script-context` tool, which is used by the
# Plutus tests that are testing script context.
# TODO: The `create-script-context` tool is broken for a very long time, hence disabled.
# See https://github.com/IntersectMBO/plutus-apps/issues/1107
case "${PLUTUS_APPS_REV:="none"}" in
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

echo "### Cleanup setup ###"

_cleanup() {
  # stop all running cluster instances
  stop_instances "$WORKDIR"

  # stop postgres if running
  if command -v stop_postgres >/dev/null 2>&1; then
    stop_postgres || :
  fi
}

_cleanup_testnet_on_interrupt() {
  [ -z "${BOOTSTRAP_DIR:-""}" ] && return

  _PYTEST_CURRENT="$(find "$WORKDIR" -type l -name pytest-current)"
  [ -z "$_PYTEST_CURRENT" ] && return
  _PYTEST_CURRENT="$(readlink -m "$_PYTEST_CURRENT")"
  export _PYTEST_CURRENT

  echo "::endgroup::" # end group for the group that was interrupted

  echo "::group::Testnet cleanup"
  printf "start: %(%H:%M:%S)T\n" -1

  # shellcheck disable=SC2016
  nix develop --accept-flake-config .#venv --command bash -c '
    . .github/setup_venv.sh
    export PATH="${PWD}/.bin":"$WORKDIR/cardano-cli/cardano-cli-build/bin":"$PATH"
    export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"
    cleanup_dir="${_PYTEST_CURRENT}/../cleanup-${_PYTEST_CURRENT##*/}-script"
    mkdir "$cleanup_dir"
    cd "$cleanup_dir"
    testnet-cleanup -a "$_PYTEST_CURRENT"
  '

  echo "::endgroup::"
}

# cleanup on Ctrl+C
_interrupted() {
  # Do testnet cleanup only on interrupted testrun. When not interrupted,
  # cleanup is done as part of a testrun.
  _cleanup_testnet_on_interrupt
  _cleanup
}
trap 'set +e; _interrupted; exit 130' SIGINT

echo "::endgroup::"  # end group for "Script setup"

echo "::group::Nix env setup"
printf "start: %(%H:%M:%S)T\n" -1

# function to update cardano-node to specified branch and/or revision, or to the latest available
# shellcheck disable=SC1090,SC1091
. .github/nix_override_cardano_node.sh

# run tests and generate report
set +e
# shellcheck disable=SC2046,SC2119
nix flake update --accept-flake-config $(node_override)
# shellcheck disable=SC2016
nix develop --accept-flake-config .#venv --command bash -c '
  echo "::endgroup::"  # end group for "Nix env setup"

  echo "::group::Python venv setup"
  printf "start: %(%H:%M:%S)T\n" -1
  . .github/setup_venv.sh clean
  echo "::endgroup::"  # end group for "Python venv setup"

  echo "::group::🧪 Testrun"
  printf "start: %(%H:%M:%S)T\n" -1
  df -h .
  export PATH="${PWD}/.bin":"$WORKDIR/cardano-cli/cardano-cli-build/bin":"$PATH"
  export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"
  make "${MAKE_TARGET:-"tests"}"
  retval="$?"
  df -h .
  echo "::endgroup::"  # end group for "Testrun"

  echo "::group::Collect artifacts & teardown cluster"
  printf "start: %(%H:%M:%S)T\n" -1
  ./.github/cli_coverage.sh
  ./.github/reqs_coverage.sh
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
  echo
  echo "KEEP_CLUSTERS_RUNNING is set, leaving clusters running until any key is pressed."
  echo "Press any key to continue..."
  read -r
fi

_cleanup

# prepare artifacts for upload in Github Actions
if [ -n "${GITHUB_ACTIONS:-""}" ]; then
  # create results archive
  ./.github/results.sh

  # save testing artifacts
  # shellcheck disable=SC1090,SC1091
  . .github/save_artifacts.sh

  # compress scheduling log
  xz "$SCHEDULING_LOG"

  echo
  echo "Dir content:"
  ls -1a
fi

exit "$retval"
