#!/usr/bin/env bash
# Run preliminary failure analysis on a regression-run directory using any
# coding-agent CLI that accepts a prompt as its first positional argument.
#
# Usage:
#   scripts/analyze_failures.sh [run_dir]
#
# run_dir defaults to ./run_workdir. It can point at a fresh run produced by
# `runner/regression.sh` or at any saved historical run directory (relative
# or absolute path).
#
# By default the agent is started in interactive mode, seeded with the
# analysis prompt, so you can read the findings and ask follow-up questions.
# The CI-only constraints (write failure_analysis.md, length cap, "Start now.")
# are intentionally NOT included here; those live in agent_docs/ci_analysis_prompt.md
# and are appended only by the CI workflows.
#
# Env vars:
#   AGENT_CMD     Command used to invoke the agent. Default: "claude".
#                 The prompt is passed as the last positional argument.
#                 Examples:
#                   AGENT_CMD="claude"              # interactive (default)
#                   AGENT_CMD="claude -p"           # non-interactive print mode
#                   AGENT_CMD="claude --model opus" # interactive, model override
#                   AGENT_CMD="gemini"
#                   AGENT_CMD="codex"
#   PROMPT_FILE   Override prompt auto-detection. Path to a prompt template
#                 containing {RUN_DIR} placeholders.

set -euo pipefail

run_dir="${1:-run_workdir}"
AGENT_CMD="${AGENT_CMD:-claude}"

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

# Substitute {RUN_DIR} in the prompt template, then seed the agent with it.
prompt_content="$(cat "${PROMPT_FILE}")"
prompt_content="${prompt_content//\{RUN_DIR\}/${run_dir}}"

# Pass the prompt as the last positional argument so interactive agents start
# seeded with it (stdin piping would put claude into non-interactive mode).
# shellcheck disable=SC2086
exec $AGENT_CMD "${prompt_content}"
