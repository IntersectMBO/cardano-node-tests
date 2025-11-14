#!/usr/bin/env bash

if [ -n "${VIRTUAL_ENV:-""}" ]; then
  echo "This script should be run outside of any virtual environment." >&2
  exit 1
fi

if ! command -v poetry >/dev/null 2>&1; then
  echo "This script requires 'poetry' to be installed and available in PATH." >&2
  exit 1
fi

case "${1:-""}" in
  "conway")
    CLUSTER_ERA=conway
    COMMAND_ERA=conway
    ;;
  *)
    echo "Usage: $0 {conway}"
    exit 1
    ;;
esac

ORIG_PWD="$PWD"
REPODIR="$(readlink -m "${0%/*}/..")"
export WORKDIR="$REPODIR/dev_workdir"

if [ "$ORIG_PWD" = "$WORKDIR" ]; then
  echo "Please run this script from outside of '$WORKDIR'" >&2
  exit 1
fi

cd "$REPODIR" || exit 1
rm -rf "${WORKDIR:?}"
mkdir -p "${WORKDIR}/tmp"

# shellcheck disable=SC1091
. "$REPODIR/.github/setup_venv.sh"

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
PYTHONPATH="\$(echo "\${PYTHONPATH:-}" | tr ":" "\n" | grep -v "/nix/store/.*/site-packages" | tr "\n" ":" | sed 's/:*$//' || :)"
if [ -n "\${PYTHONPATH:-}" ]; then
  export PYTHONPATH
else
  unset PYTHONPATH
fi
export CARDANO_NODE_SOCKET_PATH="$WORKDIR/state-cluster0/bft1.socket"
export TMPDIR="$WORKDIR/tmp"
export DEV_CLUSTER_RUNNING=1
export CLUSTERS_COUNT=1
export FORBID_RESTART=1
export NO_ARTIFACTS=1
export CLUSTER_ERA="${CLUSTER_ERA:-}"
export COMMAND_ERA="${COMMAND_ERA:-}"
EoF

# shellcheck disable=SC1091
source "$WORKDIR/activate"
prepare-cluster-scripts -d "$WORKDIR/${CLUSTER_ERA}_fast" -t "${CLUSTER_ERA}_fast"

# Compute a relative path only if WORKDIR is under ORIG_PWD
if [[ "$WORKDIR" == "$ORIG_PWD"* ]]; then
    REL_WORKDIR="./${WORKDIR#"$ORIG_PWD"/}"
else
    REL_WORKDIR="$WORKDIR"
fi

echo
echo "========================================"
echo "      🚀  Test Environment Ready"
echo "========================================"
echo
echo "👉  Activate the environment:"
echo "    source $REL_WORKDIR/activate"
echo
echo "👉  Start the local testnet:"
echo "    $REL_WORKDIR/${CLUSTER_ERA}_fast/start-cluster"
echo
