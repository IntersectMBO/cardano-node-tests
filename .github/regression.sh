#! /usr/bin/env -S nix develop --accept-flake-config .#base -c bash
# shellcheck shell=bash

set -xeuo pipefail

REPODIR="$PWD"

WORKDIR="$REPODIR/run_workdir"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

export TMPDIR="$WORKDIR/tmp"
mkdir -p "$TMPDIR"

if [ "${CI_ENABLE_DBSYNC:-"false"}" != "false" ]; then
  # setup dbsync
  # shellcheck disable=SC1090,SC1091
  . "$REPODIR/.github/source_dbsync.sh"
fi

if [ "${CLUSTER_ERA:-""}" = "babbage_pv8" ]; then
  export CLUSTER_ERA="babbage"
  export UPDATE_PV8=1
fi

echo "::group::Nix env setup"

# function to update cardano-node to specified branch and/or revision, or to the latest available
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/nix_override_cardano_node.sh"

if [ "${CI_TOPOLOGY:-""}" = "p2p" ]; then
  export ENABLE_P2P="true"
elif [ "${CI_TOPOLOGY:-""}" = "mixed" ]; then
  export MIXED_P2P="true"
fi

export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"

export SCHEDULING_LOG=scheduling.log

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

# run tests and generate report
rm -rf "${ARTIFACTS_DIR:?}"/*
set +e
# shellcheck disable=SC2046,SC2016,SC2119
nix develop --accept-flake-config $(node_override) --command bash -c '
  echo "::endgroup::"  # end group for "Nix env setup"
  echo "::group::Pytest run"
  export CARDANO_NODE_SOCKET_PATH="$CARDANO_NODE_SOCKET_PATH_CI"
  make tests
  retval="$?"
  echo "::endgroup::"
  echo "::group::Collect artifacts"
  ./.buildkite/cli_coverage.sh .
  exit "$retval"
'
retval="$?"

# move reports to root dir
mv .reports/testrun-report.* ./

# create results archive
"$REPODIR"/.buildkite/results.sh .

# grep testing artifacts for errors
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/grep_errors.sh"

# save testing artifacts
# shellcheck disable=SC1090,SC1091
. "$REPODIR/.buildkite/save_artifacts.sh"

# compress scheduling log
xz "$SCHEDULING_LOG"

echo
echo "Dir content:"
ls -1a

echo "::endgroup::" # end group for "Collect artifacts"

exit "$retval"
