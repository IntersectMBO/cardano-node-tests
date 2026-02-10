#!/usr/bin/env bash

# AI command execution guard.
#
# This script is intentionally restrictive: it allows only a small,
# explicitly approved set of commands to be executed by an AI agent.
# Any command not matching the allowlist is rejected.
#
# Commands executed through this script are considered safe to run
# outside of sandbox and without human supervision.

set -euo pipefail

cmd=${1-}

case "$cmd" in
  make)
    subcmd=${2-}
    case "$subcmd" in
      update-lockfile | lint)
        # shellcheck disable=SC1091
        source .source || source .source.dev
        ;;
      *)
        echo "make subcommand not allowed: $subcmd" >&2
        exit 2
        ;;
    esac
    ;;
  pytest)
    # shellcheck disable=SC1091
    source .source || source .source.dev

    if ! [ -S "${CARDANO_NODE_SOCKET_PATH:-}" ]; then
      echo "The local testnet cluster is not running." >&2
      exit 1
    fi
    if ! command -v cardano-node >/dev/null; then
      echo "The cardano-node binary is not available." >&2
      exit 1
    fi
    if ! command -v cardano-cli >/dev/null; then
      echo "The cardano-cli binary is not available." >&2
      exit 1
    fi
    ;;
  *)
    echo "Command not allowed: $*" >&2
    exit 2
    ;;
esac

exec "$@"
