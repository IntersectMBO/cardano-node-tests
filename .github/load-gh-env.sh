#!/usr/bin/env bash

# Load environment variables from a file and apply command-line overrides, then exec a command.
#
# Usage: load-gh-env.sh ENV_FILE [VAR=VALUE ...] COMMAND [ARGS...]
# - ENV_FILE: Path to a file containing environment variable definitions (KEY=VALUE).
# - VAR=VALUE: Optional overrides for environment variables defined in the file. Can be multiple.
# - COMMAND [ARGS...]: The command to execute with the loaded environment variables.
#
# Example:
#  .github/load-gh-env.sh .github/env_nightly_dbsync MARKEXPR=dbsync .github/regression.sh

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 ENV_FILE [VAR=VALUE ...] COMMAND [ARGS...]" >&2
  exit 2
fi

file="$1"
shift

if [[ ! -f "$file" ]]; then
  echo "Environment file not found: $file" >&2
  exit 1
fi

# 1) Read and export each variable from the env file
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ $line =~ ^#.*$ ]] && continue # Skip comments
  [[ -z $line ]] && continue       # Skip empty lines

  key=${line%%=*}
  value=${line#*=}
  if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "Invalid environment variable name in $file: '$key'" >&2
    continue
  fi
  export "$key"="$value"
done < "$file"

# 2) Apply command-line overrides: VAR=VALUE ... until first non VAR=VALUE
while [[ $# -gt 0 && "$1" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; do
  override="$1"
  shift

  key="${override%%=*}"
  value="${override#*=}"
  export "$key"="$value"
done

# 3) Remaining args are the command to exec
if [[ $# -eq 0 ]]; then
  echo "No command specified to execute." >&2
  exit 1
fi

exec "$@"
