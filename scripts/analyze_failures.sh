#!/usr/bin/env bash
# Run preliminary failure analysis on a regression-run directory using any
# coding-agent CLI that accepts a prompt on stdin (or via -p/--prompt).
#
# Usage:
#   scripts/analyze_failures.sh [run_dir]
#
# run_dir defaults to ./run_workdir. It can point at a fresh run produced by
# `runner/regression.sh` or at any saved historical run directory (relative
# or absolute path). The agent writes ${run_dir}/failure_analysis.md per the
# prompt contract.
#
# Env vars:
#   AGENT_CMD     Command used to invoke the agent. Default: "claude -p".
#                 Examples:
#                   AGENT_CMD="claude -p"
#                   AGENT_CMD="gemini"
#                   AGENT_CMD="codex exec"
#                 The prompt is piped to the command on stdin.
#   PROMPT_FILE   Override prompt auto-detection. Path to a prompt template
#                 containing {RUN_DIR} placeholders.

set -euo pipefail

run_dir="${1:-run_workdir}"
AGENT_CMD="${AGENT_CMD:-claude -p}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

if [ ! -d "${run_dir}" ]; then
  echo "Run directory not found: ${run_dir}" >&2
  exit 1
fi

# Auto-detect testsuite from run_dir contents unless PROMPT_FILE is set.
# Upgrade runs produce per-step allure result dirs/tarballs; regression runs
# produce a single allure-results dir/tarball.
if [ -z "${PROMPT_FILE:-}" ]; then
  if compgen -G "${run_dir}/allure-results-step*" > /dev/null; then
    PROMPT_FILE="${repo_root}/agent_docs/upgrade_failure_analysis_prompt.md"
    echo "Detected node-upgrade run; using upgrade prompt." >&2
  else
    PROMPT_FILE="${repo_root}/agent_docs/failure_analysis_prompt.md"
    echo "Detected regression run; using regression prompt." >&2
  fi
fi

if [ ! -f "${PROMPT_FILE}" ]; then
  echo "Prompt file not found: ${PROMPT_FILE}" >&2
  exit 1
fi

# Substitute {RUN_DIR} in the prompt template, then pipe to the agent.
prompt_content="$(cat "${PROMPT_FILE}")"
prompt_content="${prompt_content//\{RUN_DIR\}/${run_dir}}"

# shellcheck disable=SC2086
printf '%s\n' "${prompt_content}" | exec $AGENT_CMD
