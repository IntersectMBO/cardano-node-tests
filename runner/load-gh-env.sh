#!/usr/bin/env bash

# Load environment variables from a file and apply command-line overrides, then exec a command.
#
# Usage: load-gh-env.sh ENV_FILE [VAR=VALUE ...] COMMAND [ARGS...]
# - ENV_FILE: GitHub Actions env file (KEY=VALUE or multiline KEY<<DELIMITER).
#   Values are literal; comments and empty lines are ignored.
# - VAR=VALUE: Optional overrides for environment variables defined in the file. Can be multiple.
# - COMMAND [ARGS...]: The command to execute with the loaded environment variables.
#
# Example:
#  runner/load-gh-env.sh runner/env_nightly_dbsync MARKEXPR=dbsync runner/regression.sh

set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=runner/gh-env.sh
source "$script_dir/gh-env.sh"

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 ENV_FILE [VAR=VALUE ...] COMMAND [ARGS...]" >&2
  exit 2
fi

file="$1"
shift

# 1) Collect each variable from the env file
loaded_env=()
collect_gh_env() {
  loaded_env+=("$1=$2")
}
parse_gh_env_file "$file" collect_gh_env

# 2) Apply command-line overrides: VAR=VALUE ... until first non VAR=VALUE
while [[ $# -gt 0 && "$1" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; do
  loaded_env+=("$1")
  shift
done

if [[ "${1:-}" == "--" ]]; then
  shift
fi

# 3) Remaining args are the command to exec
if [[ $# -eq 0 ]]; then
  echo "No command specified to execute." >&2
  exit 1
fi

exec env "${loaded_env[@]}" "$@"
