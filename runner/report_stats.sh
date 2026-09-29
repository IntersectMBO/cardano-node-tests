#!/usr/bin/env bash

# Upload testrun statistics to the tcache.
#
# Best effort, like the other tcache calls in the workflows: when the secrets
# are not set, or the upload fails, the testrun result is unaffected. Call it
# with `|| :` so a failure here can never mask the pytest exit code.

set -uo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 4 ]; then
  echo "Usage: $0 <allure_results_dir> <exit_code> [cli_coverage_json] [step]" >&2
  exit 1
fi

results_dir="$1"
exit_code="$2"
coverage_json="${3:-}"
step="${4:-main}"

if [ -z "${TCACHE_BASIC_AUTH:-}" ] || [ -z "${TCACHE_URL:-}" ]; then
  echo "TCACHE_BASIC_AUTH or TCACHE_URL is not set, not reporting stats."
  exit 0
fi

if [ ! -d "$results_dir" ]; then
  echo "No Allure results in '$results_dir', not reporting stats." >&2
  exit 1
fi

tests_repo="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine test repo dir, exiting." >&2; exit 1; }

# `TCACHE_URL` points at the `/results` prefix, because that is the only
# endpoint it was created for. The stats endpoint sits at its own top-level
# prefix, so the suffix is stripped and replaced. Same derivation the nightly
# `/history` upload uses.
root="${TCACHE_URL%/}"
stats_url="${root%/results}/stats"

stats_json="$(mktemp)" || exit 1
# shellcheck disable=SC2064
trap "rm -f '$stats_json'" EXIT

extra_args=()
if [ -n "$coverage_json" ] && [ -e "$coverage_json" ]; then
  extra_args+=(--cli-coverage "$coverage_json")
fi
# A run restricted by a mark expression covered only part of the suite, so its
# counts are not comparable with a full run. The tcache keeps the flag so an
# aggregate can leave such a run out.
if [ -n "${MARKEXPR:-}" ]; then
  extra_args+=(--filtered)
fi

if ! "${tests_repo}/scripts/stats_json.py" "$results_dir" \
       --exit-code "$exit_code" --step "$step" "${extra_args[@]}" \
       --output "$stats_json"; then
  echo "Could not build the stats document, not reporting stats." >&2
  exit 1
fi

echo "Reporting testrun stats to $stats_url"
curl -s -X PUT --fail-with-body -u "$TCACHE_BASIC_AUTH" "$stats_url" \
  -H "Content-Type: application/json" --data-binary "@$stats_json"
