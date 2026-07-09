#!/usr/bin/env bash

set -euo pipefail

if [ -n "${VIRTUAL_ENV:-}" ]; then
  echo "This script should be run outside of any virtual environment." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "This script requires 'uv' to be installed and available in PATH." >&2
  exit 1
fi

env_name="Unknown"
case "${1:-}" in
  "conway")
    COMMAND_ERA=conway
    PROTOCOL_VERSION=11
    env_name="Conway PV11"
    ;;
  *)
    echo "Usage: $0 {conway}"
    exit 2
    ;;
esac

orig_pwd="$PWD"
repodir="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine repo dir, exiting." >&2; exit 1; }
export WORKDIR="$repodir/dev_workdir"

if [ "$orig_pwd" = "$WORKDIR" ]; then
  echo "Please run this script from outside of '$WORKDIR'" >&2
  exit 1
fi

cd "$repodir" || exit 1
rm -rf "${WORKDIR:?}"
mkdir -p "${WORKDIR}/tmp"

# shellcheck disable=SC1091
. "$repodir/runner/setup_venv.sh"

cat > "$WORKDIR/activate" <<EoF
if ! command -v cardano-node >/dev/null 2>&1; then
  echo "WARNING: 'cardano-node' is not in PATH." >&2
fi
if ! command -v cardano-cli >/dev/null 2>&1; then
  echo "WARNING: 'cardano-cli' is not in PATH." >&2
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "WARNING: 'jq' is not in PATH." >&2
fi
if [ -z "\${VIRTUAL_ENV:-}" ]; then
  source "$VIRTUAL_ENV/bin/activate"
elif [ "\${VIRTUAL_ENV:-}" != "$VIRTUAL_ENV" ]; then
  echo "ERROR: A different virtual environment is already activated." >&2
  return 1
fi
if [[ :"\${PYTHONPATH:-}": == */nix/store/* ]]; then
  PYTHONPATH="\$(echo "\${PYTHONPATH:-}" | tr ":" "\n" | grep -v "/nix/store/.*/site-packages" | tr "\n" ":" | sed 's/:*$//' || :)"
fi
if [ -n "\${PYTHONPATH:-}" ]; then
  export PYTHONPATH
else
  unset PYTHONPATH
fi
export CARDANO_NODE_SOCKET_PATH="$WORKDIR/state-cluster0/bft1.socket"
export TMPDIR="$WORKDIR/tmp"
export DEV_CLUSTER_RUNNING=true
export FORBID_RESTART=true
export NO_ARTIFACTS=true
export CLUSTERS_COUNT=1
export COMMAND_ERA="${COMMAND_ERA:-}"
export PROTOCOL_VERSION="${PROTOCOL_VERSION:-}"
echo "Activated test environment for '$env_name'"
EoF

# shellcheck disable=SC1091
source "$WORKDIR/activate"
prepare-cluster-scripts -d "$WORKDIR/local_fast" -t "local_fast"

# Compute a relative path only if WORKDIR is under orig_pwd
if [[ "$WORKDIR" == "$orig_pwd"* ]]; then
    rel_workdir="./${WORKDIR#"$orig_pwd"/}"
else
    rel_workdir="$WORKDIR"
fi

echo
echo "========================================"
echo "      🚀  Test Environment Ready"
echo "========================================"
echo
echo "👉  Activate the environment:"
echo "    source $rel_workdir/activate"
echo
echo "👉  Start the local testnet:"
echo "    $rel_workdir/local_fast/start-cluster"
echo
