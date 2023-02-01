#! /usr/bin/env -S nix develop --accept-flake-config .#base -c bash
# shellcheck shell=bash

set -xeuo pipefail

REPODIR="$(readlink -m "${0%/*}/..")"
cd "$REPODIR"

ORIG_WORKDIR="${WORKDIR:-""}"
if [ -z "${WORKDIR:-""}" ]; then
  WORKDIR="$REPODIR/run_workdir"
fi
export WORKDIR

# shellcheck disable=SC1090,SC1091
. .github/stop_cluster_instances.sh

# stop all running cluster instances
stop_instances "$WORKDIR"

# create clean workdir if using the default one
if [ -z "${ORIG_WORKDIR:-""}" ]; then
  rm -rf "${WORKDIR:?}"
fi
mkdir -p "$WORKDIR"

export TMPDIR="$WORKDIR/tmp"
mkdir -p "$TMPDIR"

if [ "${CI_ENABLE_DBSYNC:-"false"}" != "false" ]; then
  # setup dbsync
  # shellcheck disable=SC1090,SC1091
  . .github/source_dbsync.sh
fi

if [ "${CLUSTER_ERA:-""}" = "babbage_pv8" ]; then
  export CLUSTER_ERA="babbage"
  export UPDATE_PV8=1
fi

if [ "${CI_TOPOLOGY:-""}" = "p2p" ]; then
  export ENABLE_P2P="true"
elif [ "${CI_TOPOLOGY:-""}" = "mixed" ]; then
  export MIXED_P2P="true"
fi

export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
rm -rf "${ARTIFACTS_DIR:?}"

export SCHEDULING_LOG=scheduling.log
true > "$SCHEDULING_LOG"

MARKEXPR="${MARKEXPR:-""}"
if [ "${MARKEXPR:-""}" = "all" ]; then
  unset MARKEXPR
fi

if [ -n "${CLUSTERS_COUNT:-""}" ]; then
  export CLUSTERS_COUNT
fi

if [ "${TX_ERA:-""}" == "default" ]; then
  export TX_ERA=""
fi

if [ "${CI_FAST_CLUSTER:-"false"}" != "false" ] && [ -z "${SCRIPTS_DIRNAME:-""}" ]; then
  export SCRIPTS_DIRNAME="${CLUSTER_ERA:-"babbage"}_fast"
fi

export CARDANO_NODE_SOCKET_PATH_CI="$WORKDIR/state-cluster0/bft1.socket"

# function to update cardano-node to specified branch and/or revision, or to the latest available
# shellcheck disable=SC1090,SC1091
. .github/nix_override_cardano_node.sh

echo "::group::Nix env setup"
printf "start: %(%H:%M:%S)T\n" -1

# run tests and generate report
set +e
# shellcheck disable=SC2046,SC2016,SC2119
nix develop --accept-flake-config $(node_override) --command bash -c '
  printf "finish: %(%H:%M:%S)T\n" -1
  echo "::endgroup::"  # end group for "Nix env setup"
  echo "::group::Pytest run"
  export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"
  make "${MAKE_TARGET:-"tests"}"
  retval="$?"
  echo "::endgroup::"
  echo "::group::Collect artifacts"
  ./.github/cli_coverage.sh .
  exit "$retval"
'
retval="$?"

# move reports to root dir
mv .reports/testrun-report.* ./

# grep testing artifacts for errors
# shellcheck disable=SC1090,SC1091
. .github/grep_errors.sh

# stop all running cluster instances
stop_instances "$WORKDIR"

# stop postgres if running
stop_postgres || true

# prepare artifacts for upload in Github Actions
if [ -n "${GITHUB_ACTIONS:-""}" ]; then
  # create results archive
  ./.github/results.sh .

  # save testing artifacts
  # shellcheck disable=SC1090,SC1091
  . .github/save_artifacts.sh

  # compress scheduling log
  xz "$SCHEDULING_LOG"

  echo
  echo "Dir content:"
  ls -1a
fi

echo "::endgroup::" # end group for "Collect artifacts"

exit "$retval"
