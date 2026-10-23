#!/usr/bin/env bash
# Shared GitHub Actions environment-file parser. Source this file; do not execute it.

# Call CALLBACK NAME VALUE for each entry, without evaluating file contents.
# Accept comments for compatibility with load-gh-env.sh's existing env files.
parse_gh_env_file() {
  local env_file="$1" callback="$2"
  local line name value delimiter value_line closed
  if [ ! -f "$env_file" ] || [ ! -r "$env_file" ]; then
    echo "Error: environment file '$env_file' is not a readable file." >&2
    return 2
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ "$line" == *=* && "${line%%=*}" != *'<<'* ]]; then
      name="${line%%=*}"
      value="${line#*=}"
    elif [[ "$line" == *'<<'* ]]; then
      name="${line%%<<*}"
      delimiter="${line#*<<}"
      if [ -z "$delimiter" ]; then
        echo "Error: empty multiline delimiter in environment file '$env_file'." >&2
        return 2
      fi
      value=""
      closed=false
      while IFS= read -r value_line || [ -n "$value_line" ]; do
        value_line="${value_line%$'\r'}"
        if [ "$value_line" = "$delimiter" ]; then
          closed=true
          break
        fi
        value+="$value_line"$'\n'
      done
      if [ "$closed" != true ]; then
        echo "Error: missing multiline delimiter '$delimiter' in environment file '$env_file'." >&2
        return 2
      fi
      value="${value%$'\n'}"
    else
      echo "Error: expected NAME=value or NAME<<DELIMITER in environment file '$env_file'." >&2
      return 2
    fi
    if [[ ! "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "Error: invalid environment variable name in '$env_file': '$name'." >&2
      return 2
    fi
    "$callback" "$name" "$value" || return $?
  done < "$env_file"
}
