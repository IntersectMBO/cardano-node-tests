#!/usr/bin/env bash

# Run test suites.
#
# Targets: tests | testpr | testnets
# Usage:
#   ./run_tests.sh tests
#   ./run_tests.sh testpr
#   ./run_tests.sh testnets
#
# Env vars:
#   TESTS_DIR: directory with tests to run (default: cardano_node_tests/)
#   ARTIFACTS_DIR: directory to save artifacts into (default: .artifacts)
#   COVERAGE_DIR: directory to save CLI coverage data into (default: .cli_coverage)
#   REPORTS_DIR: directory to save Allure test reports into (default: .reports)
#   MARKEXPR: pytest mark expression to filter tests, without the `-m` flag
#   NO_ARTIFACTS: if set, do not save artifacts
#   PYTEST_ARGS: additional args to pass to pytest
#   CI_ARGS: additional args to pass to pytest (for CI runs)
#   TEST_THREADS: number of pytest workers (defaults vary per target)
#   DESELECT_FROM_FILE: path to file with tests to deselect
#   CLUSTERS_COUNT: number of local testnet clusters to launch
#   FORBID_RESTART: if set to 1, do not restart clusters between tests
#   SESSION_TIMEOUT: timeout for the test session (e.g. 3h for 3 hours)
#
# Notes:
# - If PYTEST_ARGS is provided, we disable cleanup and the initial "skip all" pass.
# - If DESELECT_FROM_FILE is provided, we disable the initial "skip all" pass.
# - If NO_ARTIFACTS is unset, we save artifacts under ARTIFACTS_DIR.
# - HTML/JUnit reports are generated only when PYTEST_ARGS is unset.

set -Eeuo pipefail

# Defaults
TESTS_DIR="${TESTS_DIR:-cardano_node_tests/}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-.artifacts}"
COVERAGE_DIR="${COVERAGE_DIR:-.cli_coverage}"
REPORTS_DIR="${REPORTS_DIR:-.reports}"

# Helpers
usage() {
  cat <<EOF
Usage: "$0" [tests|testpr|testnets]

Targets:
  tests     Run all tests (default TEST_THREADS=20), DbSyncAbortOnPanic=1
  testpr    Run PR-level tests (default CLUSTERS_COUNT=5, TEST_THREADS=20, MARKEXPR="smoke")
  testnets  Run tests that can run on public testnets (CLUSTERS_COUNT=1, FORBID_RESTART=1,
            default TEST_THREADS=15, MARKEXPR="testnets")

All targets respect the same env vars as the original Makefile.
EOF
}

run_pytest() {
  if [ -n "${SESSION_TIMEOUT:-}" ]; then
    local -a timeout_arr=( "--signal=INT" "--kill-after=0" "$SESSION_TIMEOUT" )
    echo "Running: PYTEST_ADDOPTS='${PYTEST_ADDOPTS:-}' timeout ${timeout_arr[*]} pytest $*"
    timeout "${timeout_arr[@]}" pytest "$@"
  else
    echo "Running: PYTEST_ADDOPTS='${PYTEST_ADDOPTS:-}' pytest $*"
    pytest "$@"
  fi
}

ensure_dirs() {
  mkdir -p "$ARTIFACTS_DIR" "$COVERAGE_DIR" "$REPORTS_DIR"
}

# Set common env vars that affect test runs.
set_common_env() {
  # Cleanup / skip logic
  CLEANUP="yes"
  RUN_SKIPS="yes"

  if [[ -n "${PYTEST_ARGS:-}" ]]; then
    CLEANUP="no"
    RUN_SKIPS="no"
    export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+$PYTEST_ADDOPTS }${PYTEST_ARGS}"
  fi

  if [[ -n "${DESELECT_FROM_FILE:-}" ]]; then
    RUN_SKIPS="no"
  fi

  if [[ -n "${CI_ARGS:-}" ]]; then
    export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+$PYTEST_ADDOPTS }${CI_ARGS}"
  fi
}

# Compute args that depend on current environment.
compute_common_args() {
  # MARKEXPR handling
  if [[ -n "${MARKEXPR:-}" ]]; then
    MARKEXPR_ARR=( -m "$MARKEXPR" )
  else
    MARKEXPR_ARR=()
  fi

  # It may not be always necessary to save artifacts, e.g. when running tests on local cluster
  # on local machine.
  if [[ -n "${NO_ARTIFACTS+x}" ]]; then
    ARTIFACTS_ARR=()
  else
    ARTIFACTS_ARR=( "--artifacts-base-dir=$ARTIFACTS_DIR" )
  fi

  # Test run report args only when PYTEST_ARGS is unset.
  if [[ -z "${PYTEST_ARGS:-}" ]]; then
    TESTRUN_REPORT_ARR=(
      "--html=$REPORTS_DIR/testrun-report.html"
      "--self-contained-html"
      "--junitxml=$REPORTS_DIR/testrun-report.xml"
    )
  else
    TESTRUN_REPORT_ARR=()
  fi

  # Deselect-from-file
  if [[ -n "${DESELECT_FROM_FILE:-}" ]]; then
    DESELECT_FROM_FILE_ARR=( "--deselect-from-file=$DESELECT_FROM_FILE" )
  else
    DESELECT_FROM_FILE_ARR=()
  fi
}

cleanup_previous_run() {
  if [[ "$CLEANUP" == "yes" ]]; then
    # Remove previous reports and coverage artifacts.
    rm -f "$REPORTS_DIR"/{*-attachment.txt,*-result.json,*-container.json,testrun-report.*} || true
    rm -f "$COVERAGE_DIR"/cli_coverage_* || true
  fi
}

initial_skip_pass() {
  if [[ "$RUN_SKIPS" == "yes" ]]; then
    echo "Initial pass: skipping all tests to register them with Allure"
    pytest -s "$TESTS_DIR" "${MARKEXPR_ARR[@]}" --skipall --alluredir="$REPORTS_DIR" >/dev/null
  fi
}

run_real_tests() {
  run_pytest \
    "$TESTS_DIR" \
    "${MARKEXPR_ARR[@]}" \
    "${DESELECT_FROM_FILE_ARR[@]}" \
    -n "${TEST_THREADS}" \
    "${ARTIFACTS_ARR[@]}" \
    --cli-coverage-dir="$COVERAGE_DIR" \
    --alluredir="$REPORTS_DIR" \
    "${TESTRUN_REPORT_ARR[@]}" \
    "$@"
}

# Provide default MARKEXPR if none was given from env/CLI.
ensure_markexpr_default() {
  local default_expr="$1"
  if [[ -z "${MARKEXPR:-}" ]]; then
    MARKEXPR="$default_expr"
  fi
}

# Targets
target_tests() {
  export DbSyncAbortOnPanic="${DbSyncAbortOnPanic:-1}"
  TEST_THREADS="${TEST_THREADS:-20}"
  SESSION_TIMEOUT="${SESSION_TIMEOUT:-3h}"

  ensure_dirs
  set_common_env
  compute_common_args
  cleanup_previous_run
  initial_skip_pass
  run_real_tests "$@"
}

target_testpr() {
  export TESTPR=1
  export CLUSTERS_COUNT="${CLUSTERS_COUNT:-5}"
  TEST_THREADS="${TEST_THREADS:-20}"
  SESSION_TIMEOUT="${SESSION_TIMEOUT:-45m}"
  ensure_markexpr_default "smoke"

  ensure_dirs
  set_common_env
  compute_common_args
  cleanup_previous_run
  initial_skip_pass
  run_real_tests "$@"
}

target_testnets() {
  export CLUSTERS_COUNT=1
  export FORBID_RESTART=1
  TEST_THREADS="${TEST_THREADS:-15}"
  SESSION_TIMEOUT="${SESSION_TIMEOUT:-20h}"
  ensure_markexpr_default "testnets"

  ensure_dirs
  set_common_env
  compute_common_args
  cleanup_previous_run
  initial_skip_pass
  run_real_tests "$@"
}

# Dispatch
main() {
  command -v pytest >/dev/null 2>&1 || {
  echo "Error: pytest not found in PATH." >&2
  exit 127
}

  local cmd="${1:-tests}"
  case "$cmd" in
    tests)    shift; target_tests    "$@";;
    testpr)   shift; target_testpr   "$@";;
    testnets) shift; target_testnets "$@";;
    -h|--help) usage;;
    *) echo "Unknown target: $cmd" >&2; usage; exit 2;;
  esac
}

main "$@"
