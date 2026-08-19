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
#   COVERAGE_DIR: directory to save CLI coverage data into (default: run_workdir/cli_coverage)
#   REPORTS_DIR: directory to save Allure test reports into (default: run_workdir/reports)
#   MARKEXPR: pytest mark expression to filter tests, without the `-m` flag
#   PYTEST_ARGS: additional args to pass to pytest
#   CI_ARGS: additional args to pass to pytest (for CI runs)
#   TEST_THREADS: number of pytest workers (defaults vary per target)
#   DESELECT_FROM_FILE: path to file with tests to deselect (one test per line;
#     `#` comments, blank lines and surrounding whitespace are stripped before the
#     file is handed to pytest)
#   CLUSTERS_COUNT: number of local testnet clusters to launch
#   FORBID_RESTART: if set to true, do not restart clusters between tests
#   SESSION_TIMEOUT: timeout for the test session (e.g. 3h for 3 hours)
#
# Notes:
# - If PYTEST_ARGS is provided, we disable cleanup and the initial "skip all" pass.
# - If any tests are deselected, we disable the initial "skip all" pass.
# - HTML/JUnit reports are generated only when PYTEST_ARGS is unset.

set -Eeuo pipefail

_top_dir="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine top dir, exiting." >&2; exit 1; }
# shellcheck disable=SC1091
. "$_top_dir/scripts/common.sh"

# Defaults
TESTS_DIR="${TESTS_DIR:-cardano_node_tests/}"
COVERAGE_DIR="${COVERAGE_DIR:-run_workdir/cli_coverage}"
REPORTS_DIR="${REPORTS_DIR:-run_workdir/reports}"

# Helpers
usage() {
  cat <<EOF
Usage: "$0" [tests|testpr|testnets]

Targets:
  tests     Run all tests (default TEST_THREADS=20), DbSyncAbortOnPanic=1
  testpr    Run PR-level tests (default CLUSTERS_COUNT=5, TEST_THREADS=20, MARKEXPR="smoke")
  testnets  Run tests that can run on public testnets (CLUSTERS_COUNT=1, FORBID_RESTART=true,
            default TEST_THREADS=6, MARKEXPR="testnets")

All targets respect the env vars documented at the top of this script.
EOF
}

run_pytest() {
  if [ -n "${SESSION_TIMEOUT:-}" ]; then
    local -a timeout_arr=( "--foreground" "--signal=INT" "--kill-after=0" "$SESSION_TIMEOUT" )
    echo "Running: PYTEST_ADDOPTS='${PYTEST_ADDOPTS:-}' timeout ${timeout_arr[*]} pytest $*"
    timeout "${timeout_arr[@]}" pytest "$@"
  else
    echo "Running: PYTEST_ADDOPTS='${PYTEST_ADDOPTS:-}' pytest $*"
    pytest "$@"
  fi
}

ensure_dirs() {
  mkdir -p "$COVERAGE_DIR" "$REPORTS_DIR"
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

  if [[ -n "${CI_ARGS:-}" ]]; then
    export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+$PYTEST_ADDOPTS }${CI_ARGS}"
  fi

  # RUN_SKIPS can still be turned off by `compute_common_args`, so it is not
  # made readonly here.
  readonly CLEANUP
}

# Compute args that depend on current environment.
compute_common_args() {
  # MARKEXPR handling
  if [[ -n "${MARKEXPR:-}" ]]; then
    markexpr_arr=( -m "$MARKEXPR" )
  else
    markexpr_arr=()
  fi

  # Test run report args only when PYTEST_ARGS is unset.
  if [[ -z "${PYTEST_ARGS:-}" ]]; then
    testrun_report_arr=(
      "--html=$REPORTS_DIR/testrun-report.html"
      "--self-contained-html"
      "--junitxml=$REPORTS_DIR/testrun-report.xml"
    )
  else
    testrun_report_arr=()
  fi

  # Deselect-from-file
  deselect_from_file_arr=()
  if [[ -n "${DESELECT_FROM_FILE:-}" ]]; then
    # Only a typo can get us here - every caller that wants no deselection sets
    # `DESELECT_FROM_FILE` to an empty value, which skips this whole block.
    # Exit code 3 distinguishes this setup failure from pytest's "some tests
    # failed" (1).
    assert_deselect_file || exit 3

    # `pytest-select` has no comment syntax - it reads every line of the file
    # as a test name. Hand it a stripped copy so that annotated lists (e.g.
    # `scripts/deselected_leios_tests.txt`, which groups the tests by cause)
    # don't end up in its "Not all deselected tests exist" warning.
    stripped_deselect="$(mktemp -t deselected_tests.XXXXXX.txt)"
    trap 'rm -f "$stripped_deselect"' EXIT
    sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^#/d' -e '/^$/d' \
      "$DESELECT_FROM_FILE" > "$stripped_deselect"

    if [[ -s "$stripped_deselect" ]]; then
      # `grep -c ''` and not `wc -l`, so that a file without a trailing newline
      # is not undercounted by one.
      echo "Deselecting $(grep -c '' "$stripped_deselect") tests listed in '$DESELECT_FROM_FILE'"
      deselect_from_file_arr=( "--deselect-from-file=$stripped_deselect" )
      # The initial "skip all" pass would register the deselected tests with
      # Allure as skipped, which is not what the deselection is for.
      RUN_SKIPS="no"
    else
      # An empty list is not an error - e.g. the tcache "already passed" list
      # is empty on the first run of a testrun.
      echo "No tests to deselect in '$DESELECT_FROM_FILE'"
    fi
  fi

  readonly RUN_SKIPS
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
    pytest -s "$TESTS_DIR" "${markexpr_arr[@]}" --skipall --alluredir="$REPORTS_DIR" >/dev/null
  fi
}

run_real_tests() {
  run_pytest \
    "$TESTS_DIR" \
    "${markexpr_arr[@]}" \
    "${deselect_from_file_arr[@]}" \
    -n "${TEST_THREADS}" \
    --cli-coverage-dir="$COVERAGE_DIR" \
    --alluredir="$REPORTS_DIR" \
    "${testrun_report_arr[@]}" \
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
  export FORBID_RESTART=true
  export CLUSTERS_COUNT=1
  TEST_THREADS="${TEST_THREADS:-6}"
  SESSION_TIMEOUT="${SESSION_TIMEOUT:-24h}"
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
    *) echo "Unknown target: $cmd" >&2; usage >&2; exit 2;;
  esac
}

main "$@"
