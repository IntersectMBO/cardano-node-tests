#!/bin/bash

if [ -z "${IN_NIX_SHELL:-""}" ]; then
  echo "This script is supposed to be run from nix shell." >&2
  exit 1
fi

if [ -n "${VIRTUAL_ENV:-""}" ]; then
  echo "This script should be run outside of any virtual environment." >&2
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

REPODIR="$(readlink -m "${0%/*}/..")"
cd "$REPODIR" || exit 1

export WORKDIR="$REPODIR/dev_workdir"

rm -rf "${WORKDIR:?}"
mkdir -p "${WORKDIR}/tmp"

# shellcheck disable=SC1091
. "$REPODIR/.github/setup_venv.sh"

cat > "$WORKDIR/.source" <<EoF
if [ -z "\${IN_NIX_SHELL:-}" ]; then
  echo "WARNING: This script is supposed to be sourced from nix shell." >&2
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
export CLUSTER_ERA="${CLUSTER_ERA:-""}"
export COMMAND_ERA="${COMMAND_ERA:-""}"
EoF

# shellcheck disable=SC1091
source "$WORKDIR/.source"
prepare-cluster-scripts -d "$WORKDIR/${CLUSTER_ERA}_fast" -t "${CLUSTER_ERA}_fast"

echo
echo
echo "------------------------"
echo "|    Test Env Ready     |"
echo "------------------------"
echo
echo "To activate it, source the env with:"
echo "source $WORKDIR/.source"
echo
echo "To start local testnet, run:"
echo "$WORKDIR/${CLUSTER_ERA}_fast/start-cluster"
echo
