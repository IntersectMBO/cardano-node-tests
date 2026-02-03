#! /usr/bin/env -S nix develop --accept-flake-config .#base -c bash
# shellcheck shell=bash disable=SC2317

set -Eeuo pipefail

# shellcheck disable=SC2016
_err_string='echo "Error at line $LINENO"'
# shellcheck disable=SC2064
trap "$_err_string" ERR

nix --version
df -h .

retval=0

REPODIR="$(readlink -m "${0%/*}/..")"
cd "$REPODIR"

export WORKDIR="$REPODIR/run_workdir"
export PATH_PREPEND="${PWD}/.bin"

# shellcheck disable=SC1091
. scripts/common.sh

# shellcheck disable=SC1091
. .github/stop_cluster_instances.sh

# stop all running cluster instances
stop_instances "$WORKDIR"

# create clean workdir
rm -rf "${WORKDIR:?}"
mkdir -p "$WORKDIR"

export TMPDIR="$WORKDIR/tmp"
mkdir -p "$TMPDIR"

export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
rm -rf "${ARTIFACTS_DIR:?}"

export SCHEDULING_LOG=scheduling.log
: > "$SCHEDULING_LOG"

MARKEXPR="${MARKEXPR:-}"
if [ "$MARKEXPR" = "all" ]; then
  unset MARKEXPR
elif [ "$MARKEXPR" = "conway only" ]; then
  unset MARKEXPR
  export TESTS_DIR="cardano_node_tests/tests/tests_conway"
elif [ "$MARKEXPR" = "dbsync config" ]; then
  export CLUSTERS_COUNT=1
  export MARKEXPR="(dbsync and smoke) or dbsync_config"
fi

if [ -n "${CLUSTERS_COUNT:-}" ]; then
  export CLUSTERS_COUNT
fi

if [ "${CLUSTER_ERA:-}" = "conway 10" ]; then
  export CLUSTER_ERA="conway"
elif [ "${CLUSTER_ERA:-}" = "conway 11" ]; then
  export CLUSTER_ERA="conway"
  export PROTOCOL_VERSION=11
fi

# Decrease the number of tests per cluster if we are using the "disk" (LMDB) UTxO backend to avoid
# having too many concurrent readers.
if [ -z "${MAX_TESTS_PER_CLUSTER:-}" ] && [[ "${UTXO_BACKEND:-}" = "disk"* ]]; then
  export MAX_TESTS_PER_CLUSTER=5
fi

if [ -n "${TESTNET_VARIANT:-}" ]; then
  export TESTNET_VARIANT
elif is_truthy "${CI_BYRON_CLUSTER:-}"; then
  export TESTNET_VARIANT="${CLUSTER_ERA:-conway}_slow"
fi

export CARDANO_NODE_SOCKET_PATH_CI="$WORKDIR/state-cluster0/bft1.socket"

# assume we run tests on testnet when `BOOTSTRAP_DIR` is set
if [ -n "${BOOTSTRAP_DIR:-}" ]; then
  export CARDANO_NODE_SOCKET_PATH_CI="$WORKDIR/state-cluster0/relay1.socket"
  export RUN_TARGET="${RUN_TARGET:-"testnets"}"
fi

echo "### Dependencies setup ###"

# setup dbsync (disabled by default)
case "${DBSYNC_REV:-}" in
  "" )
    ;;
  "none" )
    unset DBSYNC_REV
    ;;
  * )
    # shellcheck disable=SC1091
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
    # shellcheck disable=SC1091
    . .github/source_plutus_apps.sh
    ;;
esac

# setup cardano-cli (use the built-in version by default)
case "${CARDANO_CLI_REV:-}" in
  "" )
    ;;
  "none" )
    unset CARDANO_CLI_REV
    ;;
  * )
    # shellcheck disable=SC1091
    . .github/source_cardano_cli.sh
    cardano_cli_build "$CARDANO_CLI_REV"
    PATH_PREPEND="$(cardano_cli_print_path_prepend "")${PATH_PREPEND}"
    export PATH_PREPEND
    ;;
esac

# setup cardano-node binaries
case "${NODE_REV:-}" in
  "" | "none" )
    NODE_REV=master
    ;;
esac
# shellcheck disable=SC1091
. .github/source_cardano_node.sh
cardano_bins_build_all "$NODE_REV" "${CARDANO_CLI_REV:-}"
PATH_PREPEND="$(cardano_bins_print_path_prepend "${CARDANO_CLI_REV:-}")${PATH_PREPEND}"
export PATH_PREPEND

# optimize nix store if running in GitHub Actions
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  nix store gc || :
fi

echo "### Cleanup setup ###"

_cleanup() {
  # stop all running cluster instances
  stop_instances "$WORKDIR"

  # stop postgres if running
  if command -v stop_postgres >/dev/null 2>&1; then
    stop_postgres || :
  fi
}

# shellcheck disable=SC2329
_cleanup_testnet_on_interrupt() {
  [ -z "${BOOTSTRAP_DIR:-}" ] && return

  _PYTEST_CURRENT="$(find "$WORKDIR" -type l -name pytest-current)"
  [ -z "$_PYTEST_CURRENT" ] && return
  _PYTEST_CURRENT="$(readlink -m "$_PYTEST_CURRENT")"
  export _PYTEST_CURRENT

  echo "::endgroup::" # end group for the group that was interrupted

  echo "::group::Testnet cleanup"
  printf "start: %(%H:%M:%S)T\n" -1

  # shellcheck disable=SC2016
  nix develop --accept-flake-config .#testenv --command bash -c '
    . .github/setup_venv.sh
    export PATH="$PATH_PREPEND":"$PATH"
    export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"
    cleanup_dir="${_PYTEST_CURRENT}/../cleanup-${_PYTEST_CURRENT##*/}-script"
    mkdir "$cleanup_dir"
    cd "$cleanup_dir"
    testnet-cleanup -a "$_PYTEST_CURRENT"
  '

  echo "::endgroup::"
}

# shellcheck disable=SC2329
_last_cleanup() {
  # Redefine the ERR trap to avoid interfering with cleanup
  # shellcheck disable=SC2064
  trap "$_err_string" ERR
  # Ignore further interrupts during cleanup
  trap '' SIGINT

  # Do testnet cleanup only on interrupted testrun. When not interrupted,
  # cleanup is done as part of a testrun.
  _cleanup_testnet_on_interrupt || :
  _cleanup

  trap - SIGINT
}
# last cleanup on Ctrl+C or error
trap '_last_cleanup; exit 130' SIGINT
# shellcheck disable=SC2064
trap "${_err_string}; _last_cleanup" ERR

echo "::endgroup::"  # end group for "Script setup"

echo "::group::Nix env setup"
printf "start: %(%H:%M:%S)T\n" -1

if [ "$(echo "$PWD"/.bin/*)" != "${PWD}/.bin/*" ]; then
  echo
  echo "WARNING: using following binaries from ${PWD}/.bin:"
  ls -1 "${PWD}/.bin"
  echo
fi

# function to monitor system resources and log them every 10 minutes
monitor_system() {
  : > monitor.log

  while true; do
    {
      echo "===== $(date) ====="
      echo "--- CPU ---"
      top -b -n1 | head -5
      echo "--- MEM ---"
      free -h
      echo "--- DISK ---"
      df -h .
      echo
    } >> monitor.log

    sleep 600 # 10 minutes
  done
}

# start monitor in background
monitor_system &
MON_PID=$!

# ensure cleanup on ANY exit (success, error, Ctrl-C, set -e, etc.)
# shellcheck disable=SC2064
trap "echo 'Stopping monitor'; kill ${MON_PID:-} 2>/dev/null || true" EXIT

# Run tests and generate report

# shellcheck disable=SC2016
nix develop --accept-flake-config .#testenv --command bash -c '
  set -euo pipefail
  echo "::endgroup::"  # end group for "Nix env setup"

  echo "::group::Python venv setup"
  printf "start: %(%H:%M:%S)T\n" -1
  . .github/setup_venv.sh clean
  echo "::endgroup::"  # end group for "Python venv setup"

  echo "::group::🧪 Testrun"
  printf "start: %(%H:%M:%S)T\n" -1
  df -h .
  export PATH="$PATH_PREPEND":"$PATH"
  export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"
  retval=0
  ./.github/run_tests.sh "${RUN_TARGET:-"tests"}" || retval="$?"
  df -h .
  echo "::endgroup::"  # end group for "Testrun"

  echo "::group::Collect artifacts & teardown cluster"
  printf "start: %(%H:%M:%S)T\n" -1
  ./.github/cli_coverage.sh || :
  ./.github/reqs_coverage.sh || :
  exit "$retval"
' || retval="$?"

# grep testing artifacts for errors
./.github/grep_errors.sh

# Don't stop cluster instances just yet if KEEP_CLUSTERS_RUNNING is set to 1.
# After any key is pressed, resume this script and stop all running cluster instances.
if [ "${KEEP_CLUSTERS_RUNNING:-}" = 1 ]; then
  echo
  echo "KEEP_CLUSTERS_RUNNING is set, leaving clusters running until any key is pressed."
  echo "Press any key to continue..."
  read -r
fi

_last_cleanup

# prepare artifacts for upload in GitHub Actions
if [ -n "${GITHUB_ACTIONS:-}" ]; then

  # move reports to root dir
  if [ -e .reports/testrun-report.html ]; then
    mv .reports/testrun-report.* ./
  fi

  # create results archive
  ./.github/create_results.sh || :

  # save testing artifacts
  ./.github/save_artifacts.sh || :
fi

exit "$retval"
