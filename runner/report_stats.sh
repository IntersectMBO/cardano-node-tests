#!/usr/bin/env bash

# Upload testrun statistics to the tcache. Best effort: call it with `|| :`
# so a failure cannot change the caller's exit code.

set -uo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 4 ]; then
  echo "Usage: $0 <allure_results_dir> <exit_code> [cli_coverage_json] [step]" >&2
  exit 1
fi

results_dir="$1"
exit_code="$2"
coverage_json="${3:-}"
step="${4:-main}"

tests_repo="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine test repo dir, exiting." >&2; exit 1; }

# shellcheck disable=SC1091
. "${tests_repo}/scripts/common.sh"

if ! is_truthy "${ENABLE_TCACHE_STATS:-}"; then
  echo "ENABLE_TCACHE_STATS is not enabled, not reporting stats."
  exit 0
fi

if [ -z "${TCACHE_BASIC_AUTH:-}" ] || [ -z "${TCACHE_URL:-}" ]; then
  echo "TCACHE_BASIC_AUTH or TCACHE_URL is not set, not reporting stats."
  exit 0
fi

if [ ! -d "$results_dir" ]; then
  echo "No Allure results in '$results_dir', not reporting stats." >&2
  exit 1
fi

# regression.sh adds PATH_PREPEND to PATH only inside its nix shell, so add it
# again here for the `--version` calls.
if [ -n "${PATH_PREPEND:-}" ]; then
  PATH="${PATH_PREPEND}:${PATH}"
  export PATH
fi

# `TCACHE_URL` ends in `/results`.
root="${TCACHE_URL%/}"
stats_url="${root%/results}/stats"

stats_json="$(mktemp)" || exit 1
# shellcheck disable=SC2064
trap "rm -f '$stats_json'" EXIT

extra_args=()
if [ -n "$coverage_json" ] && [ -e "$coverage_json" ]; then
  extra_args+=(--cli-coverage "$coverage_json")
fi
if [ -n "${MARKEXPR:-}" ] || [ -n "${DESELECT_FROM_FILE:-}" ]; then
  extra_args+=(--filtered)
fi

if ! "${tests_repo}/scripts/stats_json.py" "$results_dir" \
       --exit-code "$exit_code" --step "$step" "${extra_args[@]}" \
       --output "$stats_json"; then
  echo "Could not build the stats document, not reporting stats." >&2
  exit 1
fi

echo "Reporting testrun stats to $stats_url"
# Time out so a tcache that never answers cannot hold up the testrun.
curl -sS -X PUT --fail-with-body --connect-timeout 10 --max-time 60 \
  -u "$TCACHE_BASIC_AUTH" "$stats_url" \
  -H "Content-Type: application/json" --data-binary "@$stats_json"
