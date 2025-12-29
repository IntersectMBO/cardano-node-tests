#! /usr/bin/env -S nix develop --accept-flake-config .#base -c bash
# shellcheck shell=bash

# controlling environment variables:
# BASE_TAR_URL - URL of a tarball with binaries for base revision
# BASE_REVISION - revision of cardano-node to upgrade from (alternative to BASE_TAR_URL)
# UPGRADE_REVISION - revision of cardano-node to upgrade to
# UPGRADE_CLI_REVISION - revision of cardano-cli to upgrade to (optional)

set -Eeuo pipefail
trap 'echo "Error at line $LINENO"' ERR

if [[ -z "${BASE_TAR_URL:-}" && -z "${BASE_REVISION:-}" ]]; then
  echo "BASE_TAR_URL or BASE_REVISION must be set"
  exit 1
fi

if [ -n "${GITHUB_ACTIONS:-}" ]; then
  CI_OPTIMIZE_SPACE="${CI_OPTIMIZE_SPACE:-"true"}"
fi

nix --version
df -h .

retval=0

REPODIR="$(readlink -m "${0%/*}/..")"
export REPODIR
cd "$REPODIR"

export WORKDIR="$REPODIR/run_workdir"

# shellcheck disable=SC1090,SC1091
. .github/stop_cluster_instances.sh

_cleanup() {
  # stop all running cluster instances
  stop_instances "$WORKDIR" || :
}

_cleanup

# cleanup on Ctrl+C
trap '_cleanup; exit 130' SIGINT
# cleanup on error
trap 'echo "Error at line $LINENO"; _cleanup' ERR

# create clean workdir
rm -rf "${WORKDIR:?}"
mkdir -p "$WORKDIR"

export TMPDIR="$WORKDIR/tmp"
mkdir -p "$TMPDIR"

export CARDANO_NODE_SOCKET_PATH_CI="$WORKDIR/state-cluster0/bft1.socket"

export ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
rm -rf "${ARTIFACTS_DIR:?}"
mkdir -p "$ARTIFACTS_DIR"

export COVERAGE_DIR="${COVERAGE_DIR:-".cli_coverage"}"
rm -rf "${COVERAGE_DIR:?}"
mkdir -p "$COVERAGE_DIR"

export SCHEDULING_LOG=scheduling.log
: > "$SCHEDULING_LOG"

export DEV_CLUSTER_RUNNING=1 CLUSTERS_COUNT=1 FORBID_RESTART=1 TEST_THREADS=10 NUM_POOLS="${NUM_POOLS:-4}"
unset ENABLE_LEGACY MIXED_P2P

echo "::endgroup::"  # end group for "Script setup"

echo "::group::Nix env setup step1"
printf "start: %(%H:%M:%S)T\n" -1

# shellcheck disable=SC1090,SC1091
. .github/source_cardano_node.sh

# Prepare cardano-node for the base revision.
# If BASE_TAR_URL is set, instead of using nix, download and extract binaries for base revision
# from a published tarball.
PATH_PREPEND_BASE=""
if [ -z "${BASE_TAR_URL:-}" ]; then
  if [ -z "${BASE_REVISION:-}" ]; then
    echo "Either BASE_TAR_URL or BASE_REVISION must be set"
    exit 1
  fi
  cardano_bins_build_all "$BASE_REVISION" "" "_base"
  PATH_PREPEND_BASE="$(cardano_bins_print_path_prepend "" "_base")"
fi
export PATH_PREPEND_BASE

# Prepare cardano-node for the upgrade revision
cardano_bins_build_all "${UPGRADE_REVISION:-"master"}" "${UPGRADE_CLI_REVISION:-}" "_upgrade"
PATH_PREPEND_UPGRADE="$(cardano_bins_print_path_prepend "${UPGRADE_CLI_REVISION:-}" "_upgrade")"
export PATH_PREPEND_UPGRADE

# Prepare cardano-cli for the upgrade revision if UPGRADE_CLI_REVISION is set
if [ -n "${UPGRADE_CLI_REVISION:-}" ]; then
  export CARDANO_CLI_REV="$UPGRADE_CLI_REVISION"
  # shellcheck disable=SC1090,SC1091
  . .github/source_cardano_cli.sh
  cardano_cli_build "$UPGRADE_CLI_REVISION" "_upgrade"
  PATH_PREPEND_UPGRADE="$(cardano_cli_print_path_prepend "_upgrade")${PATH_PREPEND_UPGRADE}"
  export PATH_PREPEND_UPGRADE
fi

# optimize nix store space if requested
if [ "${CI_OPTIMIZE_SPACE:-"false"}" != "false" ]; then
  nix store gc || :
fi

# shellcheck disable=SC2016
nix develop --accept-flake-config .#testenv --command bash -c '
  set -euo pipefail
  : > "$WORKDIR/.nix_setup"
  echo "::endgroup::"  # end group for "Nix env setup"

  echo "::group::Python venv setup step1"
  printf "start: %(%H:%M:%S)T\n" -1
  . .github/setup_venv.sh clean
  export PATH="${PATH_PREPEND_BASE}${PATH}"
  echo "::endgroup::"  # end group for "Python venv setup step1"

  echo "::group::🧪 Testrun Step1"
  printf "start: %(%H:%M:%S)T\n" -1
  df -h .
  # prepare scripts for stating cluster instance, start cluster instance, run smoke tests
  retval=0
  ./.github/node_upgrade_pytest.sh step1 || retval="$?"
  # retval 0 == all tests passed; 1 == some tests failed; > 1 == some runtime error and we do not want to continue
  [ "$retval" -le 1 ] || exit "$retval"
  echo "::endgroup::"  # end group for "Testrun Step1"

  echo "::group::Python venv setup steps 2 & 3"
  printf "start: %(%H:%M:%S)T\n" -1
  . .github/setup_venv.sh clean
  export PATH="${PATH_PREPEND_UPGRADE}${PATH}"
  echo "::endgroup::"  # end group for "Python venv setup steps 2 & 3"

  echo "::group::🧪 Testrun Step2"
  printf "start: %(%H:%M:%S)T\n" -1
  df -h .
  # update cluster nodes, run smoke tests
  retval=0
  ./.github/node_upgrade_pytest.sh step2 || retval="$?"
  [ "$retval" -le 1 ] || exit "$retval"
  echo "::endgroup::"  # end group for "Testrun Step2"

  echo "::group::🧪 Testrun Step3"
  printf "start: %(%H:%M:%S)T\n" -1
  df -h .
  # update the rest of cluster nodes, run smoke tests
  retval=0
  ./.github/node_upgrade_pytest.sh step3 || retval="$?"
  echo "::endgroup::"  # end group for "Testrun Step3"

  echo "::group::Teardown cluster & collect artifacts"
  printf "start: %(%H:%M:%S)T\n" -1
  df -h .
  ./.github/node_upgrade_pytest.sh finish || :
  exit $retval
' || retval="$?"

if [ ! -e "$WORKDIR/.nix_setup" ]; then
  echo "Nix env setup failed, exiting"
  exit 1
fi

# grep testing artifacts for errors
./.github/grep_errors.sh

_cleanup

# prepare artifacts for upload in GitHub Actions
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  # save testing artifacts
  ./.github/save_artifacts.sh

  # compress scheduling log
  xz "$SCHEDULING_LOG"
fi

exit "$retval"
