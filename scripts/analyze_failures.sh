#!/usr/bin/env bash
# Run preliminary failure analysis on a regression-run directory using any
# coding-agent CLI that accepts a prompt on stdin (or via -p/--prompt).
#
# Usage:
#   scripts/analyze_failures.sh [RUN_DIR]
#
# RUN_DIR defaults to ./run_workdir. It can point at a fresh run produced by
# `runner/regression.sh` or at any saved historical run directory (relative
# or absolute path). The agent writes ${RUN_DIR}/failure_analysis.md per the
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

RUN_DIR="${1:-run_workdir}"
AGENT_CMD="${AGENT_CMD:-claude -p}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ ! -d "${RUN_DIR}" ]; then
  echo "Run directory not found: ${RUN_DIR}" >&2
  exit 1
fi

# Auto-detect testsuite from RUN_DIR contents unless PROMPT_FILE is set.
# Upgrade runs produce per-step allure result dirs/tarballs; regression runs
# produce a single allure-results dir/tarball.
if [ -z "${PROMPT_FILE:-}" ]; then
  if compgen -G "${RUN_DIR}/allure-results-step*" > /dev/null; then
    PROMPT_FILE="${REPO_ROOT}/agent_docs/upgrade_failure_analysis_prompt.md"
    echo "Detected node-upgrade run; using upgrade prompt." >&2
  else
    PROMPT_FILE="${REPO_ROOT}/agent_docs/failure_analysis_prompt.md"
    echo "Detected regression run; using regression prompt." >&2
  fi
fi

if [ ! -f "${PROMPT_FILE}" ]; then
  echo "Prompt file not found: ${PROMPT_FILE}" >&2
  exit 1
fi

# Substitute {RUN_DIR} in the prompt template, then pipe to the agent.
prompt_content="$(cat "${PROMPT_FILE}")"
prompt_content="${prompt_content//\{RUN_DIR\}/${RUN_DIR}}"

# shellcheck disable=SC2086
printf '%s\n' "${prompt_content}" | exec $AGENT_CMD
