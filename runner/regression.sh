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

REPODIR="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine repo dir, exiting." >&2; exit 1; }
cd "$REPODIR"

export WORKDIR="$REPODIR/run_workdir"
export PATH_PREPEND="${PWD}/.bin"

# shellcheck disable=SC1091
. scripts/common.sh

if is_venv_active; then
  echo "This script should be run outside of any virtual environment." >&2
  exit 1
fi

# Refuse to start if another testrun is already using this workdir.
# `LOCK_FD` is set so background processes (e.g. monitor_system) can drop the
# inherited lock FD and not keep the lock alive if they outlive this script.
acquire_workdir_lock "$WORKDIR" LOCK_FD || exit 1

# shellcheck disable=SC1091
. runner/stop_cluster_instances.sh

# stop all running cluster instances
stop_instances "$WORKDIR"

# create clean workdir
rm -rf "${WORKDIR:?}"
mkdir -p "$WORKDIR"

export PYTHONPYCACHEPREFIX="${WORKDIR}/__pycache__"

export TMPDIR="$WORKDIR/tmp"
mkdir -p "$TMPDIR"

export ARTIFACTS_DIR="${WORKDIR}/artifacts"
export COVERAGE_DIR="${WORKDIR}/cli_coverage"
export REPORTS_DIR="${WORKDIR}/reports"

export SCHEDULING_LOG="${WORKDIR}/scheduling.log"
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

CLUSTER_ERA="conway"
if [ "${CI_CLUSTER_ERA:-}" = "conway 11" ]; then
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
  export TESTNET_VARIANT="${CLUSTER_ERA:?}_slow"
fi

if [ "${CI_CONSENSUS_MODE:-}" = "Genesis" ]; then
  export USE_GENESIS_MODE=true
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
    . runner/source_dbsync.sh
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
    . runner/source_cardano_cli.sh
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
. runner/source_cardano_node.sh
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
  _PYTEST_CURRENT="$(readlink -f "$_PYTEST_CURRENT")"
  export _PYTEST_CURRENT

  echo "::endgroup::" # end group for the group that was interrupted

  echo "::group::Testnet cleanup"
  printf "start: %(%H:%M:%S)T\n" -1

  # shellcheck disable=SC2016
  nix develop --accept-flake-config .#testenv --command bash -c '
    . runner/setup_venv.sh
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
_int_cleanup() {
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
# cleanup on Ctrl+C
trap '_int_cleanup; exit 130' SIGINT

# shellcheck disable=SC2329
_last_cleanup() {
  # Redefine the ERR trap to avoid interfering with cleanup
  # shellcheck disable=SC2064
  trap "$_err_string" ERR
  # Ignore further interrupts during cleanup
  trap '' SIGINT

  _cleanup

  trap - SIGINT
}
# last cleanup on error
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

# Function to monitor system resources and log them every 10 minutes
monitor_system() {
  # Drop the inherited workdir lock FD so this background subshell does not
  # keep the lock held if it outlives the parent script.
  if [ -n "${LOCK_FD:-}" ]; then
    exec {LOCK_FD}>&-
  fi

  monitor_log="${WORKDIR}/monitor.log"
  : > "$monitor_log"

  set +e  # don't exit on error in this monitoring loop, just log the error and keep going
  trap - ERR EXIT SIGINT # reset traps in this subshell so they don't interfere with the main script's traps

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
    } >> "$monitor_log" 2>&1

    sleep 600 # 10 minutes
  done
}

# start monitor in background
monitor_system &
MON_PID=$!

# Kill the sleep child first via pkill -P; otherwise the bash subshell stays blocked
# in the foreground sleep and SIGTERM is only handled after sleep returns.
# shellcheck disable=SC2329
kill_monitor() {
  if [ -n "${MON_PID:-}" ]; then
    echo "Stopping monitor with PID $MON_PID"
    pkill -P "$MON_PID" 2>/dev/null || true
    kill_and_wait "$MON_PID" || true
    unset MON_PID
  fi
}

# ensure cleanup on ANY exit (success, error, Ctrl-C, set -e, etc.).
trap 'kill_monitor' EXIT

# Run tests and generate report

# shellcheck disable=SC2016
nix develop --accept-flake-config .#testenv --command bash -c '
  set -euo pipefail
  echo "::endgroup::"  # end group for "Nix env setup"

  echo "::group::Python venv setup"
  printf "start: %(%H:%M:%S)T\n" -1
  . runner/setup_venv.sh clean
  echo "::endgroup::"  # end group for "Python venv setup"

  echo "::group::🧪 Testrun"
  printf "start: %(%H:%M:%S)T\n" -1
  df -h .
  export PATH="$PATH_PREPEND":"$PATH"
  export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"
  retval=0
  ./runner/run_tests.sh "${RUN_TARGET:-"tests"}" || retval="$?"
  df -h .
  echo "::endgroup::"  # end group for "Testrun"

  echo "::group::Collect artifacts & teardown cluster"
  printf "start: %(%H:%M:%S)T\n" -1
  ./runner/cli_coverage.sh "$COVERAGE_DIR" "${WORKDIR}/cli_coverage.json" || :
  ./runner/reqs_coverage.sh "$ARTIFACTS_DIR" "${WORKDIR}/requirements_coverage.json" || :
  exit "$retval"
' || retval="$?"

# grep testing artifacts for errors
./runner/grep_errors.sh "$ARTIFACTS_DIR" "${WORKDIR}/errors_all.log"

# Don't stop cluster instances just yet if KEEP_CLUSTERS_RUNNING is set to 1.
# After any key is pressed, resume this script and stop all running cluster instances.
if is_truthy "${KEEP_CLUSTERS_RUNNING:-}"; then
  echo
  echo "KEEP_CLUSTERS_RUNNING is set, leaving clusters running until any key is pressed."
  echo "Press any key to continue..."
  read -r
fi

_last_cleanup

# Prepare artifacts

# Move reports to workdir
if [ -e "${REPORTS_DIR}/testrun-report.html" ]; then
  mv "${REPORTS_DIR}"/testrun-report.* "$WORKDIR/"
fi

# Create results archive
./runner/create_results.sh "$REPORTS_DIR" "$WORKDIR" || :

# Save testing artifacts
./runner/save_artifacts.sh "$ARTIFACTS_DIR" "$WORKDIR" || :

exit "$retval"
